"""充电目标检查：纯函数模块，供网关遥测处理调用。"""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ChargingSession, PricePolicy, User
from app.security import current_user

logger = logging.getLogger("chargeflow.target")
router = APIRouter(prefix="/sessions", tags=["sessions"])


class SetTargetReq(BaseModel):
    target_soc: int | None = Field(default=None, ge=20, le=100, description="目标 SOC%，达到自动停止")
    target_cents: int | None = Field(default=None, gt=0, le=100000, description="目标金额（分），预估费用达到即停止")
    vehicle_id: int | None = None  # 关联车辆（可选，用于充电时长预估）


@router.patch("/{session_id}/target")
async def set_target(
    session_id: int,
    req: SetTargetReq,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
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

    # 计算预估时长（如果有车辆电池容量 + 当前 SOC）
    eta_minutes = None
    from app.bus import get_bus
    bus = await get_bus()
    raw = await bus.get(f"pile:{session.pile_id}:latest")
    if raw:
        import json as _json
        d = _json.loads(raw)
        current_soc = float(d.get("soc", 0))
        power_kw = float(d.get("power_kw", 0))
        if req.target_soc and power_kw > 0 and current_soc < req.target_soc:
            # 用关联车辆电池容量或默认 60kWh 估算
            battery_kwh = 60
            if req.vehicle_id:
                from app.models import UserVehicle
                v = await db.get(UserVehicle, req.vehicle_id)
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


@dataclass
class TargetCheckResult:
    should_stop: bool
    reason: str  # "target_soc" | "target_amount" | ""


async def check_target(session: ChargingSession, telemetry: dict) -> TargetCheckResult:
    """检查遥测是否达到充电目标，达到则返回停止原因。"""
    if session.status != "charging":
        return TargetCheckResult(False, "")
    soc = float(telemetry.get("soc", 0))
    cum_wh = float(telemetry.get("cum_wh", 0))

    if session.target_soc and soc >= session.target_soc:
        return TargetCheckResult(True, "target_soc")

    if session.target_cents and cum_wh > 0:
        # 简单估算：按当前电量 × 平均电价（取快照首段电价 + 服务费）
        # 精确计费需走完整引擎，这里做近似判断触发停止
        try:
            snap = session.snapshot or {}
            periods = snap.get("periods", [])
            service_fee = int(snap.get("service_fee_cents_per_kwh", 50))
            # 取当前时段电价（用第一个 period 近似，避免复杂时间计算）
            price = periods[0].get("price_cents", 70) if periods else 70
            est_cents = int(cum_wh / 1000 * (price + service_fee))
            if est_cents >= session.target_cents:
                return TargetCheckResult(True, "target_amount")
        except Exception:  # noqa: BLE001
            pass

    return TargetCheckResult(False, "")
