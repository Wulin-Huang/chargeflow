from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import User
from app.schemas import LoginReq, RegisterReq, TokenResp
from app.security import create_token, current_user, make_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResp)
async def register(req: RegisterReq, db: AsyncSession = Depends(get_db)):
    exists = (await db.execute(select(User).where(User.phone == req.phone))).scalar()
    if exists:
        raise HTTPException(409, "手机号已注册")
    h, s = make_password(req.password)
    user = User(phone=req.phone, password_hash=h, salt=s, nickname=req.nickname or req.phone[-4:])
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return TokenResp(token=create_token(user.id, user.role), role=user.role, user_id=user.id, nickname=user.nickname)


@router.post("/login", response_model=TokenResp)
async def login(req: LoginReq, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.phone == req.phone))).scalar()
    if user is None or not verify_password(req.password, user.salt, user.password_hash):
        raise HTTPException(401, "手机号或密码错误")
    return TokenResp(token=create_token(user.id, user.role), role=user.role, user_id=user.id, nickname=user.nickname)


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return {"user_id": user.id, "phone": user.phone, "nickname": user.nickname, "role": user.role, "balance_cents": user.balance_cents}

