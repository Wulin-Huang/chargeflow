"""桩模拟器：AI 参数包驱动物理仿真。

设计：LLM 负责分布（车型参数包），本进程负责物理（功率沿 SOC 曲线插值、电量积分、
温度漂移、异常按概率画像随机涌现）。

IoT 语义保真：每桩一条独立 MQTT 连接（独立遗嘱 LWT / Retain 状态 / QoS1 指令），
桩掉线时由 Broker 自动发布该桩的遗嘱消息——与真实设备行为一致。

用法：python pile_simulator.py [--host 127.0.0.1] [--port 1883] [--api http://127.0.0.1:8000] [--chaos]
"""

import argparse
import asyncio
import json
import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx
import aiomqtt

# Windows 默认 Proactor 循环不支持 paho 的 add_reader，必须切 Selector
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("simulator")


def interp_power(soc_curve: list[list[float]], soc: float) -> float:
    pts = sorted(soc_curve, key=lambda p: p[0])
    if soc <= pts[0][0]:
        return pts[0][1]
    if soc >= pts[-1][0]:
        return max(5.0, pts[-1][1])
    for a, b in zip(pts, pts[1:]):
        if a[0] <= soc <= b[0]:
            t = (soc - a[0]) / (b[0] - a[0])
            return a[1] + t * (b[1] - a[1])
    return 10.0


class SimPile:
    """单桩仿真：物理步进 + 独立 MQTT 连接。"""

    def __init__(self, pile: dict, profiles: list[dict]):
        self.id = pile["id"]
        self.code = pile["code"]
        self.max_power_kw = pile["max_power_kw"]
        self.profiles = profiles
        self.client: aiomqtt.Client | None = None

        self.state = "idle"  # idle | charging | occupied
        self.session_id = 0

        self.profile: dict | None = None
        self.soc = 0.0
        self.cum_wh = 0
        self.battery_wh = 0

        self.derate_until = 0.0
        self.temp_anomaly = False
        self.gun_temp = 25.0
        self.unplug_deadline: float | None = None
        self.limit_kw: float | None = None  # 有序充电下发的功率上限（None=不受限）

    def handle_cmd(self, payload: dict) -> tuple[bool, str]:
        action = payload.get("action")
        rid = payload.get("request_id", "")
        if action == "set_power":
            limit = (payload.get("params") or {}).get("limit_kw")
            self.limit_kw = float(limit) if limit and float(limit) > 0 else None
            return True, ""
        if action == "start":
            if self.state != "idle":
                return False, f"state={self.state}"
            pool = [p for p in self.profiles if not p.get("_in_use")] or self.profiles
            if not pool:
                return False, "no profile"
            self.profile = random.choice(pool)
            self.profile["_in_use"] = True
            self.session_id = payload.get("session_id", 0)
            self.battery_wh = self.profile["battery_kwh"] * 1000
            self.soc = random.uniform(8, 40)
            self.cum_wh = 0
            self.gun_temp = random.uniform(23, 28)
            self.unplug_deadline = None
            self.temp_anomaly = False
            self.limit_kw = None
            self.state = "charging"
            return True, ""
        if action == "stop":
            if self.state == "charging":
                self.state = "occupied"
                self.unplug_deadline = time.monotonic() + 5
            return True, ""
        return False, f"unknown action {action}"

    async def resp(self, rid: str, ok: bool, reason: str = "") -> None:
        if self.client:
            await self.client.publish(
                f"pile/{self.id}/cmd_resp", json.dumps({"request_id": rid, "ok": ok, "reason": reason}), qos=1
            )

    async def publish_status(self, state: str) -> None:
        if self.client:
            await self.client.publish(
                f"pile/{self.id}/status", json.dumps({"online": True, "state": state}), qos=1, retain=True
            )

    async def tick(self) -> None:
        c = self.client
        if c is None:
            return
        if self.state == "charging" and self.profile:
            anomaly = self.profile.get("anomaly_profile", {})
            now = time.monotonic()

            # 异常骰子（概率×dt，偶发但真实）
            if now > self.derate_until and random.random() < anomaly.get("derate", 0.03) * 0.02:
                self.derate_until = now + random.uniform(90, 240)
                logger.info("桩 %s 注入降功率异常（AI 概率 %.2f）", self.code, anomaly.get("derate", 0))
            if not self.temp_anomaly and random.random() < anomaly.get("guntemp", 0.02) * 0.01:
                self.temp_anomaly = True
                logger.info("桩 %s 注入枪温异常", self.code)

            power = interp_power(self.profile["soc_curve"], self.soc) * float(self.profile.get("temp_coeff", 1.0))
            if now < self.derate_until:
                power *= 0.3
            power = min(power, self.max_power_kw)
            if self.limit_kw is not None:  # 有序充电：台区调度器下发的功率上限
                power = min(power, self.limit_kw)

            self.cum_wh += power / 3600 * 1000
            self.soc = min(100.0, self.soc + power / 3600 * 1000 / self.battery_wh * 100)

            self.gun_temp += (power / 200) * 0.4 * random.uniform(0.7, 1.3) - 0.35
            if self.temp_anomaly:
                self.gun_temp += 0.8
            self.gun_temp = max(20.0, self.gun_temp)

            voltage = random.randint(395, 405) if self.max_power_kw > 10 else 220
            await c.publish(
                f"pile/{self.id}/telemetry",
                json.dumps(
                    {
                        "session_id": self.session_id,
                        "power_kw": round(power, 1),
                        "voltage": voltage,
                        "current_a": int(power / voltage * 1000) if voltage else 0,
                        "soc": round(self.soc, 1),
                        "gun_temp": round(self.gun_temp, 1),
                        "cum_wh": int(self.cum_wh),
                    }
                ),
                qos=0,  # 高频遥测允许丢失
            )
            if self.soc >= 100.0:
                self.state = "occupied"
                # 占位时长随机：一部分超过免费窗口以触发占位费
                self.unplug_deadline = time.monotonic() + random.choice([10 * 60, 20 * 60, 42 * 60, 55 * 60])
                await c.publish(f"pile/{self.id}/event", json.dumps({"type": "full", "session_id": self.session_id}), qos=1)
                await self.publish_status("occupied")

        elif self.state == "occupied":
            await c.publish(
                f"pile/{self.id}/telemetry",
                json.dumps({"session_id": 0, "power_kw": 0, "soc": 100, "gun_temp": 26, "cum_wh": int(self.cum_wh)}),
                qos=0,
            )
            if self.unplug_deadline and time.monotonic() >= self.unplug_deadline:
                await c.publish(f"pile/{self.id}/event", json.dumps({"type": "unplug", "session_id": self.session_id}), qos=1)
                self._reset()
                await self.publish_status("idle")

        else:
            await c.publish(
                f"pile/{self.id}/telemetry",
                json.dumps({"session_id": 0, "power_kw": 0, "soc": 0, "gun_temp": 25, "cum_wh": 0}),
                qos=0,
            )

    def _reset(self) -> None:
        self.state = "idle"
        self.session_id = 0
        self.cum_wh = 0
        if self.profile:
            self.profile.pop("_in_use", None)
        self.profile = None
        self.unplug_deadline = None
        self.temp_anomaly = False


