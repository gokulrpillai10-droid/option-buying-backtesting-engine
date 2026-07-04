"""Tests for SpotSeries -- look-ahead-safe spot OHLC for signals.

Signals must read the real index (spot), not the option-derived Forward. This
wraps a spot bar series and only ever exposes data as-of a requested minute, so a
strategy physically cannot read future bars when deciding an entry.
"""
from datetime import datetime

import pandas as pd

from engine.data.spot import SpotSeries, SpotBar, load_spot_csv


def spot_df():
    rows = [
        ("2024-01-01 09:15", 100.0, 105.0, 98.0, 102.0),
        ("2024-01-01 09:20", 102.0, 108.0, 101.0, 107.0),
        ("2024-01-01 09:25", 107.0, 110.0, 104.0, 105.0),
        ("2024-01-01 09:30", 105.0, 106.0, 100.0, 101.0),
    ]
    return pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]).assign(
        DateTime=lambda d: pd.to_datetime(d["DateTime"]))


def test_bar_at_returns_ohlc():
    s = SpotSeries(spot_df())
    bar = s.bar_at(datetime(2024, 1, 1, 9, 20))
    assert isinstance(bar, SpotBar)
    assert (bar.open, bar.high, bar.low, bar.close) == (102.0, 108.0, 101.0, 107.0)


def test_bar_at_missing_minute_is_none():
    s = SpotSeries(spot_df())
    assert s.bar_at(datetime(2024, 1, 1, 9, 17)) is None


def test_price_at_is_the_close():
    s = SpotSeries(spot_df())
    assert s.price_at(datetime(2024, 1, 1, 9, 25)) == 105.0


def test_history_excludes_future_bars():
    s = SpotSeries(spot_df())
    hist = s.history(datetime(2024, 1, 1, 9, 20))
    assert [b.close for b in hist] == [102.0, 107.0]   # 09:15, 09:20 only


def test_history_is_empty_before_session():
    s = SpotSeries(spot_df())
    assert s.history(datetime(2024, 1, 1, 9, 10)) == []


def test_close_before_returns_prior_session_close():
    rows = [
        ("2024-01-01 09:15", 100.0, 100, 100, 100.0),
        ("2024-01-01 09:20", 100.0, 100, 100, 102.0),    # prior session last close
        ("2024-01-02 09:15", 200.0, 200, 200, 200.0),
    ]
    s = SpotSeries(pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"])
                   .assign(DateTime=lambda d: pd.to_datetime(d["DateTime"])))
    assert s.close_before(datetime(2024, 1, 2, 9, 15)) == 102.0   # yesterday's close
    assert s.close_before(datetime(2024, 1, 1, 9, 15)) is None     # no prior session


def test_prev_day_hlc_returns_prior_session_high_low_close():
    rows = [
        ("2024-01-01 09:15", 100.0, 120.0, 90.0, 110.0),     # prior day: H120 L90
        ("2024-01-01 09:20", 110.0, 115.0, 95.0, 112.0),     # prior day last close 112
        ("2024-01-02 09:15", 200.0, 205.0, 198.0, 200.0),    # today
    ]
    s = SpotSeries(pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"])
                   .assign(DateTime=lambda d: pd.to_datetime(d["DateTime"])))
    assert s.prev_day_hlc(datetime(2024, 1, 2, 9, 15)) == (120.0, 90.0, 112.0)
    assert s.prev_day_hlc(datetime(2024, 1, 1, 9, 15)) is None   # no prior session


def test_load_spot_csv_from_kite_format(tmp_path):
    # mirrors nifty_5min_data.csv (lowercase cols, tz-aware timestamps)
    p = tmp_path / "spot.csv"
    p.write_text(
        "date,open,high,low,close,volume\n"
        "2024-01-01 09:15:00+05:30,100,105,98,102,0\n"
        "2024-01-01 09:20:00+05:30,102,108,101,107,0\n")
    s = load_spot_csv(str(p))
    assert isinstance(s, SpotSeries)
    bar = s.bar_at(datetime(2024, 1, 1, 9, 20))   # tz stripped to naive
    assert bar is not None and bar.close == 107.0
