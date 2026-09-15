"""MQTT 网关：设备侧与业务侧的唯一通道。

职责：状态/事件/遥测上行分发、指令下行投递与补偿、异常规则引擎、会话结算。
可靠性三层保障：QoS1 至少一次投递 + request_id 业务幂等 + 补偿任务兜底。
"""

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime

import aiomqtt
from sqlalchemy import func, select, update

from app.config import settings
from app.db import SessionLocal, utcnow
from app.models import BillingOrder, ChargingSession, Pile, Telemetry, User, WorkOrder
from app.core.billing import Sample, compute_bill, compute_occupy_fee, parse_periods
from app.gateway.broadcaster import broadcaster
from app.gateway.telemetry import telemetry_buffer
from app.scheduler import run_later

logger = logging.getLogger("chargeflow.gateway")

SUB_TOPICS = ("pile/+/telemetry", "pile/+/event", "pile/+/cmd_resp", "pile/+/status", "pile/+/lwt")
CMD_TIMEOUT_S = 8
POWER_DROP_RATIO = 0.7
POWER_DROP_SUSTAIN_S = 120
GUN_TEMP_ALARM = 55.0


def _parse_ts(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


class MqttGateway:
    def __init__(self) -> None:
        self.client: aiomqtt.Client | None = None
        self._running = False
        self._ready = asyncio.Event()
        self._pending_cmds: dict[str, dict] = {}
        self._runtime: dict[int, dict] = {}

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._run_with_reconnect())

    async def stop(self) -> None:
        self._running = False

    async def _run_with_reconnect(self) -> None:
        while self._running:
            try:
                async with aiomqtt.Client(
                    settings.mqtt_host, settings.mqtt_port, identifier="chargeflow-gateway-01"
                ) as client:
                    self.client = client
                    for t in SUB_TOPICS:
                        await client.subscribe(t, qos=1)
                    self._ready.set()
                    logger.info("MQTT 网关已连接并订阅 %s", SUB_TOPICS)
                    async for message in client.messages:
                        await self._dispatch(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._ready.clear()
                logger.exception("MQTT 网关连接异常，3s 后重连")
                await asyncio.sleep(3)

    async def wait_ready(self, timeout: float = 5.0) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ---------- 指令下行 ----------
    async def publish_cmd(self, session_id: int, request_id: str, pile_id: int, action: str, params: dict | None = None) -> None:
        if not await self.wait_ready():
            raise RuntimeError("MQTT 网关未就绪")
        payload = json.dumps(
            {"request_id": request_id, "session_id": session_id, "action": action, "params": params or {}}
        )
        await self.client.publish(f"pile/{pile_id}/cmd", payload, qos=1)
        self._pending_cmds[request_id] = {
            "session_id": session_id,
            "pile_id": pile_id,
            "action": action,
            "sent_at": time.monotonic(),
            "retries": 0,
        }
        run_later(CMD_TIMEOUT_S, self._compensate_cmd, request_id)

    async def _compensate_cmd(self, request_id: str) -> None:
        info = self._pending_cmds.get(request_id)
        if info is None:  # 已收到回执
            return
        session_id, pile_id, action = info["session_id"], info["pile_id"], info["action"]
        async with SessionLocal() as db:
            session = await db.get(ChargingSession, session_id)
        if session is None or session.status not in ("starting", "charging", "paused"):
            self._pending_cmds.pop(request_id, None)
            return

        if info["retries"] == 0:
            info["retries"] = 1
            logger.warning("指令回执超时，重发一次 request_id=%s", request_id)
            await self.client.publish(
                f"pile/{pile_id}/cmd",
                json.dumps({"request_id": request_id, "session_id": session_id, "action": action, "params": {}}),
                qos=1,
            )
            run_later(CMD_TIMEOUT_S, self._compensate_cmd, request_id)
        else:
            # 二次失败：状态回滚 + 释放桩 + 幂等键允许用户重新扫码
            logger.error("指令补偿失败，回滚会话 %s", request_id)
            self._pending_cmds.pop(request_id, None)
            async with SessionLocal() as db:
                s = await db.get(ChargingSession, session_id)
                if s and s.status == "starting":
                    s.status = "failed"
                    s.stop_reason = "cmd_timeout"
                    s.end_at = utcnow()
                    await db.execute(update(Pile).where(Pile.id == pile_id).values(status="idle"))
                    await db.commit()
            await broadcaster.publish("session_update", {"session_id": session_id, "status": "failed", "reason": "cmd_timeout"})
            await broadcaster.publish("pile_update", {"pile_id": pile_id, "status": "idle"})

    # ---------- 上行分发 ----------
    async def _dispatch(self, message: aiomqtt.Message) -> None:
        try:
            parts = message.topic.value.split("/")
            pile_id = int(parts[1])
            kind = parts[2]
            payload = json.loads(message.payload.decode())
        except (ValueError, IndexError, json.JSONDecodeError):
            logger.warning("丢弃无法解析的报文 topic=%s", message.topic.value)
            return
        handler = {
            "status": self._on_status,
            "lwt": self._on_lwt,
            "event": self._on_event,
            "cmd_resp": self._on_cmd_resp,
            "telemetry": self._on_telemetry,
        }.get(kind)
        if handler:
            try:
                await handler(pile_id, payload)
            except Exception:
                logger.exception("处理 %s 报文失败 pile=%s", kind, pile_id)

    async def _on_status(self, pile_id: int, payload: dict) -> None:
        online = bool(payload.get("online", True))
        state = payload.get("state", "idle")
        status = state if online else "offline"
        async with SessionLocal() as db:
            await db.execute(update(Pile).where(Pile.id == pile_id).values(status=status))
            await db.commit()
        await broadcaster.publish("pile_update", {"pile_id": pile_id, "status": status})

    async def _on_lwt(self, pile_id: int, payload: dict) -> None:
        logger.warning("桩 %s 遗嘱触发（连接异常断开）", pile_id)
        session = await self._active_session(pile_id)
        async with SessionLocal() as db:
            await db.execute(update(Pile).where(Pile.id == pile_id).values(status="offline"))
            await db.commit()
        if session:
            await self._create_work_order(pile_id, "offline", {"lwt": payload, "session_id": session.id})
            await self.settle_session(session.id, stop_reason="pile_offline")
        await broadcaster.publish("pile_update", {"pile_id": pile_id, "status": "offline"})

    async def _on_event(self, pile_id: int, payload: dict) -> None:
        etype = payload.get("type")
        session = await self._active_session(pile_id)
        if session is None:
            return
        if etype == "full":
            async with SessionLocal() as db:
                s = await db.get(ChargingSession, session.id)
                if s and s.status in ("charging", "paused"):
                    s.status = "occupied"
                    snap = dict(s.snapshot or {})
                    snap["full_at"] = utcnow().isoformat()
                    s.snapshot = snap
                    await db.commit()
            await db_publish_helper(pile_id, "occupied")
            await broadcaster.publish("session_update", {"session_id": session.id, "status": "occupied"})
        elif etype in ("unplug", "stopped"):
            await self.settle_session(session.id, stop_reason=etype)
        elif etype == "fault":
            await self._create_work_order(pile_id, "device_fault", payload)
            await self.settle_session(session.id, stop_reason="device_fault")

    async def _on_cmd_resp(self, pile_id: int, payload: dict) -> None:
        request_id = payload.get("request_id")
        info = self._pending_cmds.pop(request_id, None)
        if info is None:
            return
        session_id = info["session_id"]
        async with SessionLocal() as db:
            s = await db.get(ChargingSession, session_id)
            if s is None:
                return
            if info["action"] == "start":
                if payload.get("ok") and s.status == "starting":
                    s.status = "charging"
                    await db.execute(update(Pile).where(Pile.id == pile_id).values(status="charging"))
                    await db.commit()
                    await broadcaster.publish("session_update", {"session_id": session_id, "status": "charging"})
                    await broadcaster.publish("pile_update", {"pile_id": pile_id, "status": "charging"})
                elif not payload.get("ok") and s.status == "starting":
                    s.status = "failed"
                    s.stop_reason = f"rejected:{payload.get('reason', 'unknown')}"
                    s.end_at = utcnow()
                    await db.execute(update(Pile).where(Pile.id == pile_id).values(status="idle"))
                    await db.commit()
                    await broadcaster.publish("session_update", {"session_id": session_id, "status": "failed", "reason": s.stop_reason})
            elif info["action"] == "stop":
                await db.commit()

    async def _on_telemetry(self, pile_id: int, payload: dict) -> None:
        session_id = payload.get("session_id") or 0
        if session_id:
            telemetry_buffer.push(session_id, pile_id, payload)
        bus_latest = {
            "power_kw": payload.get("power_kw", 0),
            "soc": payload.get("soc", 0),
            "gun_temp": payload.get("gun_temp", 25),
            "cum_wh": payload.get("cum_wh", 0),
            "session_id": session_id,
            "ts": utcnow().isoformat(),
        }
        from app.bus import get_bus

        await get_bus().set(f"pile:{pile_id}:latest", json.dumps(bus_latest), ex=60)
        if session_id:
            await broadcaster.publish(
                "telemetry",
                {"pile_id": pile_id, **bus_latest},
                throttle_key=f"tel:{pile_id}",
                min_interval=1.0,
            )
            await self._rule_engine(pile_id, payload)
            await self._check_charge_target(pile_id, payload, session_id)

    async def _check_charge_target(self, pile_id: int, payload: dict, session_id: int) -> None:
        """充电目标检查：达到 SOC 或金额目标时自动下发 stop。每会话只触发一次。"""
        from app.core.chargetarget import check_target

        rt_key = f"_target_stopped_{session_id}"
        if self._runtime.get(pile_id, {}).get(rt_key):
            return

        async with SessionLocal() as db:
            s = await db.get(ChargingSession, session_id)
            if not s or s.status != "charging":
                return
            if not s.target_soc and not s.target_cents:
                return
            result = await check_target(s, payload)
            if not result.should_stop:
                return
            # 标记已触发，防重复
            rt = self._runtime.setdefault(pile_id, {})
            rt[rt_key] = True
            logger.info("会话 %s 达到充电目标 %s，自动停止", session_id, result.reason)
            s.stop_reason = result.reason
            await db.commit()
        # 下发停止指令（复用 stop 链路）
        await self.publish_cmd(session_id, f"target-auto-{session_id}", pile_id, "stop")

    # ---------- 异常规则引擎（触发确定性归代码） ----------
    async def _rule_engine(self, pile_id: int, payload: dict) -> None:
        rt = self._runtime.setdefault(pile_id, {"powers": deque(maxlen=300), "drop_since": None, "temp_wo": False})
        power = float(payload.get("power_kw", 0))
        gun_temp = float(payload.get("gun_temp", 25))
        rt["powers"].append((time.monotonic(), power))

        if gun_temp > GUN_TEMP_ALARM and not rt["temp_wo"]:
            rt["temp_wo"] = True
            await self._create_work_order(pile_id, "overtemp", {"gun_temp": gun_temp})
            return

        powers = rt["powers"]
        if len(powers) < 120:
            return
        now = time.monotonic()
        recent = [p for t, p in powers if now - t <= 30]
        baseline = [p for t, p in powers if 60 <= now - t <= 180]
        if recent and baseline and max(baseline) > 10:
            avg_recent = sum(recent) / len(recent)
            avg_base = sum(baseline) / len(baseline)
            if avg_base > 0 and avg_recent < avg_base * POWER_DROP_RATIO:
                if rt["drop_since"] is None:
                    rt["drop_since"] = now
                elif now - rt["drop_since"] >= POWER_DROP_SUSTAIN_S:
                    rt["drop_since"] = None
                    await self._create_work_order(
                        pile_id,
                        "power_drop",
                        {"avg_baseline_kw": round(avg_base, 1), "avg_recent_kw": round(avg_recent, 1)},
                    )
            else:
                rt["drop_since"] = None

    async def _create_work_order(self, pile_id: int, wo_type: str, extra: dict) -> None:
        from app.ai.diagnose import diagnose_work_order

        async with SessionLocal() as db:
            exists = await db.execute(
                select(WorkOrder).where(WorkOrder.pile_id == pile_id, WorkOrder.type == wo_type, WorkOrder.status == "open")
            )
            if exists.scalar():
                return
            wo = WorkOrder(pile_id=pile_id, type=wo_type, trigger_rule=str(extra))
            db.add(wo)
            await db.commit()
            await db.refresh(wo)
            wo_id = wo.id
        await broadcaster.publish("work_order", {"id": wo_id, "pile_id": pile_id, "type": wo_type, "status": "open"})
        run_later(1, diagnose_work_order, wo_id)  # LLM 定因在增强层异步执行

    # ---------- 结算 ----------
    async def _active_session(self, pile_id: int):
        async with SessionLocal() as db:
            r = await db.execute(
                select(ChargingSession)
                .where(
                    ChargingSession.pile_id == pile_id,
                    ChargingSession.status.in_(("starting", "charging", "paused", "occupied")),
                )
                .order_by(ChargingSession.id.desc())
                .limit(1)
            )
            s = r.scalar()
            if s:
                await db.refresh(s)
            return s

    async def settle_session(self, session_id: int, stop_reason: str = "normal") -> None:
        async with SessionLocal() as db:
            s = await db.get(ChargingSession, session_id)
            if s is None or s.status in ("settled", "failed", "settling"):
                return
            rows = (
                await db.execute(select(Telemetry).where(Telemetry.session_id == session_id).order_by(Telemetry.ts))
            ).scalars().all()
            samples = [Sample(ts=r.ts, cum_wh=r.cum_wh) for r in rows]
            snapshot = dict(s.snapshot or {})
            periods = parse_periods(snapshot.get("periods") or [])
            occupy_fee = 0
            full_at = _parse_ts(snapshot.get("full_at"))
            if full_at and snapshot.get("occupy_billed") is not True:
                occupy_fee = compute_occupy_fee(
                    full_at, utcnow(), snapshot.get("free_occupy_minutes", 30), snapshot.get("occupy_fee_cents_per_min", 50)
                )
                snapshot["occupy_billed"] = True
                s.snapshot = snapshot
            bill = compute_bill(samples, periods, snapshot.get("service_fee_cents_per_kwh", 50), occupy_fee)

            db.add(
                BillingOrder(
                    session_id=session_id,
                    energy_fee_cents=bill["energy_fee_cents"],
                    service_fee_cents=bill["service_fee_cents"],
                    occupy_fee_cents=bill["occupy_fee_cents"],
                    total_cents=bill["total_cents"],
                    segments=bill["segments"],
                )
            )
            # 钱包扣款：与账单同一事务原子提交；余额不足记欠费（负余额），下次启动拦截
            u = await db.get(User, s.user_id)
            if u:
                u.balance_cents -= bill["total_cents"]
                if u.balance_cents < 0:
                    snap = dict(s.snapshot or {})
                    snap["arrears_cents"] = -u.balance_cents
                    s.snapshot = snap
                    logger.warning("用户 %s 欠费 %d 分（结算扣款后余额为负）", u.id, -u.balance_cents)
            s.status = "settled"
            s.end_at = utcnow()
            s.kwh_wh = bill["total_wh"]
            s.stop_reason = stop_reason
            await db.execute(update(Pile).where(Pile.id == s.pile_id).values(status="idle"))
            await db.commit()
            self._runtime.pop(s.pile_id, None)

        await broadcaster.publish(
            "session_update",
            {"session_id": session_id, "status": "settled", "stop_reason": stop_reason, "total_cents": bill["total_cents"]},
        )
        await broadcaster.publish("bill", {"session_id": session_id, "total_cents": bill["total_cents"]})

    # ---------- 停止指令（用户主动） ----------
    async def request_stop(self, session_id: int, request_id: str, pile_id: int) -> None:
        await self.publish_cmd(session_id, request_id, pile_id, "stop")


async def db_publish_helper(pile_id: int, status: str) -> None:
    async with SessionLocal() as db:
        await db.execute(update(Pile).where(Pile.id == pile_id).values(status=status))
        await db.commit()
    await broadcaster.publish("pile_update", {"pile_id": pile_id, "status": status})


gateway = MqttGateway()


async def kpi_loop() -> None:
    """每 5s 聚合平台 KPI 推送大屏。"""
    from app.bus import get_bus

    while True:
        try:
            await asyncio.sleep(5)
            async with SessionLocal() as db:
                total_piles = (await db.execute(select(func.count(Pile.id)))).scalar() or 0
                online = 0
                charging = 0
                total_power = 0.0
                for p in (await db.execute(select(Pile))).scalars():
                    raw = await get_bus().get(f"pile:{p.id}:latest")
                    if raw:
                        online += 1
                        d = json.loads(raw)
                        if d.get("session_id"):
                            charging += 1
                            total_power += float(d.get("power_kw", 0))
                today = utcnow().date().isoformat()
                energy_wh = (await db.execute(select(func.max(Telemetry.cum_wh)))).scalar() or 0
            await broadcaster.publish(
                "kpi",
                {
                    "piles_total": total_piles,
                    "piles_online": online,
                    "charging_sessions": charging,
                    "total_power_kw": round(total_power, 1),
                    "date": today,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("KPI 聚合失败")
