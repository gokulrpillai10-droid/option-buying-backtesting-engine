"""Shared per-day chain-snapshot plumbing (DRY home for the OI + straddle studies).

Extracted from ``regime_straddle_pnl`` so the OI triage and the straddle
thermometer resolve an end-of-day option-chain snapshot the same way. Tenor
(front-weekly vs min-DTE-held) is the caller's ``MarketData(min_dte=...)`` choice:
``min_dte=0`` keeps the pin window (nearest expiry on/after the day, incl. expiry
day); the quarantine set is honored by ``MarketData`` itself.
"""
from __future__ import annotations

from datetime import date
from typing import Optional, Tuple

import pandas as pd

from engine.data.chain import Chain


def eod_snapshot(day_df: pd.DataFrame):
    """Snapshot at the session's last minute for a single-day chain frame, or
    ``None`` if the frame is empty."""
    if day_df.empty:
        return None
    eod = day_df["DateTime"].max()
    return Chain(day_df).snapshot_at(eod)


def front_weekly_eod_snapshot(day: date, md, cat, cache: dict
                              ) -> Tuple[object, Optional[date]]:
    """(EOD snapshot, expiry) for ``day`` on the expiry ``md`` selects for it
    (min_dte drives the tenor). ``cache`` maps expiry-iso -> loaded frame so each
    per-expiry parquet is read once. Returns ``(None, None)`` when there is no
    expiry, no parquet, or no data for that day."""
    E = md.expiry_for(day)
    if E is None:
        return None, None
    e_iso = E.isoformat()
    path = cat.path_for(e_iso)
    if path is None:
        return None, None
    if e_iso not in cache:
        df = pd.read_parquet(path)
        df["DateTime"] = pd.to_datetime(df["DateTime"])
        df["_d"] = df["DateTime"].dt.date
        cache[e_iso] = df
    df = cache[e_iso]
    day_df = df[df["_d"] == day].drop(columns="_d")
    snap = eod_snapshot(day_df)
    return (snap, E) if snap is not None else (None, None)
