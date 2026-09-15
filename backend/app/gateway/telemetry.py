"""遥测攒批写：500ms flush，把逐条 INSERT 的写放大降两个数量级。

读侧走 Redis 最新值（TTL 60s），PG 明细只为回放/结算/分析存在。
"""

import asyncio
import logging
from datetime import datetime

from sqlalchemy import insert

from app.db import SessionLocal, utcnow
from app.models import Telemetry

logger = logging.getLogger("chargeflow.telemetry")

FLUSH_INTERVAL = 0.5


class TelemetryBuffer:
    def __init__(self) -> None:
        self._buf: list[dict] = []
        self._task: asyncio.Task | None = None
        self._dropped = 0

    def push(self, session_id: int, pile_id: int, payload: dict) -> None:
        if len(self._buf) > 20_000:  # 背压保护：极瑞情况下丢 QoS0 数据而非撑爆内存
            self._dropped += 1
            return
        self._buf.append(
            {
                "session_id": session_id,
                "pile_id": pile_id,
                "ts": utcnow(),
                "power_kw": payload.get("power_kw", 0),
                "voltage": payload.get("voltage", 0),
                "current_a": payload.get("current_a", 0),
                "soc": payload.get("soc", 0),
                "gun_temp": payload.get("gun_temp", 25.0),
                "cum_wh": payload.get("cum_wh", 0),
            }
        )

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.flush()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            try:
                await self.flush()
            except Exception:
                logger.exception("遥测批量写失败")

    async def flush(self) -> None:
        if not self._buf:
            return
        batch, self._buf = self._buf, []
        async with SessionLocal() as db:
            await db.execute(insert(Telemetry), batch)
            await db.commit()

    @property
    def stats(self) -> dict:
        return {"pending": len(self._buf), "dropped": self._dropped}


telemetry_buffer = TelemetryBuffer()
