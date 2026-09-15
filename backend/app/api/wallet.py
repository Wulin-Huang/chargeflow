"""钱包：余额、充值与消费明细——支付闭环的用户侧入口。

结算侧（网关 settle_session）在事务内扣款；本模块只做查询与充值，
欠费拦截在 /sessions/start 启动风控里（余额 ≤ 0 拒绝启动）。
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import BillingOrder, ChargingSession, User
from app.security import current_user

logger = logging.getLogger("chargeflow.wallet")
router = APIRouter(prefix="/wallet", tags=["wallet"])


class RechargeReq(BaseModel):
    amount_cents: int = Field(gt=0, le=1000000)  # 单笔 1 分 ~ 1 万元


@router.get("")
async def wallet(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    orders = (
        await db.execute(
            select(BillingOrder, ChargingSession.pile_id)
            .join(ChargingSession, ChargingSession.id == BillingOrder.session_id)
            .where(ChargingSession.user_id == user.id)
            .order_by(BillingOrder.id.desc())
            .limit(10)
        )
    ).all()
    return {
        "balance_cents": user.balance_cents,
        "arrears": user.balance_cents < 0,
        "recent_orders": [
            {
                "order_id": o.id,
                "session_id": o.session_id,
                "pile_id": pid,
                "total_cents": o.total_cents,
                "created_at": o.created_at.isoformat(),
            }
            for o, pid in orders
        ],
    }


@router.post("/recharge")
async def recharge(req: RechargeReq, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    user.balance_cents += req.amount_cents
    await db.commit()
    logger.info("用户 %s 充值 %d 分 → 余额 %d 分", user.id, req.amount_cents, user.balance_cents)
    return {"ok": True, "balance_cents": user.balance_cents}
