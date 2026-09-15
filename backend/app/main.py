import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.ai.profiles import ensure_profiles, seed_default_profiles
from app.api import admin, ai, auth, internal, reservations, sessions, stations, vehicles, wallet
from app.broker import start_embedded_broker, stop_embedded_broker
from app.core.loadbalancer import loadbalance_loop
from app.db import init_db, utcnow
from app.gateway.broadcaster import broadcaster
from app.gateway.mqtt_gateway import gateway, kpi_loop
from app.gateway.telemetry import telemetry_buffer
from app.models import Reservation
from app.scheduler import run_periodic
from app.security import decode_token
from app.seed import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("chargeflow")


async def reservation_sweeper() -> None:
    """预约生命周期巡检：到窗激活、过窗过期、过期释放桩。"""
    from app.db import SessionLocal
    from app.models import Pile

    async with SessionLocal() as db:
        now = utcnow()
        pending = (await db.execute(select(Reservation).where(Reservation.status == "pending"))).scalars().all()
        for r in pending:
            if r.start_at <= now:
                r.status = "active"
        active = (await db.execute(select(Reservation).where(Reservation.status == "active"))).scalars().all()
        for r in active:
            if r.end_at + (r.end_at - r.start_at) * 0.2 < now:  # 宽限 20% 时长
                r.status = "expired"
                pile = await db.get(Pile, r.pile_id)
                if pile and pile.status == "reserved":
                    pile.status = "idle"
                await broadcaster.publish("pile_update", {"pile_id": r.pile_id, "status": "idle"})
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if sys.platform == "win32" and isinstance(asyncio.get_event_loop(), asyncio.ProactorEventLoop):
        # add_reader 不可用 → MQTT 网关永远连不上；提前点名根因
        logger.critical("Windows 检测到 Proactor 事件循环，MQTT 网关将不可用！请用 python serve.py 启动")
    await init_db()
    await seed()
    await seed_default_profiles()

    broker = await start_embedded_broker()
    await broadcaster.start()
    await telemetry_buffer.start()
    await gateway.start()

    run_periodic(30, reservation_sweeper, name="reservation-sweeper")
    run_periodic(5, kpi_loop, name="kpi-loop")
    asyncio.create_task(loadbalance_loop(), name="loadbalance-loop")  # 自带 6s 循环，无需再包周期器
    asyncio.create_task(ensure_profiles())  # AI 参数包：失败静默降级，默认包兜底
    logger.info("ChargeFlow 启动完成：broker=%s", broker is not None)
    yield

    await gateway.stop()
    await telemetry_buffer.stop()
    await broadcaster.stop()
    await stop_embedded_broker(broker)


app = FastAPI(title="ChargeFlow 充电站 IoT 运营平台", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(stations.router)
app.include_router(sessions.router)
app.include_router(reservations.router)
app.include_router(ai.router)
app.include_router(admin.router)
app.include_router(internal.router)
app.include_router(wallet.router)
app.include_router(vehicles.router)


@app.get("/health")
async def health():
    return {"ok": True, "service": "chargeflow"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = ""):
    try:
        decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    await broadcaster.connect(ws)
    try:
        while True:
            await ws.receive_text()  # 心跳保活（客户端 ping）
    except WebSocketDisconnect:
        broadcaster.disconnect(ws)
    except Exception:
        broadcaster.disconnect(ws)


# 生产模式下托管前端构建产物
import mimetypes

# Windows 注册表会让 mimetypes 把 .js 猜成 text/plain，浏览器将拒绝执行 module 脚本
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
