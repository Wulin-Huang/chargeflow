"""轻量 asyncio 任务调度：延迟任务 + 周期任务。

单进程演示用进程内实现；生产切换 arq + Redis（补偿/过期逻辑完全复用）。
"""

import asyncio
import logging

logger = logging.getLogger("chargeflow.scheduler")

_tasks: set[asyncio.Task] = set()


def run_later(seconds: float, fn, *args) -> asyncio.Task:
    async def _wrapper():
        await asyncio.sleep(seconds)
        try:
            await fn(*args)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("延迟任务执行失败 fn=%s", getattr(fn, "__name__", fn))

    t = asyncio.create_task(_wrapper())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t


def run_periodic(interval: float, fn, *args, name: str = "") -> asyncio.Task:
    async def _wrapper():
        while True:
            await asyncio.sleep(interval)
            try:
                await fn(*args)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("周期任务执行失败 name=%s", name)

    t = asyncio.create_task(_wrapper(), name=name or getattr(fn, "__name__", "periodic"))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t
