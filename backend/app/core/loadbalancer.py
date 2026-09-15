"""台区有序充电：负荷调度器。

物理背景：站点共用一台配电变压器（台区），额定容量有限。多桩同时满功率充电会
超过台区容量 → 跳闸/罚款风险。真实运营商（特来电/星星充电）的核心能力就是
「有序充电」：平台侧实时感知各桩负荷，超容时动态限功率，负载回落后自动恢复。

算法：水位填充公平份额（water-filling fair share）——
  把台区容量想象成水池，各桩需求是高低不一的柱子：
  1. 需求低于当前水位的桩全额满足，剩余容量在更高需求的桩间平摊；
  2. 不超容时零干预（returns demands 原样），保证不影响正常充电体验；
  3. 单测锁定：分配之和恒 ≤ 容量、需求低者不劣于需求高者（无嫉妒分配）。

可靠性：限功率指令复用 MQTT QoS1 指令通道（幂等 request_id），桩执行后回执；
调度器对桩保持「当前限额」状态，负载回落后下发解除指令（limit=0）。
"""

import asyncio
import json
import logging
import time
import uuid

from sqlalchemy import select

from app.db import SessionLocal
from app.gateway.broadcaster import broadcaster
from app.models import Pile, Station

logger = logging.getLogger("chargeflow.loadbalancer")

DISPATCH_INTERVAL_S = 6.0
RELIEVE_MARGIN = 0.95  # 需求回落到容量的 95% 以下才解除限制（防抖动）
SETTLE_THRESHOLD = 0.97  # 分配与当前功率差超过 3% 才下发（减少指令量）


def fair_share(demands: list[float], capacity: float) -> list[float]:
    """水位填充公平份额分配。需求总额 ≤ 容量时原样返回；否则按水位填充限功率。"""
    if not demands:
        return []
    if capacity <= 0:
        return [0.0] * len(demands)
    total = sum(demands)
    if total <= capacity:
        return list(demands)

    allocated = [0.0] * len(demands)
    order = sorted(range(len(demands)), key=lambda i: demands[i])
    remaining = capacity
    for idx, i in enumerate(order):
        peers = len(demands) - idx  # 含自身的剩余桩数
        share = remaining / peers
        if demands[i] <= share:
            allocated[i] = demands[i]
            remaining -= demands[i]
        else:
            # 该桩拿不满份额：此后所有桩统一按份额平摊（同一水位）
            for j in order[idx:]:
                allocated[j] = remaining / peers
            break
    return allocated


class LoadBalancer:
    def __init__(self) -> None:
        self._limits: dict[int, float] = {}  # pile_id -> 当前下发的限功率（kW）
        self._stations: list[dict] = []  # [{id, name, capacity_kw, pile_ids}]
        self._last_report: dict[int, dict] = {}

    def refresh_topology(self, stations: list[dict]) -> None:
        self._stations = stations

    @property
    def last_report(self) -> dict[int, dict]:
        return dict(self._last_report)

    def status_of(self, pile_id: int) -> float | None:
        return self._limits.get(pile_id)

    async def dispatch_once(self) -> None:
        """单轮调度：读实时功率 → 按站聚合 → 超容限功率 / 回落解除。"""
        from app.bus import get_bus

        bus = await get_bus()
        for st in self._stations:
            demands: list[float] = []
            ids: list[int] = []
            for pid, _max_kw in await self._piles_of(st["id"]):
                raw = await bus.get(f"pile:{pid}:latest")
                if not raw:
                    continue
                d = json.loads(raw)
                if d.get("session_id"):  # 只调度活跃会话
                    demands.append(float(d.get("power_kw", 0)))
                    ids.append(pid)
            capacity = float(st["capacity_kw"] or 0)
            if not demands or capacity <= 0:
                continue
            total_demand = sum(demands)
            shares = fair_share(demands, capacity)
            curbed = total_demand > capacity
            for pid, share, demand in zip(ids, shares, demands):
                if curbed and share < demand * SETTLE_THRESHOLD:
                    await self._send_limit(pid, round(share, 1))
                elif not curbed and total_demand < capacity * RELIEVE_MARGIN and pid in self._limits:
                    await self._send_limit(pid, 0)  # 解除限制
                    self._limits.pop(pid, None)
            self._last_report[st["id"]] = {
                "station_id": st["id"],
                "name": st["name"],
                "capacity_kw": capacity,
                "demand_kw": round(total_demand, 1),
                "charging_piles": len(ids),
                "curbed": curbed,
                "shaved_kw": round(total_demand - sum(shares), 1) if curbed else 0.0,
            }

    async def _send_limit(self, pile_id: int, limit_kw: float) -> None:
        from app.gateway.mqtt_gateway import gateway

        if limit_kw > 0:
            self._limits[pile_id] = limit_kw
        rid = f"lb-{uuid.uuid4().hex[:12]}"
        try:
            await gateway.publish_cmd(0, rid, pile_id, "set_power", {"limit_kw": limit_kw})
            await broadcaster.publish(
                "power_dispatch", {"pile_id": pile_id, "limit_kw": limit_kw, "ts": time.time()}
            )
        except Exception:  # noqa: BLE001 网关未就绪：下一轮重试
            logger.debug("set_power 下发失败 pile=%s", pile_id)

    async def _piles_of(self, station_id: int) -> list[tuple[int, int]]:
        async with SessionLocal() as db:
            rows = (await db.execute(select(Pile.id, Pile.max_power_kw).where(Pile.station_id == station_id))).all()
        return rows


balancer = LoadBalancer()


async def loadbalance_loop() -> None:
    """周期调度：拓扑每 30s 刷新一次，功率分配每 6s 一轮。"""
    top_counter = 0
    while True:
        try:
            if top_counter % 5 == 0:
                async with SessionLocal() as db:
                    rows = (await db.execute(select(Station))).scalars().all()
                balancer.refresh_topology(
                    [{"id": s.id, "name": s.name, "capacity_kw": s.capacity_kw or 0} for s in rows]
                )
            await balancer.dispatch_once()
            report = {sid: r for sid, r in balancer.last_report.items() if r["charging_piles"] > 0}
            if report:
                await broadcaster.publish("load_status", {"stations": list(report.values())})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("负荷调度轮次失败")
        top_counter += 1
        await asyncio.sleep(DISPATCH_INTERVAL_S)
