import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import get_bus
from app.db import get_db
from app.gateway.mqtt_gateway import gateway
from app.models import BillingOrder, ChargingSession, Pile, PricePolicy, Reservation, User
from app.schemas import SessionOut, SetTargetReq, StartChargingReq
from app.security import current_user

logger = logging.getLogger("chargeflow.sessions")

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/start", response_model=SessionOut)
async def start_charging(
    req: StartChargingReq,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    # 幂等：同 request_id 直接返回既有会话，不重发指令
    existing = (await db.execute(select(ChargingSession).where(ChargingSession.request_id == req.request_id))).scalar()
    if existing:
        return _out(existing)

    pile = await db.get(Pile, req.pile_id)
    if pile is None:
        raise HTTPException(404, "桩不存在")
    if pile.status == "offline":
        raise HTTPException(409, "桩离线")
    if pile.status not in ("idle", "reserved"):
        raise HTTPException(409, f"桩当前状态 {pile.status}，不可启动")
    # 余额风控：欠费（负余额）或零余额拒绝启动，先充值再充电
    if user.balance_cents <= 0:
        raise HTTPException(
            402,
            f"余额不足（{user.balance_cents / 100:.2f} 元），请先在充电页充值后再启动",
        )
    res: Reservation | None = None
    if pile.status == "reserved":
        res = (
            await db.execute(
                select(Reservation).where(
                    Reservation.pile_id == pile.id,
                    Reservation.status.in_(("pending", "active")),
                )
            )
        ).scalar()
        if res is None or res.user_id != user.id:
            raise HTTPException(403, "该桩已被他人预约锁定")

    # 电价快照：契约成立时锁定，调价不影响进行中的会话
    policy = (
        await db.execute(select(PricePolicy).where(PricePolicy.station_id == pile.station_id).order_by(PricePolicy.id.desc()).limit(1))
    ).scalar()
    snapshot = {
        "periods": policy.periods if policy else [],
        "service_fee_cents_per_kwh": policy.service_fee_cents_per_kwh if policy else 50,
        "free_occupy_minutes": policy.free_occupy_minutes if policy else 30,
        "occupy_fee_cents_per_min": policy.occupy_fee_cents_per_min if policy else 50,
    }

    session = ChargingSession(user_id=user.id, pile_id=pile.id, request_id=req.request_id, status="starting", snapshot=snapshot)
    db.add(session)
    pile.status = "starting"
    await db.commit()
    await db.refresh(session)

    if res is not None and res.status == "pending":
        res.status = "fulfilled"

    try:
        await gateway.publish_cmd(session.id, req.request_id, pile.id, "start")
    except Exception as e:  # noqa: BLE001 网关不可达 → 回滚
        session.status = "failed"
        session.stop_reason = "gateway_unavailable"
        pile.status = "idle"
        await db.commit()
        raise HTTPException(503, f"指令下发失败：{e}")
    await db.commit()
    return _out(session)


@router.post("/{session_id}/stop")
async def stop_charging(
    session_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(ChargingSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    if session.status not in ("charging", "paused"):
        raise HTTPException(409, f"会话状态 {session.status}，不可停止")
    request_id = f"stop-{uuid.uuid4().hex[:16]}"
    await gateway.request_stop(session_id, request_id, session.pile_id)
    return {"ok": True, "session_id": session_id, "request_id": request_id}


@router.get("/active", response_model=SessionOut | None)
async def active_session(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = (
        await db.execute(
            select(ChargingSession)
            .where(ChargingSession.user_id == user.id, ChargingSession.status.in_(("starting", "charging", "paused", "occupied")))
            .order_by(ChargingSession.id.desc())
            .limit(1)
        )
    ).scalar()
    return _out(s) if s else None


@router.get("/history", response_model=list[SessionOut])
async def history(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(ChargingSession).where(ChargingSession.user_id == user.id).order_by(ChargingSession.id.desc()).limit(20)
        )
    ).scalars().all()
    return [_out(s) for s in rows]


@router.get("/{session_id}/bill")
async def bill_detail(session_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    session = await db.get(ChargingSession, session_id)
    if session is None or (session.user_id != user.id and user.role == "customer"):
        raise HTTPException(404, "会话不存在")
    order = (await db.execute(select(BillingOrder).where(BillingOrder.session_id == session_id))).scalar()
    if order is None:
        # 未结算：给实时预估（当前时段电价 × 累积电量）
        latest = await get_bus().get(f"pile:{session.pile_id}:latest")
        cum_wh = json.loads(latest).get("cum_wh", 0) if latest else 0
        snap = session.snapshot or {}
        now_price = _current_price(snap.get("periods") or [])
        est_cents = round(cum_wh * now_price / 1000) + round(cum_wh * snap.get("service_fee_cents_per_kwh", 50) / 1000)
        return {"session_id": session_id, "settled": False, "est_total_cents": est_cents, "cum_wh": cum_wh}
    return {
        "session_id": session_id,
        "settled": True,
        "total_cents": order.total_cents,
        "energy_fee_cents": order.energy_fee_cents,
        "service_fee_cents": order.service_fee_cents,
        "occupy_fee_cents": order.occupy_fee_cents,
        "segments": order.segments,
    }


def _current_price(periods: list[dict]) -> int:
    now = datetime.now().strftime("%H:%M")
    for p in periods:
        if p["start"] <= now < p["end"] or (p["start"] > p["end"] and (now >= p["start"] or now < p["end"])):
            return int(p["price_cents"])
    return periods[0]["price_cents"] if periods else 70


def _out(s: ChargingSession) -> SessionOut:
    return SessionOut(
        id=s.id, pile_id=s.pile_id, status=s.status, start_at=s.start_at, end_at=s.end_at,
        kwh_wh=s.kwh_wh, stop_reason=s.stop_reason or "",
    )




@router.patch("/{session_id}/target")
async def set_target(
    session_id: int,
    req: SetTargetReq,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """设置充电目标（SOC% 或金额），达到后自动停止。"""
    session = await db.get(ChargingSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    if session.status not in ("starting", "charging", "paused"):
        raise HTTPException(400, f"当前状态 {session.status} 不可设置目标")
    if req.target_soc is None and req.target_cents is None and req.vehicle_id is None:
        raise HTTPException(400, "至少设置一个目标（target_soc 或 target_cents）")

    # 校验车辆归属
    if req.vehicle_id is not None:
        from app.models import UserVehicle
        v = await db.get(UserVehicle, req.vehicle_id)
        if not v or v.user_id != user.id:
            raise HTTPException(400, "车辆不存在")

    if req.target_soc is not None:
        session.target_soc = req.target_soc
    if req.target_cents is not None:
        session.target_cents = req.target_cents
    if req.vehicle_id is not None:
        session.vehicle_id = req.vehicle_id

    await db.commit()
    logger.info("会话 %s 设置目标 soc=%s amount=%s vehicle=%s", session_id, req.target_soc, req.target_cents, req.vehicle_id)

    # 计算预估时长
    eta_minutes = None
    bus = await get_bus()
    raw = await bus.get(f"pile:{session.pile_id}:latest")
    if raw:
        import json as _json
        d = _json.loads(raw)
        current_soc = float(d.get("soc", 0))
        power_kw = float(d.get("power_kw", 0))
        if req.target_soc and power_kw > 0 and current_soc < req.target_soc:
            battery_kwh = 60
            if session.vehicle_id:
                from app.models import UserVehicle
                v = await db.get(UserVehicle, session.vehicle_id)
                if v:
                    battery_kwh = v.battery_kwh
            need_kwh = battery_kwh * (req.target_soc - current_soc) / 100
            eta_minutes = round(need_kwh / power_kw * 60)

    return {
        "ok": True,
        "target_soc": session.target_soc,
        "target_cents": session.target_cents,
        "vehicle_id": session.vehicle_id,
        "eta_minutes": eta_minutes,
    }
