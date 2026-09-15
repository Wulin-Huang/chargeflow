import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.profiles import generate_profiles
from app.ai.report import generate_daily_report
from app.core.loadbalancer import balancer
from app.db import get_db, utcnow
from app.gateway.broadcaster import broadcaster
from app.gateway.telemetry import telemetry_buffer
from app.models import BillingOrder, ChargingSession, DailyReport, Pile, PricePolicy, Reservation, Station, User, VehicleProfile, WorkOrder
from app.schemas import PricePolicyReq
from app.security import require_role

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats")
async def stats(user: User = Depends(require_role("operator", "admin")), db: AsyncSession = Depends(get_db)):
    today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    sessions_today = (await db.execute(select(func.count(ChargingSession.id)).where(ChargingSession.start_at >= today_start))).scalar()
    energy_wh = (await db.execute(select(func.sum(ChargingSession.kwh_wh)).where(ChargingSession.start_at >= today_start))).scalar() or 0
    revenue_cents = (await db.execute(select(func.sum(BillingOrder.total_cents)).where(BillingOrder.created_at >= today_start))).scalar() or 0
    open_wos = (await db.execute(select(func.count(WorkOrder.id)).where(WorkOrder.status == "open"))).scalar()
    piles = (await db.execute(select(Pile))).scalars().all()
    by_status: dict[str, int] = {}
    for p in piles:
        by_status[p.status] = by_status.get(p.status, 0) + 1
    return {
        "sessions_today": sessions_today,
        "energy_kwh_today": round(energy_wh / 1000, 1),
        "revenue_yuan_today": round(revenue_cents / 100, 2),
        "open_work_orders": open_wos,
        "pile_status": by_status,
        "ws_connections": broadcaster.connection_count,
        "telemetry_pending": telemetry_buffer.stats["pending"],
    }


@router.get("/pile-health")
async def pile_health(user: User = Depends(require_role("operator", "admin")), db: AsyncSession = Depends(get_db)):
    """桩健康度评分（PHM 预测性维护）：工单压力 + 运行状态 + 实时枪温加权可解释模型。"""
    from app.bus import get_bus
    from datetime import timedelta

    week_ago = utcnow() - timedelta(days=7)
    rows = (
        await db.execute(
            select(Pile, Station.name)
            .join(Station, Station.id == Pile.station_id)
            .order_by(Pile.id)
        )
    ).all()
    open_wos: dict[int, int] = {}
    recent_wos: dict[int, int] = {}
    for pid, status in (
        await db.execute(select(WorkOrder.pile_id, WorkOrder.status).where(WorkOrder.created_at >= week_ago))
    ).all():
        recent_wos[pid] = recent_wos.get(pid, 0) + 1
        if status == "open":
            open_wos[pid] = open_wos.get(pid, 0) + 1

    bus = await get_bus()
    out = []
    for pile, station_name in rows:
        score = 100.0
        reasons: list[str] = []
        o = open_wos.get(pile.id, 0)
        r = recent_wos.get(pile.id, 0)
        if o:
            score -= 25 * o
            reasons.append(f"{o} 个未结工单(-{25 * o})")
        if r > o:
            score -= 8 * (r - o)
            reasons.append(f"近7天 {r - o} 个已结工单(-{8 * (r - o)})")
        if pile.status == "offline":
            score -= 20
            reasons.append("当前离线(-20)")
        elif pile.status == "fault":
            score -= 30
            reasons.append("故障停机(-30)")
        raw = await bus.get(f"pile:{pile.id}:latest")
        if raw:
            d = json.loads(raw)
            gun_temp = float(d.get("gun_temp", 25))
            if gun_temp > 45:
                score -= 15
                reasons.append(f"枪温 {gun_temp:.1f}℃ 偏高(-15)")
        score = max(5, min(100, score))
        grade = "优" if score >= 85 else "良" if score >= 70 else "关注" if score >= 50 else "维护"
        suggestion = (
            "立即派单检修" if score < 50 else "列入本周巡检" if score < 70 else "持续观察" if score < 85 else "健康"
        )
        out.append(
            {
                "pile_id": pile.id,
                "code": pile.code,
                "station": station_name,
                "score": round(score),
                "grade": grade,
                "reasons": reasons or ["无扣分项"],
                "suggestion": suggestion,
                "limit_kw": balancer.status_of(pile.id),
            }
        )
    out.sort(key=lambda x: x["score"])
    return {"total": len(out), "piles": out[:12]}


