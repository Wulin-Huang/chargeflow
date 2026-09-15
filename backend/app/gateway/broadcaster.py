"""WS 连接管理 + Redis pub/sub 扇出。

多实例部署时每个实例订阅 ws:broadcast 频道，各自推给自己的连接——业务侧无感。
"""

import asyncio
import json
import logging

from fastapi import WebSocket

from app.bus import WS_CHANNEL, get_bus

logger = logging.getLogger("chargeflow.ws")


class Broadcaster:
    def __init__(self) -> None:
        self._conns: set[WebSocket] = set()
        self._task: asyncio.Task | None = None
        self._last_push: dict[str, float] = {}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._conns.add(ws)
        logger.info("WS 接入，当前连接 %d", len(self._conns))

    def disconnect(self, ws: WebSocket) -> None:
        self._conns.discard(ws)
        logger.info("WS 断开，当前连接 %d", len(self._conns))

    async def start(self) -> None:
        self._task = asyncio.create_task(self._subscribe_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _subscribe_loop(self) -> None:
        while True:
            try:
                bus = get_bus()
                pubsub = bus.pubsub()
                await pubsub.subscribe(WS_CHANNEL)
                async for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    await self._broadcast_raw(msg["data"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("pub/sub 订阅异常，3s 后重连")
                await asyncio.sleep(3)

    async def _broadcast_raw(self, raw: str) -> None:
        if not self._conns:
            return
        dead = []
        for ws in list(self._conns):
            try:
                await ws.send_text(raw)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def publish(self, event_type: str, data: dict, throttle_key: str | None = None, min_interval: float = 1.0) -> None:
        """发布事件到频道。throttle_key 用于遥测类高频事件节流。"""
        import time

        if throttle_key:
            now = time.monotonic()
            if now - self._last_push.get(throttle_key, 0) < min_interval:
                return
            self._last_push[throttle_key] = now
        payload = json.dumps({"type": event_type, "data": data, "ts": time.time()})
        await get_bus().publish(WS_CHANNEL, payload)

    @property
    def connection_count(self) -> int:
        return len(self._conns)


broadcaster = Broadcaster()
