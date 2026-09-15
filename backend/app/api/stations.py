from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import get_bus
from app.db import get_db
from app.models import Pile, PricePolicy, Station, User
from app.schemas import PileOut, StationOut
from app.security import current_user

router = APIRouter(prefix="/stations", tags=["stations"])


@router.get("", response_model=list[StationOut])
async def list_stations(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    stations = (await db.execute(select(Station))).scalars().all()
    out = []
    for st in stations:
        piles = (await db.execute(select(Pile).where(Pile.station_id == st.id))).scalars().all()
        free = charging = 0
        for p in piles:
            latest = await get_bus().get(f"pile:{p.id}:latest")
            if not latest:
                continue
            if p.status == "idle":
                free += 1
            elif p.status in ("charging", "paused"):
                charging += 1
        policy = (
            await db.execute(select(PricePolicy).where(PricePolicy.station_id == st.id).order_by(PricePolicy.id.desc()).limit(1))
        ).scalar()
        hint = ""
        if policy and policy.periods:
            lo = min(p["price_cents"] for p in policy.periods)
            hi = max(p["price_cents"] for p in policy.periods)
            hint = f"¥{lo / 100:.2f}–¥{hi / 100:.2f}"
        out.append(
            StationOut(
                id=st.id, name=st.name, address=st.address, lat=float(st.lat), lng=float(st.lng),
                total_piles=len(piles), free_piles=free, charging_piles=charging, price_hint=hint,
            )
        )
    return out


@router.get("/{station_id}/piles", response_model=list[PileOut])
async def station_piles(station_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    piles = (await db.execute(select(Pile).where(Pile.station_id == station_id))).scalars().all()
    out = []
    for p in piles:
        latest = await get_bus().get(f"pile:{p.id}:latest")
        # redis latest 只证明"设备在线"；业务状态以 DB 为准
        status = p.status if latest else "offline"
        out.append(PileOut(id=p.id, code=p.code, connector=p.connector, max_power_kw=p.max_power_kw, status=status))
    return out
