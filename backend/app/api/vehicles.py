"""车主服务 API：我的爱车（车辆管理）。"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import User, UserVehicle, VehicleProfile
from app.security import current_user

logger = logging.getLogger("chargeflow.vehicles")
router = APIRouter(prefix="/vehicles", tags=["vehicles"])


class VehicleCreateReq(BaseModel):
    nickname: str = Field(min_length=1, max_length=32)
    plate: str = Field(min_length=6, max_length=10, default="")
    profile_id: int | None = None  # 关联车型参数包（可选）
    battery_kwh: int = Field(gt=0, le=200, default=60)
    default_target_soc: int = Field(ge=20, le=100, default=80)


class VehicleUpdateReq(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=32)
    plate: str | None = None
    profile_id: int | None = None
    battery_kwh: int | None = Field(default=None, gt=0, le=200)
    default_target_soc: int | None = Field(default=None, ge=20, le=100)
    is_default: bool | None = None


def _vehicle_dict(v: UserVehicle, profile: VehicleProfile | None = None) -> dict:
    return {
        "id": v.id,
        "nickname": v.nickname,
        "plate": v.plate,
        "battery_kwh": v.battery_kwh,
        "default_target_soc": v.default_target_soc,
        "is_default": v.is_default,
        "profile_id": v.profile_id,
        "profile_name": profile.model_name if profile else None,
        "total_sessions": v.total_sessions,
        "total_kwh": round(v.total_wh / 1000, 2),
        "created_at": v.created_at.isoformat(),
    }


@router.get("")
async def list_vehicles(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(UserVehicle).where(UserVehicle.user_id == user.id).order_by(UserVehicle.is_default.desc(), UserVehicle.id.desc())
        )
    ).scalars().all()
    profiles = {}
    if rows:
        pids = [r.profile_id for r in rows if r.profile_id]
        if pids:
            for p in (await db.execute(select(VehicleProfile).where(VehicleProfile.id.in_(pids)))).scalars():
                profiles[p.id] = p
    return {"total": len(rows), "vehicles": [_vehicle_dict(v, profiles.get(v.profile_id)) for v in rows]}


@router.post("")
async def add_vehicle(req: VehicleCreateReq, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    # 验证 profile_id（如果提供）
    if req.profile_id:
        if not await db.get(VehicleProfile, req.profile_id):
            raise HTTPException(400, "车型参数包不存在")
    # 首辆车自动设为默认
    existing = (
        await db.execute(select(UserVehicle.id).where(UserVehicle.user_id == user.id).limit(1))
    ).scalar()
    is_default = existing is None
    v = UserVehicle(
        user_id=user.id,
        nickname=req.nickname,
        plate=req.plate,
        profile_id=req.profile_id,
        battery_kwh=req.battery_kwh,
        default_target_soc=req.default_target_soc,
        is_default=is_default,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    logger.info("用户 %s 添加车辆 %s", user.id, v.nickname)
    profile = await db.get(VehicleProfile, v.profile_id) if v.profile_id else None
    return {"ok": True, "vehicle": _vehicle_dict(v, profile)}


@router.patch("/{vehicle_id}")
async def update_vehicle(vehicle_id: int, req: VehicleUpdateReq, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    v = await db.get(UserVehicle, vehicle_id)
    if not v or v.user_id != user.id:
        raise HTTPException(404, "车辆不存在")
    if req.nickname is not None:
        v.nickname = req.nickname
    if req.plate is not None:
        v.plate = req.plate
    if req.profile_id is not None:
        if not await db.get(VehicleProfile, req.profile_id):
            raise HTTPException(400, "车型参数包不存在")
        v.profile_id = req.profile_id
    if req.battery_kwh is not None:
        v.battery_kwh = req.battery_kwh
    if req.default_target_soc is not None:
        v.default_target_soc = req.default_target_soc
    if req.is_default is True:
        # 取消其他默认
        others = (
            await db.execute(select(UserVehicle).where(UserVehicle.user_id == user.id, UserVehicle.is_default == True))
        ).scalars().all()
        for o in others:
            o.is_default = False
        v.is_default = True
    await db.commit()
    await db.refresh(v)
    profile = await db.get(VehicleProfile, v.profile_id) if v.profile_id else None
    return {"ok": True, "vehicle": _vehicle_dict(v, profile)}


@router.delete("/{vehicle_id}")
async def delete_vehicle(vehicle_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    v = await db.get(UserVehicle, vehicle_id)
    if not v or v.user_id != user.id:
        raise HTTPException(404, "车辆不存在")
    if v.is_default:
        raise HTTPException(400, "默认车辆不可删除，请先设置其他车辆为默认")
    await db.delete(v)
    await db.commit()
    logger.info("用户 %s 删除车辆 %s", user.id, v.nickname)
    return {"ok": True}
