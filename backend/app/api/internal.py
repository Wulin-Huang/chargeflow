"""内部接口：仅供桩模拟器拉取桩清单与车型参数包（演示单机部署，不对公网暴露）。"""

from fastapi import APIRouter
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Pile, VehicleProfile

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


@router.get("/bootstrap")
async def bootstrap():
    """模拟器启动时拉取：要模拟的桩 + 可用车型参数包。"""
    async with SessionLocal() as db:
        piles = (await db.execute(select(Pile))).scalars().all()
        profiles = (await db.execute(select(VehicleProfile))).scalars().all()
        return {
            "piles": [{"id": p.id, "code": p.code, "connector": p.connector, "max_power_kw": p.max_power_kw} for p in piles],
            "profiles": [
                {
                    "id": v.id,
                    "model_name": v.model_name,
                    "battery_kwh": v.battery_kwh,
                    "soc_curve": v.soc_curve,
                    "temp_coeff": float(v.temp_coeff),
                    "anomaly_profile": v.anomaly_profile,
                }
                for v in profiles
            ],
        }
