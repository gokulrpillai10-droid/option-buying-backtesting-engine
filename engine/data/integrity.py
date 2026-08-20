"""Data-integrity invariants + guarded scoped readers for option-chain frames.

The failure this prevents: a raw multi-day / multi-expiry frame filtered on an
INCOMPLETE key (e.g. time but not date) silently returns wrong-context rows, and
``.iloc[0]`` picks an arbitrary one. These helpers make that a loud, immediate
error instead of a wrong number that flows into a backtest verdict.

Hard-fail by default (a silent wrong number is worse than a stopped run); pass
``strict=False`` to downgrade any violation to a warning for exploration.

Invariant classes (see docs/backtest-integrity.md):
  HARD  — scoping (one row per (date,time,strike); one Forward per snapshot;
          call-premium monotone in strike), single-day, session bounds.
  SOFT  — cost/range sanity lives elsewhere (warn).
"""
import warnings
from datetime import time

import pandas as pd

IST_OPEN, IST_CLOSE = time(9, 15), time(15, 30)


class IntegrityError(AssertionError):
    """A data-integrity invariant was violated (hard-fail)."""


def _fail(msg: str, strict: bool):
    if strict:
        raise IntegrityError(msg)
    warnings.warn(msg, stacklevel=2)


def _times(df):
    return pd.to_datetime(df["DateTime"]).dt.time


def _dates(df):
    return pd.to_datetime(df["DateTime"]).dt.date


def check_single_day(df, strict: bool = True):
    """A day-scoped frame must contain exactly one calendar date."""
    days = pd.unique(_dates(df))
    if len(days) > 1:
        _fail(f"expected one day, got {len(days)}: {sorted(days)[:3]}...", strict)


def check_session_bounds(df, strict: bool = True):
    """Every row must fall inside the IST trading session (09:15-15:30)."""
    t = _times(df)
    bad = (t < IST_OPEN) | (t > IST_CLOSE)
    if bool(bad.any()):
        _fail(f"{int(bad.sum())} rows outside IST session {IST_OPEN}-{IST_CLOSE}", strict)


def check_snapshot(snap, strict: bool = True):
    """A single-timestamp snapshot must have each strike once and ONE Forward.
    Duplicate strikes or multiple forwards == wrong-context rows leaked in (the
    exact time-only-filter bug)."""
    dups = int(snap["StrikePrice"].duplicated().sum())
    if dups:
        _fail(f"snapshot has {dups} duplicate strike rows -> mixed context "
              f"(a leak from an incomplete date/time filter)", strict)
    if "Forward" in snap.columns and snap["Forward"].nunique(dropna=True) > 1:
        _fail(f"snapshot has {snap['Forward'].nunique()} distinct Forward values "
              f"-> mixed timestamps/days", strict)


def check_call_monotone(snap, strict: bool = True, min_strikes: int = 5, max_corr: float = -0.3):
    """Within a real snapshot, call premium falls as strike rises. A mixed-context
    snapshot loses that (flat/positive correlation). Hard-fail only on a GROSS
    violation (corr(strike, call) > max_corr) so bid/ask noise doesn't false-trip.
    Calibrated on 125 real snapshots (corr min -0.96, mean -0.72); -0.3 leaves
    wide margin against legitimate near-expiry curve flattening while still
    flagging the ~0 correlation of mixed-context data."""
    if "CallClose" not in snap.columns:
        return
    t = snap
    if "CallIsTradedStrike" in t.columns:
        t = t[t["CallIsTradedStrike"].astype(bool)]
    t = t[["StrikePrice", "CallClose"]].dropna()
    if len(t) < min_strikes or t["CallClose"].nunique() < 2 or t["StrikePrice"].nunique() < 2:
        return
    corr = t["StrikePrice"].corr(t["CallClose"])
    if pd.isna(corr) or corr > max_corr:
        _fail(f"call premium not monotone-decreasing in strike (corr={corr:.2f} "
              f"> {max_corr}) -> likely mixed-context snapshot", strict)


def assert_unique_strikes(frame, strict: bool = True):
    """Cheap hot-path guard for a single-timestamp chain frame: a repeated strike
    means mixed-context rows leaked in (and ``set_index('StrikePrice')`` would make
    ``.loc[strike]`` return several rows). Called on every snapshot construction."""
    if frame.empty or "StrikePrice" not in frame.columns:
        return
    dups = int(frame["StrikePrice"].duplicated().sum())
    if dups:
        _fail(f"chain snapshot has {dups} duplicate strike rows -> mixed context "
              f"(incomplete date/time scoping)", strict)


def one_row(frame, strict: bool = True, **where):
    """Return the single row matching ``where`` (e.g. StrikePrice=21700), or fail.
    Replaces the dangerous ``frame[mask].iloc[0]`` that silently takes row 0."""
    mask = pd.Series(True, index=frame.index)
    for k, v in where.items():
        mask &= (frame[k] == v)
    sub = frame[mask]
    if len(sub) != 1:
        _fail(f"expected exactly 1 row for {where}, got {len(sub)}", strict)
        if len(sub) == 0:
            return None
    return sub.iloc[0]


def at_most_one_row(frame, strict: bool = True, **where):
    """Return the single row matching ``where``, or None if there are none. Hard-fail
    only on >1 (a duplicate == mixed-context leak). For optional lookups where a
    missing strike/timestamp is legitimate (unlike ``one_row`` which requires one)."""
    mask = pd.Series(True, index=frame.index)
    for k, v in where.items():
        mask &= (frame[k] == v)
    sub = frame[mask]
    if len(sub) > 1:
        _fail(f"expected <=1 row for {where}, got {len(sub)}", strict)
    return sub.iloc[0] if len(sub) >= 1 else None


def scoped_day(source, date, strict: bool = True) -> pd.DataFrame:
    """Read/scope an option-chain source to a SINGLE calendar day, with invariants.
    ``source`` is a parquet path or an already-loaded DataFrame. This is the one
    sanctioned way to go from a whole-contract parquet to a day frame."""
    df = source if isinstance(source, pd.DataFrame) else pd.read_parquet(source)
    if not pd.api.types.is_datetime64_any_dtype(df["DateTime"]):
        df = df.copy()                                    # only copy when we must reparse
        df["DateTime"] = pd.to_datetime(df["DateTime"])
    day = df[df["DateTime"].dt.date == pd.Timestamp(date).date()]
    check_single_day(day, strict)
    check_session_bounds(day, strict)
    return day


def snapshot_at(day_df, t: time, strict: bool = True) -> pd.DataFrame:
    """The single-timestamp snapshot at time ``t`` from a day frame, with invariants
    (no duplicate strikes, one forward, monotone calls)."""
    snap = day_df[_times(day_df) == t]
    check_snapshot(snap, strict)
    check_call_monotone(snap, strict)
    return snap
