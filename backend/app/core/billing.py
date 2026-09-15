"""分时电价分段计费引擎。

不变量（有单测锁定）：
  1. 金额全程整数运算（毫厘 = 1/1000 分），杜绝浮点误差；
  2. 电量按 Wh 整数累积，分段按时间比例切分，各段之和恒等于总量；
  3. 分段四舍五入后加总恒等于总额（最大余数段吸收残差）。
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class TOUPeriod:
    start: time
    end: time  # exclusive，支持跨午夜（start > end）
    price_cents_per_kwh: int


@dataclass(frozen=True)
class Sample:
    ts: datetime
    cum_wh: int


def period_at(t: time, periods: list[TOUPeriod]) -> TOUPeriod:
    for p in periods:
        if p.start <= p.end:
            if p.start <= t < p.end:
                return p
        elif t >= p.start or t < p.end:  # 跨午夜段
            return p
    raise ValueError("电价时段未覆盖全天")


def _boundaries_between(a: datetime, b: datetime, periods: list[TOUPeriod]) -> list[datetime]:
    """找出 (a, b) 内的换价边界时刻。"""
    out = []
    day = a.date()
    for d in (day, day + timedelta(days=1)):
        for p in periods:
            bt = datetime.combine(d, p.start)
            if a < bt < b:
                out.append(bt)
    return sorted(set(out))


def _round_half_up(x: float) -> int:
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def _split_delta_by_cuts(a: datetime, b: datetime, delta_wh: int) -> list[tuple[datetime, datetime, int]]:
    """把 [a,b] 区间的 delta_wh 按时间比例分摊到各子区间，末段吸收整数残差。"""
    total_ms = int((b - a).total_seconds() * 1000)
    cuts = [a] + _boundaries_between(a, b, _ACTIVE_PERIODS) + [b]
    pieces = []
    allocated = 0
    for i in range(len(cuts) - 1):
        ms = int((cuts[i + 1] - cuts[i]).total_seconds() * 1000)
        wh = _round_half_up(delta_wh * ms / total_ms)
        pieces.append((cuts[i], cuts[i + 1], wh))
        allocated += wh
    last = pieces[-1]
    pieces[-1] = (last[0], last[1], last[2] + (delta_wh - allocated))
    return pieces


_ACTIVE_PERIODS: list[TOUPeriod] = []  # compute_energy_segments 注入，避免深传参


def compute_energy_segments(samples: list[Sample], periods: list[TOUPeriod]) -> list[dict]:
    """把 (ts, cum_wh) 采样序列按换价边界切成段，电量按时间比例分摊并聚合。"""
    global _ACTIVE_PERIODS
    if len(samples) < 2:
        return []
    _ACTIVE_PERIODS = periods

    merged: list[dict] = []
    for a, b in zip(samples, samples[1:]):
        delta_wh = b.cum_wh - a.cum_wh
        if (b.ts - a.ts).total_seconds() <= 0 or delta_wh <= 0:
            continue
        for seg_start, seg_end, wh in _split_delta_by_cuts(a.ts, b.ts, delta_wh):
            if wh <= 0:
                continue
            mid = seg_start + (seg_end - seg_start) / 2
            p = period_at(mid.time(), periods)
            label = f"{p.start:%H:%M}-{p.end:%H:%M}"
            if merged and merged[-1]["period"] == label and merged[-1]["_end"] == seg_start:
                merged[-1]["wh"] += wh
                merged[-1]["_end"] = seg_end
            else:
                merged.append(
                    {"period": label, "price_cents_per_kwh": p.price_cents_per_kwh, "wh": wh, "_end": seg_end}
                )
    for m in merged:
        m.pop("_end", None)
    return merged


def compute_bill(
    samples: list[Sample],
    periods: list[TOUPeriod],
    service_fee_cents_per_kwh: int,
    occupy_fee_cents: int = 0,
) -> dict:
    segments = compute_energy_segments(samples, periods)
    total_wh = sum(s["wh"] for s in segments)

    # 毫厘（millicents）：fee_cents = wh * price / 1000，故 wh*price 即毫厘
    for s in segments:
        s["energy_fee_millicents"] = s["wh"] * s["price_cents_per_kwh"]
        s["service_fee_millicents"] = s["wh"] * service_fee_cents_per_kwh

    energy_mc = sum(s["energy_fee_millicents"] for s in segments)
    energy_cents = _round_half_up(energy_mc / 1000)
    service_cents = _round_half_up(total_wh * service_fee_cents_per_kwh / 1000)

    for s in segments:
        s["energy_fee_cents"] = _round_half_up(s["energy_fee_millicents"] / 1000)

    # 残差归属：最大段吸收四舍五入差，保证「分段加总 == 总额」
    if segments:
        diff = energy_cents - sum(s["energy_fee_cents"] for s in segments)
        max(segments, key=lambda s: s["wh"])["energy_fee_cents"] += diff

    return {
        "segments": segments,
        "total_wh": total_wh,
        "energy_fee_cents": energy_cents,
        "service_fee_cents": service_cents,
        "occupy_fee_cents": occupy_fee_cents,
        "total_cents": energy_cents + service_cents + occupy_fee_cents,
    }


def parse_periods(policy_periods: list[dict]) -> list[TOUPeriod]:
    out = []
    for p in policy_periods:
        sh, sm = p["start"].split(":")
        eh, em = p["end"].split(":")
        eh_i = int(eh) % 24  # 24:00 → 00:00，与 start 构成跨午夜段
        out.append(TOUPeriod(time(int(sh), int(sm)), time(eh_i, int(em)), int(p["price_cents"])))
    return out


def compute_occupy_fee(full_at: datetime, unplug_at: datetime, free_minutes: int, cents_per_min: int) -> int:
    minutes = int((unplug_at - full_at).total_seconds() // 60)
    return max(0, minutes - free_minutes) * cents_per_min
