"""消息总线抽象：dev 用 fakeredis（进程内），生产用真实 Redis。

两种驱动暴露完全一致的接口（SETNX/EX/TTL/pub-sub），切换只改 REDIS_URL。
"""

import redis.asyncio as aioredis
from fakeredis import aioredis as fake_aioredis

from app.config import settings

_pool: aioredis.Redis | None = None


def get_bus() -> aioredis.Redis:
    global _pool
    if _pool is None:
        if settings.redis_url:
            _pool = aioredis.from_url(settings.redis_url, decode_responses=True)
        else:
            _pool = fake_aioredis.FakeRedis(decode_responses=True)
    return _pool


WS_CHANNEL = "ws:broadcast"
