"""分时计费引擎单测：跨段切分 / 精度守恒 / 占位费 / 跨午夜。"""

from datetime import datetime, timedelta

from app.core.billing import (
    TOUPeriod,
    compute_bill,
    compute_occupy_fee,
    compute_energy_segments,
    parse_periods,
)
from time import time as _  # noqa: F401


def periods_std():
    return parse_periods(
        [
            {"start": "00:00", "end": "08:00", "price_cents": 35},
            {"start": "08:00", "end": "17:00", "price_cents": 70},
            {"start": "17:00", "end": "21:00", "price_cents": 110},
            {"start": "21:00", "end": "24:00", "price_cents": 70},
        ]
    )


def mk_samples(start: datetime, minutes: list[int], wh_per_min: float):
    return [
        # 采样序列：cum_wh 线性增长
        type("S", (), {"ts": start + timedelta(minutes=m), "cum_wh": int(m * wh_per_min)})()
        for m in minutes
    ]


def test_cross_valley_peak_boundary():
    """16:50 开始充到 17:20（峰从 17:00 起）：应切成 平·10min + 峰·20min，电量 1:2。"""
    start = datetime(2026, 9, 10, 16, 50)
    samples = mk_samples(start, list(range(0, 31, 1)), 1000.0 / 10)  # 3kWh 总量
    segs = compute_energy_segments(samples, periods_std())
    assert len(segs) == 2, segs
    assert segs[0]["period"] == "08:00-17:00"
    assert segs[1]["period"] == "17:00-21:00"
    ratio = segs[1]["wh"] / segs[0]["wh"]
    assert 1.7 < ratio < 2.3, f"电量切分比例应为 1:2，实际 {ratio}"


def test_segments_sum_equals_total():
    """不变量：各段电量之和 == 总电量。"""
    start = datetime(2026, 9, 10, 16, 30)
    samples = mk_samples(start, list(range(0, 120, 7)), 500.0)
    segs = compute_energy_segments(samples, periods_std())
    total = samples[-1].cum_wh
    assert sum(s["wh"] for s in segs) == total


def test_bill_cents_consistency():
    """不变量：分段金额加总 == 总金额（残差被最大段吸收）。"""
    start = datetime(2026, 9, 10, 16, 55)
    samples = mk_samples(start, list(range(0, 90, 3)), 777.0)
    bill = compute_bill(samples, periods_std(), service_fee_cents_per_kwh=50)
    assert sum(s["energy_fee_cents"] for s in bill["segments"]) == bill["energy_fee_cents"]
    assert bill["total_cents"] == bill["energy_fee_cents"] + bill["service_fee_cents"] + bill["occupy_fee_cents"]


def test_midnight_wrap_period():
    """跨午夜电价策略（22:00-06:00 峰值段）。"""
    periods = parse_periods(
        [
            {"start": "06:00", "end": "22:00", "price_cents": 50},
            {"start": "22:00", "end": "06:00", "price_cents": 90},
        ]
    )
    start = datetime(2026, 9, 10, 23, 40)
    samples = mk_samples(start, list(range(0, 40, 5)), 1000.0)
    segs = compute_energy_segments(samples, periods)
    assert all(s["price_cents_per_kwh"] == 90 for s in segs)


def test_occupy_fee_free_window():
    full = datetime(2026, 9, 10, 12, 0)
    unplug = datetime(2026, 9, 10, 12, 25)
    assert compute_occupy_fee(full, unplug, 30, 50) == 0
    unplug2 = datetime(2026, 9, 10, 12, 50)
    assert compute_occupy_fee(full, unplug2, 30, 50) == 20 * 50


def test_zero_delta_ignored():
    start = datetime(2026, 9, 10, 10, 0)
    samples = [
        type("S", (), {"ts": start, "cum_wh": 100})(),
        type("S", (), {"ts": start + timedelta(minutes=1), "cum_wh": 100})(),  # 无增量
        type("S", (), {"ts": start + timedelta(minutes=2), "cum_wh": 900})(),
    ]
    segs = compute_energy_segments(samples, periods_std())
    assert sum(s["wh"] for s in segs) == 800
