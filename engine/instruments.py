"""Instrument metadata that the engine (not the strategy) owns.

NIFTY lot size is date-keyed: a backtest that hardcodes one value is wrong across
the 2025-12-30 boundary (75 -> 65). The schedule is a sorted list of
(effective_date, lot_size); ``lot_size_for`` returns the size in effect on a date.
Default encodes the boundary the validated scripts used; pass a custom schedule to
extend the real multi-year history.
"""
from __future__ import annotations

from datetime import date
from typing import List, Tuple

# (effective_date, lot_size), ascending by date.
NIFTY_LOT_SCHEDULE: List[Tuple[date, int]] = [
    (date(1900, 1, 1), 75),
    (date(2025, 12, 30), 65),
]


def lot_size_for(d: date, schedule: List[Tuple[date, int]] = NIFTY_LOT_SCHEDULE) -> int:
    """Lot size in effect on ``d`` (the latest schedule entry with effective_date <= d)."""
    size = schedule[0][1]
    for eff, s in schedule:
        if d >= eff:
            size = s
        else:
            break
    return size
