"""Offline tests for analysis/fetch_nifty_spot.py — no Kite, no network."""
from datetime import date, datetime

import pandas as pd

from analysis.fetch_nifty_spot import NIFTY_TOKEN, fetch_and_write


def _kite_recs(day: date, n: int = 3, base: float = 22_000.0):
    return [
        {
            "date": pd.Timestamp(datetime(day.year, day.month, day.day, 9, 15 + i),
                                 tz="Asia/Kolkata"),
            "open": base, "high": base + 5, "low": base - 5, "close": base,
            "volume": 50_000,
        }
        for i in range(n)
    ]


def _mock_fetch(token, frm, to, interval):
    assert token == NIFTY_TOKEN
    assert interval == "minute"
    return _kite_recs(frm.date())


def test_new_bars_written_to_out_path(tmp_path):
    cache = tmp_path / "cache"
    out = tmp_path / "index_data.parquet"
    n = fetch_and_write(
        fetch_fn=_mock_fetch,
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
        cache_dir=str(cache),
        out_path=str(out),
        existing_end=date(2026, 2, 25),
    )
    assert n > 0
    assert out.exists()
    df = pd.read_parquet(out)
    assert list(df.columns) == ["DateTime", "Open", "High", "Low", "Close"]
    assert df["DateTime"].is_monotonic_increasing
    assert df["DateTime"].dt.tz is None                      # naive IST


def test_bars_trimmed_at_existing_end_boundary(tmp_path):
    # Inject records for Feb 25 (existing) AND Feb 26 (new).
    feb25 = date(2026, 2, 25)
    feb26 = date(2026, 2, 26)

    def fetch_two_days(token, frm, to, interval):
        return _kite_recs(feb25) + _kite_recs(feb26)

    out = tmp_path / "index_data.parquet"
    fetch_and_write(
        fetch_fn=fetch_two_days,
        start=date(2026, 2, 1),
        end=date(2026, 2, 28),
        cache_dir=str(tmp_path / "cache"),
        out_path=str(out),
        existing_end=feb25,
    )
    df = pd.read_parquet(out)
    # Feb 25 bars must be excluded; Feb 26 bars must be present.
    assert (df["DateTime"].dt.date == feb25).sum() == 0
    assert (df["DateTime"].dt.date == feb26).sum() == 3


def test_no_new_bars_returns_zero_and_no_file(tmp_path):
    # All bars fall on or before the existing end date.
    def fetch_old(token, frm, to, interval):
        return _kite_recs(date(2026, 2, 20))

    out = tmp_path / "index_data.parquet"
    n = fetch_and_write(
        fetch_fn=fetch_old,
        start=date(2026, 2, 1),
        end=date(2026, 2, 25),
        cache_dir=str(tmp_path / "cache"),
        out_path=str(out),
        existing_end=date(2026, 2, 25),
    )
    assert n == 0
    assert not out.exists()


def test_idempotent_second_run_skips_cached_months(tmp_path):
    cache = tmp_path / "cache"
    out = tmp_path / "index_data.parquet"
    calls = []

    def counting_fetch(token, frm, to, interval):
        calls.append(frm.date())
        return _kite_recs(frm.date())

    fetch_and_write(
        fetch_fn=counting_fetch,
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
        cache_dir=str(cache),
        out_path=str(out),
        existing_end=date(2026, 2, 25),
    )
    first_call_count = len(calls)

    # Second run: March is finalized and fully cached — must be skipped.
    fetch_and_write(
        fetch_fn=counting_fetch,
        start=date(2026, 3, 1),
        end=date(2026, 4, 30),           # extend end, April is new
        cache_dir=str(cache),
        out_path=str(out),
        existing_end=date(2026, 2, 25),
    )
    # March skipped; only April added a fetch call
    assert len(calls) == first_call_count + 1


def test_output_parquet_schema(tmp_path):
    out = tmp_path / "idx.parquet"
    fetch_and_write(
        fetch_fn=_mock_fetch,
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
        cache_dir=str(tmp_path / "c"),
        out_path=str(out),
        existing_end=date(2026, 2, 25),
    )
    df = pd.read_parquet(out)
    assert set(df.columns) == {"DateTime", "Open", "High", "Low", "Close"}
    assert pd.api.types.is_datetime64_dtype(df["DateTime"])
    for col in ("Open", "High", "Low", "Close"):
        assert pd.api.types.is_float_dtype(df[col])