@router.get("/work-orders")
async def work_orders(user: User = Depends(require_role("operator", "admin")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(WorkOrder).order_by(WorkOrder.id.desc()).limit(50))).scalars().all()
    return [
        {
            "id": w.id, "pile_id": w.pile_id, "type": w.type, "severity": w.severity, "status": w.status,
            "trigger_rule": w.trigger_rule, "ai_diagnosis": w.ai_diagnosis, "resolution": w.resolution,
            "created_at": w.created_at.isoformat(), "closed_at": w.closed_at.isoformat() if w.closed_at else None,
        }
        for w in rows
    ]


@router.post("/work-orders/{wo_id}/close")
async def close_wo(wo_id: int, resolution: str = "", user: User = Depends(require_role("operator", "admin")), db: AsyncSession = Depends(get_db)):
    wo = await db.get(WorkOrder, wo_id)
    if wo is None:
        raise HTTPException(404, "工单不存在")
    wo.status = "closed"
    wo.resolution = resolution
    wo.closed_at = utcnow()
    pile = await db.get(Pile, wo.pile_id)
    if pile and pile.status == "fault":
        pile.status = "idle"
    await db.commit()
    return {"ok": True}


@router.post("/price-policies")
async def upsert_policy(req: PricePolicyReq, user: User = Depends(require_role("admin")), db: AsyncSession = Depends(get_db)):
    db.add(
        PricePolicy(
            station_id=req.station_id,
            periods=req.periods,
            service_fee_cents_per_kwh=req.service_fee_cents_per_kwh,
            free_occupy_minutes=req.free_occupy_minutes,
            occupy_fee_cents_per_min=req.occupy_fee_cents_per_min,
            effective_from=utcnow(),
        )
    )
    await db.commit()
    return {"ok": True}


@router.get("/price-policies/{station_id}")
async def get_policy(station_id: int, user: User = Depends(require_role("operator", "admin")), db: AsyncSession = Depends(get_db)):
    p = (
        await db.execute(select(PricePolicy).where(PricePolicy.station_id == station_id).order_by(PricePolicy.id.desc()).limit(1))
    ).scalar()
    if p is None:
        raise HTTPException(404, "暂无策略")
    return {
        "periods": p.periods,
        "service_fee_cents_per_kwh": p.service_fee_cents_per_kwh,
        "free_occupy_minutes": p.free_occupy_minutes,
        "occupy_fee_cents_per_min": p.occupy_fee_cents_per_min,
    }


@router.post("/ai/generate-profiles")
async def gen_profiles(user: User = Depends(require_role("operator", "admin"))):
    try:
        return await generate_profiles(12)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"生成失败（AI 层降级不影响核心链路）：{e}")


@router.get("/vehicle-profiles")
async def vehicle_profiles(user: User = Depends(require_role("operator", "admin")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(VehicleProfile))).scalars().all()
    return [
        {"id": v.id, "model_name": v.model_name, "battery_kwh": v.battery_kwh, "soc_curve": v.soc_curve, "anomaly_profile": v.anomaly_profile}
        for v in rows
    ]


@router.post("/daily-report")
async def daily_report(user: User = Depends(require_role("operator", "admin"))):
    return await generate_daily_report()


@router.get("/daily-reports")
async def daily_reports(user: User = Depends(require_role("operator", "admin")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(DailyReport).order_by(DailyReport.id.desc()).limit(7))).scalars().all()
    return [{"date": r.report_date, "stats": r.stats, "narrative": r.narrative} for r in rows]