async def run_pile(pl: SimPile, args) -> None:
    """单桩连接生命周期：断线自动重连；遗嘱在异常断开时由 broker 发布。"""
    will = aiomqtt.Will(f"pile/{pl.id}/lwt", json.dumps({"online": False, "code": pl.code}), qos=1)
    while True:
        try:
            async with aiomqtt.Client(
                args.host, args.port, identifier=f"pile-sim-{pl.code}", will=will
            ) as client:
                pl.client = client
                await client.subscribe(f"pile/{pl.id}/cmd", qos=1)
                await pl.publish_status("idle" if pl.state == "idle" else pl.state)

                async def ticker():
                    while True:
                        await asyncio.sleep(1.0)
                        await pl.tick()

                async def messages():
                    async for m in client.messages:
                        if m.topic.value.split("/")[2] != "cmd":
                            continue
                        try:
                            payload = json.loads(m.payload.decode())
                        except json.JSONDecodeError:
                            continue
                        ok, reason = pl.handle_cmd(payload)
                        await pl.resp(payload.get("request_id", ""), ok, reason)
                        if ok and payload.get("action") == "start":
                            await pl.publish_status("charging")

                await asyncio.gather(ticker(), messages())
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 断线（含混沌）后随机退避重连
            logger.warning("桩 %s 连接断开（%s），稍后重连", pl.code, type(e).__name__)
            pl.client = None
            await asyncio.sleep(random.uniform(30, 60))


async def abrupt_kill(pl: SimPile) -> None:
    """演示专用：跳过 DISCONNECT 报文直接断 socket → broker 发布遗嘱（真实掉线语义）。"""
    try:
        pl.client._client._sock_close()  # noqa: SLF001
    except Exception:
        pass


