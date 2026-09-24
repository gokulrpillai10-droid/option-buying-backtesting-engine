"""MarketData — expiry selection (from REAL partitions) + trading-day calendar.

Expiry selection must come from the actual expiry partitions, never a weekday rule
(the expiry weekday changed Thu->Tue ~Sep 2025). Nearest expiry with DTE>=min_dte.
Trading days come from the resident spot (the calendar).
"""
from datetime import date, datetime

import pandas as pd

import pytest

from engine.data.spot import SpotSeries
from engine.data.market import MarketData, DataFreshnessError


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


def test_quarantined_days_are_skipped_and_counted():
    cat = FakeCatalog(["2024-01-31"])
    days = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 5), date(2024, 1, 10)]
    md = MarketData(cat, _spot(days), min_dte=3, quarantine={date(2024, 1, 2)})
    assert md.trading_days(date(2024, 1, 1), date(2024, 1, 10)) == \
        [date(2024, 1, 1), date(2024, 1, 5), date(2024, 1, 10)]
    assert md.quarantined_between(date(2024, 1, 1), date(2024, 1, 10)) == 1
    assert md.quarantined_between(date(2024, 1, 3), date(2024, 1, 10)) == 0


def test_last_expiry_date_returns_max():
    cat = FakeCatalog(["2024-01-04", "2024-01-11", "2024-01-18"])
    md = MarketData(cat, _spot([date(2024, 1, 1)]))
    assert md.last_expiry_date() == date(2024, 1, 18)


def test_last_expiry_date_none_when_empty():
    cat = FakeCatalog([])
    md = MarketData(cat, _spot([date(2024, 1, 1)]))
    assert md.last_expiry_date() is None


def test_stream_raises_when_end_past_last_expiry():
    cat = FakeCatalog(["2024-01-04", "2024-01-11"])
    md = MarketData(cat, _spot([date(2024, 1, 1), date(2024, 1, 2)]))
    with pytest.raises(DataFreshnessError, match="2024-01-15"):
        list(md.stream(date(2024, 1, 1), date(2024, 1, 15)))


def test_stream_ok_when_end_equals_last_expiry():
    cat = FakeCatalog(["2024-01-11"])
    md = MarketData(cat, _spot([date(2024, 1, 1)]))
    result = list(md.stream(date(2024, 1, 1), date(2024, 1, 11)))
    assert isinstance(result, list)


def test_stream_ok_when_end_before_last_expiry():
    cat = FakeCatalog(["2024-01-11"])
    md = MarketData(cat, _spot([date(2024, 1, 1)]))
    result = list(md.stream(date(2024, 1, 1), date(2024, 1, 5)))
    assert isinstance(result, list)


def test_stream_no_raise_when_catalog_empty():
    cat = FakeCatalog([])
    md = MarketData(cat, _spot([date(2024, 1, 1)]))
    result = list(md.stream(date(2024, 1, 1), date(2024, 1, 5)))
    assert result == []


def test_load_quarantine_roundtrip(tmp_path):
    import json
    from engine.data.market import load_quarantine
    p = tmp_path / "q.json"
    p.write_text(json.dumps({"quarantined": ["2024-01-02", "2024-03-05"]}))
    assert load_quarantine(str(p)) == {date(2024, 1, 2), date(2024, 3, 5)}
