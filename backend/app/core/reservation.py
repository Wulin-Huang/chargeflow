"""预约双层防护：Redis SETNX 快速筛选 + DB 部分唯一索引最终兜底。"""

import logging

from sqlalchemy.exc import IntegrityError

from app.bus import get_bus

logger = logging.getLogger("chargeflow.reservation")


async def acquire_pile_lock(pile_id: int, ttl: int = 10) -> bool:
    bus = get_bus()
    return await bus.set(f"pile:{pile_id}:reslock", "1", nx=True, ex=ttl)


async def release_pile_lock(pile_id: int) -> None:
    await get_bus().delete(f"pile:{pile_id}:reslock")


async def create_reservation_safely(db, reservation) -> tuple[int | None, str]:
    """返回 (reservation_id, None) 或 (None, reason)。"""
    try:
        db.add(reservation)
        await db.commit()
        await db.refresh(reservation)
        return reservation.id, None
    except IntegrityError:
        await db.rollback()
        logger.info("唯一索引兜底拦截并发预约 pile=%s", reservation.pile_id)
        return None, "该桩已被其他用户抢先预约"