async def fetch_bootstrap(api: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{api}/internal/bootstrap")
        r.raise_for_status()
        return r.json()


class VirtualDriver:
    """虚拟车流：经真实 API 走完整闭环（登录→启动→充电→停止→结算扣款→再启动）。

    大屏「数据是活的」演示基础；同时让台区调度器有真实负荷可调度。
    """

    def __init__(self, idx: int, api: str):
        self.phone = f"139{idx:08d}"
        self.password = "traffic123"
        self.api = api
        self.token = ""
        self.name = f"虚拟车主-{idx:02d}"
        self.active_session_id = 0
        self.active_pile_id = 0
        self.stop_at: float = 0.0

    async def ensure_account(self) -> None:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{self.api}/auth/login", json={"phone": self.phone, "password": self.password})
            if r.status_code == 401:
                await c.post(
                    f"{self.api}/auth/register",
                    json={"phone": self.phone, "password": self.password, "nickname": self.name},
                )
                r = await c.post(f"{self.api}/auth/login", json={"phone": self.phone, "password": self.password})
            r.raise_for_status()
            self.token = r.json()["token"]

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    async def _recharge_if_poor(self) -> None:
        async with httpx.AsyncClient(timeout=15) as c:
            w = (await c.get(f"{self.api}/wallet", headers=self._h())).json()
            if w["balance_cents"] < 500:
                await c.post(f"{self.api}/wallet/recharge", headers=self._h(), json={"amount_cents": 5000})

    async def drive_once(self) -> None:
        """一次完整出行：选站→挑空闲直流桩→充电 2-6 分钟→主动停止（结算扣款）。"""
        await self._recharge_if_poor()
        async with httpx.AsyncClient(timeout=15) as c:
            stations = (await c.get(f"{self.api}/stations", headers=self._h())).json()
            st = random.choice(stations)
            piles = (await c.get(f"{self.api}/stations/{st['id']}/piles", headers=self._h())).json()
            candidates = [p for p in piles if p["status"] == "idle" and p["connector"] == "DC"]
            if not candidates:
                return
            pile = random.choice(candidates)
            r = await c.post(
                f"{self.api}/sessions/start",
                headers=self._h(),
                json={"pile_id": pile["id"], "request_id": f"vf-{self.phone}-{int(time.time() * 1000)}"},
            )
            if r.status_code != 200:
                return
            self.active_session_id = r.json()["id"]
            self.active_pile_id = pile["id"]
            self.stop_at = time.monotonic() + random.uniform(120, 360)

    async def stop_if_due(self) -> None:
        if self.active_session_id and time.monotonic() >= self.stop_at:
            async with httpx.AsyncClient(timeout=15) as c:
                try:
                    await c.post(f"{self.api}/sessions/{self.active_session_id}/stop", headers=self._h())
                except Exception:
                    pass
            self.active_session_id = 0

    async def loop(self) -> None:
        await self.ensure_account()
        while True:
            try:
                await self.stop_if_due()
                if not self.active_session_id:
                    await self.drive_once()
            except Exception:
                pass
            await asyncio.sleep(random.uniform(8, 20))


async def traffic(api: str, n: int) -> None:
    drivers = [VirtualDriver(i, api) for i in range(1, n + 1)]
    logger.info("虚拟车流启动：%d 位车主经真实 API 循环充电（余额不足自动充值）", n)
    await asyncio.gather(*[d.loop() for d in drivers])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--chaos", action="store_true", help="随机让充电中的桩异常掉线（触发 LWT/故障分支）")
    parser.add_argument("--traffic", type=int, default=0, metavar="N", help="虚拟车流：N 位车主经真实 API 循环充电")
    args = parser.parse_args()

    boot = await fetch_bootstrap(args.api)
    profiles: list[dict] = boot["profiles"]
    piles = [SimPile(p, profiles) for p in boot["piles"]]
    logger.info("模拟器就绪：%d 桩（每桩独立 MQTT 连接），%d 个车型参数包", len(piles), len(profiles))

    async def refresh_profiles() -> None:
        known = {p["model_name"] for p in profiles}
        while True:
            await asyncio.sleep(30)
            try:
                data = await fetch_bootstrap(args.api)
                new = [p for p in data["profiles"] if p["model_name"] not in known]
                if new:
                    profiles.extend(new)
                    known.update(p["model_name"] for p in new)
                    for pl in piles:
                        pl.profiles = profiles
                    logger.info("热加载 %d 个新车型参数包（AI 生成）", len(new))
            except Exception:
                pass

    async def chaos() -> None:
        if not args.chaos:
            return
        while True:
            await asyncio.sleep(random.uniform(90, 240))
            victims = [p for p in piles if p.state == "charging"]
            if victims:
                v = random.choice(victims)
                logger.warning("混沌模式：桩 %s 模拟异常掉线（应触发 LWT → fault → 工单）", v.code)
                await abrupt_kill(v)

    tasks = [run_pile(p, args) for p in piles] + [refresh_profiles(), chaos()]
    if args.traffic > 0:
        tasks.append(traffic(args.api, args.traffic))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("模拟器退出（进程级 kill 会触发全部桩的 LWT——演示故障分支用）")
