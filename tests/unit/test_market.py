"""MarketData — expiry selection (from REAL partitions) + trading-day calendar.

Expiry selection must come from the actual expiry partitions, never a weekday rule
(the expiry weekday changed Thu->Tue ~Sep 2025). Nearest expiry with DTE>=min_dte.
Trading days come from the resident spot (the calendar).
"""
from datetime import date, datetime

import pandas as pd

from engine.data.spot import SpotSeries
from engine.data.market import MarketData


class FakeCatalog:
    def __init__(self, expiries):
        self._e = expiries

    def expiries(self):
        return self._e

    def path_for(self, iso):
        return None


def _spot(dates):
    rows = [(datetime.combine(d, datetime.min.time()).replace(hour=9, minute=15),
             100.0, 100.0, 100.0, 100.0) for d in dates]
    return SpotSeries(pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]))


def test_expiry_for_picks_nearest_with_min_dte():
    cat = FakeCatalog(["2024-01-04", "2024-01-11", "2024-01-18"])
    md = MarketData(cat, _spot([date(2024, 1, 1)]), min_dte=3)
    # 2024-01-01: 01-04 is 3 days away (>=3) -> 01-04
    assert md.expiry_for(date(2024, 1, 1)) == date(2024, 1, 4)
    # 2024-01-02: 01-04 is 2 days (<3) -> skip to 01-11
    assert md.expiry_for(date(2024, 1, 2)) == date(2024, 1, 11)


def test_expiry_for_none_when_no_future_expiry():
    cat = FakeCatalog(["2024-01-04"])
    md = MarketData(cat, _spot([date(2024, 1, 1)]), min_dte=3)
    assert md.expiry_for(date(2024, 1, 10)) is None


def test_trading_days_from_spot_in_range():
    cat = FakeCatalog(["2024-01-31"])
    days = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 5), date(2024, 1, 10)]
    md = MarketData(cat, _spot(days), min_dte=3)
    got = md.trading_days(date(2024, 1, 2), date(2024, 1, 5))
    assert got == [date(2024, 1, 2), date(2024, 1, 5)]
