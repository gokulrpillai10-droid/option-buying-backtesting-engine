"""Kite loader: month chunking, resumability, retry/backoff, integrity clean.
All tests use a fake fetch_fn + injected sleep — no network, no kiteconnect."""
import json
from datetime import date, datetime

import pandas as pd
import pytest

from engine.equity.kite_loader import (_normalize, clean, fetch_months,
                                       load_cached, month_range)


def recs(day, n=2, base=1700.0):
    """Kite-style records: tz-aware 'date' + lowercase ohlc."""
    return [{"date": pd.Timestamp(datetime(day.year, day.month, day.day, 9, 15 + i),
                                  tz="Asia/Kolkata"),
             "open": base, "high": base + 1, "low": base - 1, "close": base,
             "volume": 1000}
            for i in range(n)]


def test_month_range_clips_to_bounds():
    assert month_range(date(2024, 1, 15), date(2024, 3, 10)) == [
        (date(2024, 1, 15), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 10)),
    ]


def test_fetch_writes_month_files_and_throttles(tmp_path):
    calls, sleeps = [], []
    def fn(token, frm, to, interval):
        calls.append((token, frm.date(), to.date(), interval))
        return recs(frm.date())
    written, skipped = fetch_months(fn, "HDFCBANK", 341249,
                                    date(2024, 1, 1), date(2024, 2, 29),
                                    cache_dir=str(tmp_path),
                                    sleep=sleeps.append, today=date(2024, 6, 1))
    assert written == 2 and skipped == 0
    assert (tmp_path / "HDFCBANK" / "2024-01.parquet").exists()
    assert (tmp_path / "HDFCBANK" / "2024-02.parquet").exists()
    assert all(c[3] == "minute" for c in calls)
    assert sleeps.count(0.35) == 2                      # throttle after each fetch


def test_resume_skips_complete_cached_months(tmp_path):
    def fn(token, frm, to, interval):
        return recs(frm.date())
    fetch_months(fn, "HDFCBANK", 341249, date(2024, 1, 1), date(2024, 1, 31),
                 cache_dir=str(tmp_path), sleep=lambda s: None,
                 today=date(2024, 6, 1))
    calls = []
    def fn2(token, frm, to, interval):
        calls.append(frm.date())
        return recs(frm.date())
    written, skipped = fetch_months(fn2, "HDFCBANK", 341249,
                                    date(2024, 1, 1), date(2024, 2, 29),
                                    cache_dir=str(tmp_path),
                                    sleep=lambda s: None, today=date(2024, 6, 1))
    assert written == 1 and skipped == 1
    assert calls == [date(2024, 2, 1)]                  # January not refetched


def test_current_month_is_always_refetched(tmp_path):
    def fn(token, frm, to, interval):
        return recs(frm.date())
    # Cache June as if fetched mid-month, then fetch again "today" = June 20.
    fetch_months(fn, "HDFCBANK", 341249, date(2024, 6, 1), date(2024, 6, 15),
                 cache_dir=str(tmp_path), sleep=lambda s: None,
                 today=date(2024, 6, 20))
    written, skipped = fetch_months(fn, "HDFCBANK", 341249,
                                    date(2024, 6, 1), date(2024, 6, 20),
                                    cache_dir=str(tmp_path),
                                    sleep=lambda s: None, today=date(2024, 6, 20))
    assert written == 1 and skipped == 0                # overwritten, not skipped


def test_retry_backoff_then_success(tmp_path):
    sleeps, attempts = [], {"n": 0}
    def flaky(token, frm, to, interval):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("kite 429")
        return recs(frm.date())
    written, _ = fetch_months(flaky, "HDFCBANK", 341249,
                              date(2024, 1, 1), date(2024, 1, 31),
                              cache_dir=str(tmp_path), sleep=sleeps.append,
                              today=date(2024, 6, 1))
    assert written == 1 and attempts["n"] == 3
    assert sleeps[:2] == [1, 2]                          # 2**0, 2**1 backoff


def test_retry_exhausted_raises(tmp_path):
    def dead(token, frm, to, interval):
        raise ConnectionError("kite down")
    with pytest.raises(ConnectionError):
        fetch_months(dead, "HDFCBANK", 341249,
                     date(2024, 1, 1), date(2024, 1, 31),
                     cache_dir=str(tmp_path), sleep=lambda s: None,
                     max_retries=3, today=date(2024, 6, 1))


def test_max_retries_zero_rejected(tmp_path):
    # max_retries=0 would fall off the retry loop returning None -> a silently
    # empty cached month; must fail loud instead, before any fetch happens.
    calls = []
    def fn(token, frm, to, interval):
        calls.append(frm)
        return recs(frm.date())
    with pytest.raises(ValueError):
        fetch_months(fn, "HDFCBANK", 341249,
                     date(2024, 1, 1), date(2024, 1, 31),
                     cache_dir=str(tmp_path), sleep=lambda s: None,
                     max_retries=0, today=date(2024, 6, 1))
    assert calls == []


