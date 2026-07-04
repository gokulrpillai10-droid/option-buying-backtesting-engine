"""SpotSeries.from_index — first-class loader for the Data-v3 INDEX partition.

Promotes build_spot_from_index.py into the engine. Must: discover index_data.parquet
across the scattered Data folders, dedupe by DateTime, normalize timestamps to naive
IST (mixed tz-aware/naive files silently misalign otherwise), and resample 1-min ->
the strategy timeframe START-labeled (a bar labelled B covers [B, B+5min)).
"""
from datetime import datetime

import pandas as pd

from engine.data.spot import SpotSeries


def _write_index(path, rows, tz=None):
    df = pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"])
    df["DateTime"] = pd.to_datetime(df["DateTime"])
    if tz:
        df["DateTime"] = df["DateTime"].dt.tz_localize(tz)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


def _fixture_store(tmp_path):
    base = "2024-01-01 "
    a = tmp_path / "Data" / "Data-v3" / "instrument=NIFTY50" / "INDEX" / "index_data.parquet"
    b = tmp_path / "Data 2" / "Data-v3" / "instrument=NIFTY50" / "INDEX" / "index_data.parquet"
    _write_index(a, [
        (base + "09:15", 100, 101, 99, 100), (base + "09:16", 100, 102, 99, 101),
        (base + "09:17", 101, 103, 100, 102), (base + "09:18", 102, 104, 101, 103),
        (base + "09:19", 103, 105, 102, 104), (base + "09:20", 104, 106, 103, 105),
    ])
    # file B overlaps 09:18-09:20 (dup) and is tz-AWARE (Asia/Kolkata wall-clock = IST)
    _write_index(b, [
        (base + "09:18", 102, 104, 101, 103), (base + "09:19", 103, 105, 102, 104),
        (base + "09:20", 104, 106, 103, 105), (base + "09:21", 105, 107, 104, 106),
        (base + "09:22", 106, 108, 105, 107), (base + "09:23", 107, 109, 106, 108),
    ], tz="Asia/Kolkata")
    return tmp_path


def test_from_index_dedupes_tznormalizes_and_resamples_start_labeled(tmp_path):
    s = SpotSeries.from_index(str(_fixture_store(tmp_path)), timeframe="5min")
    assert isinstance(s, SpotSeries)

    # 09:15 bar covers [09:15,09:20): open=100, high=max(101..105)=105, low=min(99..102)=99, close=104
    b1 = s.bar_at(datetime(2024, 1, 1, 9, 15))   # naive lookup works -> tz was normalized
    assert (b1.open, b1.high, b1.low, b1.close) == (100.0, 105.0, 99.0, 104.0)

    # 09:20 bar covers [09:20,09:25): open=104, high=max(106..109)=109, low=min(103..106)=103, close=108
    b2 = s.bar_at(datetime(2024, 1, 1, 9, 20))
    assert (b2.open, b2.high, b2.low, b2.close) == (104.0, 109.0, 103.0, 108.0)

    # exactly two 5-min bars, no duplicate timestamps from the overlap
    ts = s.timestamps()
    assert len(ts) == 2 and len(ts) == len(set(ts))


def test_from_index_raises_when_no_parquets(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        SpotSeries.from_index(str(tmp_path), timeframe="5min")
