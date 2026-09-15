from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.reservation import acquire_pile_lock, create_reservation_safely, release_pile_lock
from app.db import get_db, utcnow
from app.gateway.broadcaster import broadcaster
from app.models import Pile, Reservation, User
from app.schemas import ReservationReq, ReservationOut
from app.security import current_user

router = APIRouter(prefix="/reservations", tags=["reservations"])


@router.post("", response_model=ReservationOut)
async def create_reservation(
    req: ReservationReq,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.start_at <= utcnow():
        raise HTTPException(422, "预约开始时间必须晚于当前时间")
    pile = await db.get(Pile, req.pile_id)
    if pile is None:
        raise HTTPException(404, "桩不存在")

    # 第一层：Redis SETNX 快速筛掉绝大多数竞争
    if not await acquire_pile_lock(pile.id):
        raise HTTPException(409, "手慢了，该桩正被抢占")
    try:
        reservation = Reservation(user_id=user.id, pile_id=req.pile_id, start_at=req.start_at, end_at=req.end_at)
        rid, err = await create_reservation_safely(db, reservation)  # 第二层：部分唯一索引兜底
        if err:
            raise HTTPException(409, err)
    finally:
        await release_pile_lock(pile.id)

    pile.status = "reserved"
    await db.commit()
    await broadcaster.publish("pile_update", {"pile_id": pile.id, "status": "reserved"})
    return ReservationOut(id=rid, pile_id=req.pile_id, start_at=req.start_at, end_at=req.end_at, status="pending")


@router.post("/{reservation_id}/cancel")
async def cancel(reservation_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    res = await db.get(Reservation, reservation_id)
    if res is None or res.user_id != user.id:
        raise HTTPException(404, "预约不存在")
    if res.status not in ("pending", "active"):
        raise HTTPException(409, f"预约状态 {res.status}，不可取消")
    res.status = "cancelled"
    pile = await db.get(Pile, res.pile_id)
    if pile and pile.status == "reserved":
        pile.status = "idle"
    await db.commit()
    await broadcaster.publish("pile_update", {"pile_id": res.pile_id, "status": "idle"})
    return {"ok": True}


@router.get("/mine", response_model=list[ReservationOut])
async def mine(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(Reservation).where(Reservation.user_id == user.id).order_by(Reservation.id.desc()).limit(20)
        )
    ).scalars().all()
    return [ReservationOut(id=r.id, pile_id=r.pile_id, start_at=r.start_at, end_at=r.end_at, status=r.status) for r in rows]
