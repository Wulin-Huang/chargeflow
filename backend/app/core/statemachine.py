"""显式状态机：跃迁表集中定义，非法跃迁直接告警——缺陷在开发期暴露而非线上。

充电会话状态机（与方案文档图 2 一致）：
  idle → reserved → starting → charging ⇄ paused → occupied → settling → idle
  任意 → fault → idle
"""

import logging

logger = logging.getLogger("chargeflow.statemachine")

TRANSITIONS: dict[tuple[str, str], str] = {
    ("idle", "RESERVED"): "reserved",
    ("reserved", "START_CMD_SENT"): "starting",
    ("reserved", "CANCELLED"): "idle",
    ("reserved", "EXPIRED"): "idle",
    ("starting", "CMD_ACK_OK"): "charging",
    ("starting", "CMD_ACK_REJECT"): "idle",
    ("starting", "CMD_TIMEOUT"): "idle",
    ("charging", "PAUSED"): "paused",
    ("paused", "RESUMED"): "charging",
    ("charging", "FULL"): "occupied",
    ("charging", "STOPPED"): "settling",
    ("charging", "UNPLUGGED"): "settling",
    ("paused", "STOPPED"): "settling",
    ("occupied", "UNPLUGGED"): "settling",
    ("charging", "FAULT"): "fault",
    ("paused", "FAULT"): "fault",
    ("occupied", "FAULT"): "fault",
    ("fault", "WO_CLOSED"): "idle",
    ("settling", "SETTLED"): "idle",
}

PILE_STATES = ("idle", "reserved", "starting", "charging", "paused", "occupied", "fault")


def next_state(current: str, event: str) -> str:
    key = (current, event)
    if key not in TRANSITIONS:
        logger.warning("非法状态跃迁被拒绝：session=%s + 事件=%s", current, event)
        raise ValueError(f"非法跃迁：{current} + {event}")
    return TRANSITIONS[key]


def try_next_state(current: str, event: str) -> str | None:
    return TRANSITIONS.get((current, event))
