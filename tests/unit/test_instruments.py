"""Lot sizing — the day-keyed front-weekly schedule (approximation) and the
authoritative per-expiry LotTable (from Data-v3 EXPIRIES.LotSize).

The NIFTY lot history is non-monotone: 75 (2020) -> 50 (2021-05) -> 25 (2024-05)
-> 75 (2025-01) -> 65 (2026-01), and weekly/monthly contracts diverge during
transitions. See docs/backtest-integrity.md.
"""
from datetime import date

import pandas as pd

from engine.instruments import lot_size_for, LotTable


def test_front_weekly_schedule_matches_expiries_regimes():
    # regimes derived from Data-v3 EXPIRIES.LotSize (weekly), asserted mid-regime
    assert lot_size_for(date(2020, 6, 1)) == 75
    assert lot_size_for(date(2021, 4, 1)) == 75      # before the 2021-05 cut
    assert lot_size_for(date(2021, 6, 1)) == 50
    assert lot_size_for(date(2024, 3, 1)) == 50
    assert lot_size_for(date(2024, 7, 1)) == 25      # 2024-05 -> 25
    assert lot_size_for(date(2024, 12, 1)) == 25
    assert lot_size_for(date(2025, 3, 1)) == 75      # 2025-01 -> 75
    assert lot_size_for(date(2025, 12, 29)) == 75
    assert lot_size_for(date(2026, 2, 1)) == 65      # 2026-01 -> 65


def test_lot_size_accepts_custom_schedule():
    sched = [(date(1900, 1, 1), 50), (date(2021, 1, 1), 75)]
    assert lot_size_for(date(2020, 6, 1), sched) == 50
    assert lot_size_for(date(2022, 1, 1), sched) == 75


def test_lottable_exact_per_expiry():
    df = pd.DataFrame({
        "Expiry": pd.to_datetime(["2024-05-02", "2024-05-09", "2025-01-02"]),
        "LotSize": [50, 25, 75],
    })
    lt = LotTable.from_frame(df)
    assert lt.for_expiry(date(2024, 5, 2)) == 50
    assert lt.for_expiry(date(2024, 5, 9)) == 25
    assert lt.for_expiry(date(2025, 1, 2)) == 75
    # absent expiry -> nearest prior regime in force
    assert lt.for_expiry(date(2024, 5, 5)) == 50
    # before all known expiries -> None (caller falls back)
    assert lt.for_expiry(date(2020, 1, 1)) is None
    # accepts Timestamp / datetime too
    assert lt.for_expiry(pd.Timestamp("2024-05-09")) == 25


def test_lottable_for_day_uses_front_expiry():
    df = pd.DataFrame({
        "Expiry": pd.to_datetime(["2024-05-02", "2024-05-09"]),
        "LotSize": [50, 25],
    })
    lt = LotTable.from_frame(df)
    assert lt.for_day(date(2024, 4, 30)) == 50   # front is 05-02 -> 50
    assert lt.for_day(date(2024, 5, 3)) == 25    # front is 05-09 -> 25
    assert lt.for_day(date(2030, 1, 1)) == 25    # past all -> last known lot
