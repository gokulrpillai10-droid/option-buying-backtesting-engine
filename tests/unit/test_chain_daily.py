"""Shared per-day chain-snapshot plumbing (DRY extraction from regime_straddle_pnl).

Pins the behaviour the straddle + OI studies both depend on: an empty day frame
yields None, and a populated one snapshots the session's LAST minute (EOD).
"""
import pandas as pd

import analysis.chain_daily as cd


def test_eod_snapshot_none_on_empty_frame():
    assert cd.eod_snapshot(pd.DataFrame({"DateTime": []})) is None


def test_eod_snapshot_uses_the_last_minute(monkeypatch):
    seen = {}

    class _FakeChain:
        def __init__(self, df):
            seen["df_rows"] = len(df)

        def snapshot_at(self, dt):
            seen["dt"] = dt
            return ("snap", dt)

    monkeypatch.setattr(cd, "Chain", _FakeChain)
    df = pd.DataFrame({"DateTime": pd.to_datetime(
        ["2023-01-02 09:20", "2023-01-02 15:29", "2023-01-02 11:00"])})
    snap = cd.eod_snapshot(df)
    # snapshot taken at the max DateTime (EOD), not first/arbitrary
    assert seen["dt"] == df["DateTime"].max()
    assert snap == ("snap", df["DateTime"].max())
