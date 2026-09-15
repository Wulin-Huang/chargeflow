"""有序充电公平份额分配算法（water-filling）单测。

不变量：
  1. 不超容时零干预（原样返回）；
  2. 超容时分配总和 ≤ 容量，且尽量贴近容量（不浪费台区）；
  3. 需求低的桩不劣于需求高的桩（无嫉妒）；
  4. 边界：空输入、零容量、单桩。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.loadbalancer import fair_share


def test_no_intervention_when_under_capacity():
    assert fair_share([60, 30], 200) == [60, 30]


def test_zero_intervention_exactly_at_capacity():
    assert fair_share([100, 50], 150) == [100, 50]


def test_sum_within_capacity_when_over():
    demands = [120, 100, 80, 40]
    cap = 180
    shares = fair_share(demands, cap)
    assert sum(shares) <= cap + 1e-9
    assert sum(shares) > cap * 0.95  # 容量充分利用


def test_equal_share_among_equal_demands():
    shares = fair_share([100, 100, 100], 150)
    assert shares == [50, 50, 50]


def test_small_demand_fully_served():
    # 需求 30 的桩全额满足，剩余 120 平摊给两个 100kW 桩
    shares = fair_share([100, 30, 100], 150)
    assert shares[1] == 30
    assert abs(shares[0] - 60) < 1e-9
    assert abs(shares[2] - 60) < 1e-9


def test_envy_freeness():
    demands = [120, 80, 60, 20]
    shares = fair_share(demands, 100)
    # 需求小的分配不应多于需求大的
    for i, j in zip(range(len(demands)), range(1, len(demands))):
        assert shares[i] >= shares[j] - 1e-9 or demands[i] <= shares[i] + 1e-9


def test_empty_and_zero_capacity():
    assert fair_share([], 100) == []
    assert fair_share([50, 60], 0) == [0.0, 0.0]


def test_single_pile_capped():
    assert fair_share([150], 100) == [100]
