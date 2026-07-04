"""Date-keyed NIFTY lot size (engine-owned, never strategy-supplied).

The lot size changed 75 -> 65 on 2025-12-30. If a backtest uses a constant, every
2026 trade is off by ~15% on size and absolute P&L (outside-voice P1-4). The schedule
is configurable so the true multi-year history can be extended; the default encodes the
boundary the validated scripts used.
"""
from datetime import date

from engine.instruments import lot_size_for


def test_lot_size_before_and_after_2025_12_30():
    assert lot_size_for(date(2024, 6, 1)) == 75
    assert lot_size_for(date(2025, 12, 29)) == 75    # day before
    assert lot_size_for(date(2025, 12, 30)) == 65    # effective date
    assert lot_size_for(date(2026, 1, 15)) == 65


def test_lot_size_accepts_custom_schedule():
    sched = [(date(2020, 1, 1), 50), (date(2021, 11, 1), 75)]
    assert lot_size_for(date(2020, 6, 1), sched) == 50
    assert lot_size_for(date(2022, 1, 1), sched) == 75