def test_partial_month_refetched_once_finalized(tmp_path):
    # January first cached mid-month (through Jan 20) while it was the current
    # month -> a partial file. On a later run, once January is finalized, it must
    # be re-fetched through its end rather than frozen with the mid-month hole.
    def fn(token, frm, to, interval):
        return recs(frm.date())
    fetch_months(fn, "HDFCBANK", 341249, date(2024, 1, 1), date(2024, 1, 20),
                 cache_dir=str(tmp_path), sleep=lambda s: None,
                 today=date(2024, 1, 20))
    cov = json.loads((tmp_path / "HDFCBANK" / "_coverage.json").read_text())
    assert cov["2024-01"] == "2024-01-20"                # partial coverage recorded

    calls = []
    def fn2(token, frm, to, interval):
        calls.append(frm.date())
        return recs(frm.date())
    written, skipped = fetch_months(fn2, "HDFCBANK", 341249,
                                    date(2024, 1, 1), date(2024, 1, 31),
                                    cache_dir=str(tmp_path),
                                    sleep=lambda s: None, today=date(2024, 2, 15))
    assert written == 1 and skipped == 0                 # re-fetched, not frozen
    assert calls == [date(2024, 1, 1)]
    cov = json.loads((tmp_path / "HDFCBANK" / "_coverage.json").read_text())
    assert cov["2024-01"] == "2024-01-31"                # now covers full month

    # A third run over the same finalized, fully-covered month is skipped.
    _, skipped2 = fetch_months(fn2, "HDFCBANK", 341249,
                               date(2024, 1, 1), date(2024, 1, 31),
                               cache_dir=str(tmp_path),
                               sleep=lambda s: None, today=date(2024, 2, 15))
    assert skipped2 == 1


def test_empty_result_not_written_and_never_overwrites(tmp_path):
    # A 0-bar fetch is a transient API failure: never persist it (that would
    # cement a permanent gap) and never clobber good cached data with it.
    empty = lambda token, frm, to, interval: []
    written, _ = fetch_months(empty, "HDFCBANK", 341249,
                              date(2024, 1, 1), date(2024, 1, 31),
                              cache_dir=str(tmp_path), sleep=lambda s: None,
                              today=date(2024, 6, 1))
    assert written == 0
    assert not (tmp_path / "HDFCBANK" / "2024-01.parquet").exists()
    assert "2024-01" not in _read_cov(tmp_path)          # no gap cemented

    # Seed a good current month, then re-fetch it with an empty result.
    good = lambda token, frm, to, interval: recs(frm.date())
    fetch_months(good, "HDFCBANK", 341249, date(2024, 6, 1), date(2024, 6, 15),
                 cache_dir=str(tmp_path), sleep=lambda s: None,
                 today=date(2024, 6, 20))
    written, _ = fetch_months(empty, "HDFCBANK", 341249,
                              date(2024, 6, 1), date(2024, 6, 20),
                              cache_dir=str(tmp_path), sleep=lambda s: None,
                              today=date(2024, 6, 20))
    assert written == 0
    df = load_cached("HDFCBANK", cache_dir=str(tmp_path))  # good data survives
    assert len(df) == 2


def test_legacy_parquet_without_coverage_is_migrated_and_skipped(tmp_path):
    # A finalized month left on disk by an older loader (no _coverage.json entry)
    # is trusted as complete: recorded into the manifest and skipped, not refetched.
    outdir = tmp_path / "HDFCBANK"
    outdir.mkdir(parents=True)
    legacy = clean(_normalize(recs(date(2024, 1, 10))))[0]
    legacy.to_parquet(outdir / "2024-01.parquet", index=False)
    assert not (outdir / "_coverage.json").exists()

    calls = []
    def fn(token, frm, to, interval):
        calls.append(frm.date())
        return recs(frm.date())
    written, skipped = fetch_months(fn, "HDFCBANK", 341249,
                                    date(2024, 1, 1), date(2024, 1, 31),
                                    cache_dir=str(tmp_path),
                                    sleep=lambda s: None, today=date(2024, 6, 1))
    assert written == 0 and skipped == 1 and calls == []  # trusted, not refetched
    assert _read_cov(tmp_path)["2024-01"] == "2024-01-31"  # migrated into manifest


def _read_cov(tmp_path):
    return json.loads((tmp_path / "HDFCBANK" / "_coverage.json").read_text())


def test_clean_drops_bad_bars_and_dedupes():
    t = pd.Timestamp("2024-01-01 09:15")
    df = pd.DataFrame([
        {"DateTime": t, "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5},
        {"DateTime": t, "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5},  # dup
        {"DateTime": t + pd.Timedelta(minutes=1),
         "Open": 100.0, "High": 99.5, "Low": 99.0, "Close": 100.0},   # High < Open
        {"DateTime": t + pd.Timedelta(minutes=2),
         "Open": 0.0, "High": 1.0, "Low": 0.0, "Close": 1.0},         # zero price
        {"DateTime": t + pd.Timedelta(minutes=3),
         "Open": 100.0, "High": 101.0, "Low": 100.5, "Close": 100.6}, # Low > min(O,C)
    ])
    out, dropped = clean(df)
    assert len(out) == 1 and dropped == 3
    assert out.iloc[0]["DateTime"] == t


def test_load_cached_concats_sorted(tmp_path):
    def fn(token, frm, to, interval):
        return recs(frm.date())
    fetch_months(fn, "HDFCBANK", 341249, date(2024, 1, 1), date(2024, 2, 29),
                 cache_dir=str(tmp_path), sleep=lambda s: None,
                 today=date(2024, 6, 1))
    df = load_cached("HDFCBANK", cache_dir=str(tmp_path))
    assert list(df.columns) == ["DateTime", "Open", "High", "Low", "Close"]
    assert df["DateTime"].is_monotonic_increasing
    assert df["DateTime"].dt.tz is None                  # naive IST
    assert len(df) == 4                                  # 2 bars x 2 months
