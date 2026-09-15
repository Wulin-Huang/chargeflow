"""车型参数包生成：LLM 负责分布，代码负责物理。

生成 → pydantic 严格校验 → 入库 vehicle_profiles（一次生成永久复用）。
模拟器读取参数包做物理仿真，异常按概率画像随机涌现——数据「活」的源头。
"""

import logging

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.ai.client import chat_json, deepseek_available
from app.db import SessionLocal, utcnow
from app.models import VehicleProfile

logger = logging.getLogger("chargeflow.ai.profiles")

PROMPT = """你是一名电动车充电仿真专家。请生成 {n} 个虚构车型的充电仿真参数包，要求：
1. 覆盖不同定位（800V 高压平台旗舰 / 400V 中端 / 入门小车），battery_kwh 在 30-120；
2. soc_curve：8-12 个点，每个点是 [soc, power_kw]，soc 从 5 到 100 单调递增且不超过 100，每个点的 power_kw 必须在 5-250 之间（soc=100 收尾点也不得低于 5），快充拐点后明显下降（CC-CV 特征），峰值与车型定位匹配；
3. anomaly_profile 三个概率都在 0.01-0.15 之间，让异常"偶发但真实"；
4. temp_coeff 在 0.7-1.3。
输出 JSON：{{"profiles":[{{"model_name": "...", "battery_kwh": 0, "soc_curve": [[5, 123], ...], "temp_coeff": 1.0, "anomaly_profile": {{"derate": 0.05, "guntemp": 0.03, "offline": 0.01}}}}]}}"""


class ProfilePack(BaseModel):
    model_name: str = Field(max_length=60)
    battery_kwh: int = Field(ge=30, le=120)
    soc_curve: list[list[float]] = Field(min_length=6, max_length=14)
    temp_coeff: float = Field(ge=0.7, le=1.3)
    anomaly_profile: dict = Field(default_factory=dict)

    # LLM 输出是不可信边界：越界值清洗归一（clamp/sort/dedup），仅结构性损坏才拒绝

    @field_validator("battery_kwh", mode="before")
    @classmethod
    def clamp_battery(cls, v):
        try:
            return min(120, max(30, int(v)))
        except (TypeError, ValueError):
            return 60

    @field_validator("temp_coeff", mode="before")
    @classmethod
    def clamp_temp(cls, v):
        try:
            return min(1.3, max(0.7, float(v)))
        except (TypeError, ValueError):
            return 1.0

    @field_validator("soc_curve")
    @classmethod
    def check_curve(cls, v):
        if len(v) < 2:
            raise ValueError("soc_curve 至少 2 个点")
        pts = sorted(
            ([min(100.0, max(0.0, float(a))), min(250.0, max(0.5, float(b)))] for a, b in v),
            key=lambda p: p[0],
        )
        dedup = [pts[0]]
        for p in pts[1:]:
            if p[0] > dedup[-1][0]:
                dedup.append(p)
        if len(dedup) < 2:
            raise ValueError("soc_curve 去重后有效点不足")
        return dedup

    @field_validator("anomaly_profile", mode="before")
    @classmethod
    def fill_anomaly(cls, v):
        if not isinstance(v, dict):
            return {"derate": 0.05, "guntemp": 0.03, "offline": 0.01}
        out = {}
        for k, default in (("derate", 0.05), ("guntemp", 0.03), ("offline", 0.01)):
            try:
                out[k] = min(0.3, max(0.0, float(v.get(k, default))))
            except (TypeError, ValueError):
                out[k] = default
        return out


class ProfilesResult(BaseModel):
    profiles: list[ProfilePack]


DEFAULT_PROFILES = [
    {
        "model_name": "默认·旗舰 800V",
        "battery_kwh": 100,
        "soc_curve": [[5, 185], [15, 190], [25, 190], [35, 188], [45, 182], [55, 165], [65, 140], [75, 110], [85, 80], [95, 45], [100, 20]],
        "temp_coeff": 1.0,
        "anomaly_profile": {"derate": 0.05, "guntemp": 0.02, "offline": 0.01},
    },
    {
        "model_name": "默认·中端 400V",
        "battery_kwh": 66,
        "soc_curve": [[5, 88], [15, 92], [30, 90], [45, 84], [60, 70], [75, 52], [90, 32], [100, 10]],
        "temp_coeff": 1.0,
        "anomaly_profile": {"derate": 0.06, "guntemp": 0.03, "offline": 0.01},
    },
    {
        "model_name": "默认·入门小车",
        "battery_kwh": 35,
        "soc_curve": [[5, 42], [20, 45], [40, 43], [60, 38], [80, 28], [100, 8]],
        "temp_coeff": 1.0,
        "anomaly_profile": {"derate": 0.04, "guntemp": 0.02, "offline": 0.01},
    },
]


async def seed_default_profiles() -> None:
    async with SessionLocal() as db:
        for p in DEFAULT_PROFILES:
            exists = (await db.execute(select(VehicleProfile).where(VehicleProfile.model_name == p["model_name"]))).scalar()
            if exists is None:
                db.add(VehicleProfile(**p, created_at=utcnow()))
        await db.commit()


async def generate_profiles(n: int = 12) -> dict:
    """调用 DeepSeek 生成车型参数包。失败时抛 LLMError（上层降级）。"""
    raw = await chat_json(
        [{"role": "user", "content": PROMPT.format(n=n)}],
        scene="profile_gen",
    )
    packs = ProfilesResult.model_validate(raw).profiles
    saved = 0
    async with SessionLocal() as db:
        for p in packs:
            exists = (await db.execute(select(VehicleProfile).where(VehicleProfile.model_name == p.model_name))).scalar()
            if exists is None:
                db.add(
                    VehicleProfile(
                        model_name=p.model_name,
                        battery_kwh=p.battery_kwh,
                        soc_curve=[[float(a), float(b)] for a, b in p.soc_curve],
                        temp_coeff=p.temp_coeff,
                        anomaly_profile=p.anomaly_profile,
                        created_at=utcnow(),
                    )
                )
                saved += 1
        await db.commit()
    return {"generated": len(packs), "saved": saved}


async def ensure_profiles(min_count: int = 12) -> dict:
    """启动时自动补齐 AI 参数包（可选增强：无 Key 时静默跳过，默认包兜底）。"""
    async with SessionLocal() as db:
        from sqlalchemy import func

        count = (await db.execute(select(func.count(VehicleProfile.id)))).scalar() or 0
    if count >= min_count or not deepseek_available():
        return {"skipped": True, "count": count}
    try:
        return await generate_profiles(min_count - count)
    except Exception:
        logger.warning("AI 参数包生成失败，使用默认参数包兜底（核心链路不受影响）")
        return {"skipped": True, "degraded": True}
