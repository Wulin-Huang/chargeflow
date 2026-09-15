from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import event, text as sqlalchemy_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def _make_engine():
    engine = create_async_engine(settings.db_url, echo=False, pool_pre_ping=True)
    if settings.db_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


engine = _make_engine()
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    from app import models  # noqa: F401 确保模型注册

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 轻量迁移：create_all 不修改既有表，手工补列（幂等）
        result = await conn.execute(sqlalchemy_text("PRAGMA table_info(stations)"))
        if "capacity_kw" not in {row[1] for row in result}:
            await conn.execute(sqlalchemy_text("ALTER TABLE stations ADD COLUMN capacity_kw INTEGER DEFAULT 0"))

        # charging_sessions 新列迁移（车辆关联 + 充电目标）
        cs_cols = {row[1] for row in await conn.execute(sqlalchemy_text("PRAGMA table_info(charging_sessions)"))}
        if "vehicle_id" not in cs_cols:
            await conn.execute(sqlalchemy_text("ALTER TABLE charging_sessions ADD COLUMN vehicle_id INTEGER"))
        if "target_soc" not in cs_cols:
            await conn.execute(sqlalchemy_text("ALTER TABLE charging_sessions ADD COLUMN target_soc INTEGER"))
        if "target_cents" not in cs_cols:
            await conn.execute(sqlalchemy_text("ALTER TABLE charging_sessions ADD COLUMN target_cents INTEGER"))
