"""异常诊断：规则引擎触发（确定性）+ LLM 定因（模糊性）——各干各擅长的事。

LLM 输出过 pydantic 结构化校验；校验失败或 AI 不可用时降级为无诊断工单。
"""

import logging
from datetime import timedelta

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.ai.client import chat_json, deepseek_available
from app.db import SessionLocal, utcnow
from app.gateway.broadcaster import broadcaster
from app.models import Telemetry, WorkOrder

logger = logging.getLogger("chargeflow.ai.diagnose")


class Diagnosis(BaseModel):
    severity: str = Field(pattern="^(low|medium|high)$")
    root_cause: str = Field(max_length=200)
    evidence: list[str] = Field(max_length=6)
    action: str = Field(max_length=200)
    confidence: float = Field(ge=0, le=1)

    @field_validator("evidence")
    @classmethod
    def check_ev(cls, v):
        return [e[:120] for e in v]


PROMPT = """你是充电桩运维诊断专家。基于以下真实遥测摘要与触发规则，给出结构化诊断。
只输出 JSON：{{"severity":"low|medium|high","root_cause":"一句话根因","evidence":["证据1","证据2"],"action":"建议处置","confidence":0.0}}
触发规则：{trigger}
遥测摘要：{digest}
要求：证据必须引用摘要中的具体数字，不确定时降低 confidence。"""


async def _telemetry_digest(pile_id: int) -> dict:
    async with SessionLocal() as db:
        since = utcnow() - timedelta(minutes=60)
        stats = (
            await db.execute(
                select(
                    func.count(Telemetry.id),
                    func.avg(Telemetry.power_kw),
                    func.max(Telemetry.power_kw),
                    func.min(Telemetry.power_kw),
                    func.max(Telemetry.gun_temp),
                    func.max(Telemetry.soc),
                    func.max(Telemetry.cum_wh),
                ).where(Telemetry.pile_id == pile_id, Telemetry.ts >= since)
            )
        ).one()
        return {
            "samples_1h": stats[0] or 0,
            "avg_power_kw": round(float(stats[1] or 0), 1),
            "max_power_kw": round(float(stats[2] or 0), 1),
            "min_power_kw": round(float(stats[3] or 0), 1),
            "max_gun_temp_c": round(float(stats[4] or 0), 1),
            "max_soc": int(stats[5] or 0),
            "energy_wh_1h": int(stats[6] or 0),
        }


async def diagnose_work_order(wo_id: int) -> None:
    """规则已触发（工单已建），LLM 补充定因与建议。失败降级不影响工单本身。"""
    async with SessionLocal() as db:
        wo = await db.get(WorkOrder, wo_id)
        if wo is None or wo.status != "open" or wo.ai_diagnosis.get("attempted"):
            return
        wo.ai_diagnosis = {**(wo.ai_diagnosis or {}), "attempted": True}
        await db.commit()

    if not deepseek_available():
        return

    digest = await _telemetry_digest(wo.pile_id)
    prompt = PROMPT.format(trigger=wo.trigger_rule, digest=digest)
    try:
        result = await chat_json(
            [
                {"role": "system", "content": "你是严谨的运维诊断专家，只输出符合要求的 JSON。"},
                {"role": "user", "content": prompt},
            ],
            scene="diagnose",
        )
        diag = Diagnosis.model_validate(result).model_dump()
        diag["model"] = "deepseek"
    except Exception as e:  # noqa: BLE001 统一降级：工单保留，诊断为空
        logger.warning("AI 诊断降级 wo=%s: %s", wo_id, e)
        diag = {"degraded": True, "reason": "AI 诊断不可用，请人工分析"}

    async with SessionLocal() as db:
        wo = await db.get(WorkOrder, wo_id)
        if wo:
            wo.ai_diagnosis = {**(wo.ai_diagnosis or {}), **diag}
            wo.severity = diag.get("severity", wo.severity)
            await db.commit()
    await broadcaster.publish("work_order", {"id": wo_id, "pile_id": wo.pile_id, "type": wo.type, "diagnosis": diag})
