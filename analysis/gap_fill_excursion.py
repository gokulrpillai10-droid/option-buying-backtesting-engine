"""Pure, engine-free helpers for the gap-fill pre-study and strategy tests.

A long option's premium excursion, measured PATH-AWARE: favorable excursion is
only credited up to the point a fixed candidate stop would have fired, so the
frozen BOOK target can't be set from a peak the trade never lived to see.
"""
from __future__ import annotations

from typing import List, Tuple


def atm_strike(strikes: List[int], ref: float) -> int:
    """Strike nearest ``ref``; ties resolve to the higher strike."""
    return min(strikes, key=lambda s: (abs(s - ref), -s))


def excursion_with_stop(path: List[float], entry: float,
                        stop_pts: float) -> Tuple[float, float, bool]:
    """Walk a long-option premium ``path`` from ``entry``.

    Favorable excursion (premium - entry) is only credited on bars reached
    BEFORE the running adverse move (entry - premium) first hits ``stop_pts``.
    Adverse excursion (mae) is measured over the whole path (informational).
    Returns (mfe, mae, stopped).
    """
    mfe = 0.0
    mae = 0.0
    stopped = False
    for prem in path:
        adverse = entry - prem
        if adverse > mae:
            mae = adverse
        if not stopped:
            if adverse >= stop_pts:
                stopped = True
                continue
            fav = prem - entry
            if fav > mfe:
                mfe = fav
    return mfe, mae, stopped
