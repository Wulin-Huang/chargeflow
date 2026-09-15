"""运营日报：聚合真实数据 → LLM 生成分析叙事。失败降级为纯统计模板。"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.ai.client import chat_json, deepseek_available
from app.db import SessionLocal, utcnow
from app.gateway.broadcaster import broadcaster
from app.models import BillingOrder, ChargingSession, DailyReport, WorkOrder

logger = logging.getLogger("chargeflow.ai.report")

PROMPT = """你是充电站运营分析师。基于以下当日真实运营数据输出 JSON：{{"analysis":"..."}}
analysis 为 150 字以内的中文分析：突出电量/收入/时段分布的特征，归因异常工单，给出一条可执行建议。语气专业克制，数字必须来自数据。
数据：{stats}"""


async def generate_daily_report(report_date: date | None = None) -> dict:
    report_date = report_date or utcnow().date()
    day_start = datetime.combine(report_date, datetime.min.time())
    day_end = datetime.combine(report_date + timedelta(days=1), datetime.min.time())

    async with SessionLocal() as db:
        sessions = (
            await db.execute(
                select(ChargingSession).where(ChargingSession.start_at >= day_start, ChargingSession.start_at < day_end)
            )
        ).scalars().all()
        energy_wh = sum(s.kwh_wh for s in sessions)
        orders = (
            await db.execute(
                select(BillingOrder).where(BillingOrder.created_at >= day_start, BillingOrder.created_at < day_end)
            )
        ).scalars().all()
        revenue_cents = sum(o.total_cents for o in orders)
        wos = (
            await db.execute(
                select(WorkOrder).where(WorkOrder.created_at >= day_start, WorkOrder.created_at < day_end)
            )
        ).scalars().all()
        wo_types = {}
        for w in wos:
            wo_types[w.type] = wo_types.get(w.type, 0) + 1

    stats = {
        "date": report_date.isoformat(),
        "sessions": len(sessions),
        "energy_kwh": round(energy_wh / 1000, 1),
        "revenue_yuan": round(revenue_cents / 100, 2),
        "work_orders": len(wos),
        "work_order_types": wo_types,
        "avg_kwh_per_session": round(energy_wh / max(1, len(sessions)) / 1000, 1),
    }

    narrative = f"统计日报（降级模板）：{stats['sessions']} 单，{stats['energy_kwh']} kWh，收入 ¥{stats['revenue_yuan']}，工单 {len(wos)} 个。"
    if deepseek_available():
        try:
            result = await chat_json(
                [{"role": "user", "content": PROMPT.format(stats=stats)}],
                scene="daily_report",
            )
            if isinstance(result, dict) and result.get("analysis"):
                narrative = str(result["analysis"])[:500]
        except Exception as e:  # noqa: BLE001
            logger.warning("AI 日报降级：%s", e)

    async with SessionLocal() as db:
        existing = (
            await db.execute(select(DailyReport).where(DailyReport.report_date == report_date.isoformat()))
        ).scalar()
        if existing:
            existing.stats = stats
            existing.narrative = narrative
        else:
            db.add(DailyReport(report_date=report_date.isoformat(), stats=stats, narrative=narrative, created_at=utcnow()))
        await db.commit()

    await broadcaster.publish("daily_report", {"date": report_date.isoformat(), "stats": stats, "narrative": narrative})
    return {"stats": stats, "narrative": narrative}
