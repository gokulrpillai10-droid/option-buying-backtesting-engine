"""Instrument metadata the engine (not the strategy) owns.

NIFTY lot size is not a constant. NSE revised it several times and the history is
NON-monotone -- 75 (2020) -> 50 (2021-05) -> 25 (2024-05) -> 75 (2025-01) ->
65 (2026-01) -- and during a transition weekly and monthly contracts can carry
DIFFERENT lots. A single hardcoded number is therefore structurally wrong.

The authoritative source is the per-expiry ``LotSize`` in the Data-v3 EXPIRIES
table. ``LotTable`` loads it, and the Backtest driver sizes each order by the lot
of the EXPIRY it actually trades (``MarketData.lot_size``).

``lot_size_for(day)`` remains as a day-keyed approximation -- the front-weekly lot
in effect -- for standalone callers and as the driver's fallback when no per-expiry
table is available (e.g. synthetic tests). The default schedule is derived from the
EXPIRIES data (weekly regimes); its transition dates are the expiry dates at which
the weekly lot changed, so it can be off by up to a week right at a boundary. Use
``LotTable`` when exactness matters.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Tuple

# Front-weekly lot regimes, derived from Data-v3 EXPIRIES.LotSize.
# (effective_date, lot_size), ascending. Approximation of the exact per-expiry table.
NIFTY_LOT_SCHEDULE: List[Tuple[date, int]] = [
    (date(1900, 1, 1), 75),   # 2020-01 .. 2021-04
    (date(2021, 5, 6), 50),   # 2021-05 .. 2024-05
    (date(2024, 5, 9), 25),   # 2024-05 .. 2024-12
    (date(2025, 1, 2), 75),   # 2025-01 .. 2025-12
    (date(2026, 1, 6), 65),   # 2026-01 ..
]


def _as_date(x) -> date:
    """Coerce a date/datetime/Timestamp/str to a plain ``date``."""
    if isinstance(x, date) and not hasattr(x, "hour"):
        return x
    import pandas as pd
    return pd.Timestamp(x).date()


def lot_size_for(d: date, schedule: List[Tuple[date, int]] = NIFTY_LOT_SCHEDULE) -> int:
    """Front-weekly lot size in effect on ``d`` (latest schedule entry with
    effective_date <= d). Day-keyed approximation; the exact per-expiry lot lives
    in ``LotTable``."""
    size = schedule[0][1]
    for eff, s in schedule:
        if d >= eff:
            size = s
        else:
            break
    return size


class LotTable:
    """Authoritative per-expiry lot sizes (from the Data-v3 EXPIRIES.LotSize column)."""

    def __init__(self, by_expiry: Dict[date, int]):
        self._by = dict(by_expiry)
        self._sorted = sorted(self._by)          # ascending expiry dates

    @classmethod
    def from_frame(cls, df) -> "LotTable":
        """Build from an EXPIRIES frame with ``Expiry`` and ``LotSize`` columns."""
        sub = df[["Expiry", "LotSize"]].dropna()
        by = {_as_date(e): int(l) for e, l in zip(sub["Expiry"], sub["LotSize"])}
        return cls(by)

    def for_expiry(self, expiry) -> Optional[int]:
        """Exact lot for ``expiry``; if that expiry is absent, the lot of the
        nearest EARLIER known expiry (the regime in force). None if the table is
        empty or ``expiry`` precedes every known expiry."""
        expiry = _as_date(expiry)
        if expiry in self._by:
            return self._by[expiry]
        prior = [e for e in self._sorted if e <= expiry]
        return self._by[prior[-1]] if prior else None

    def for_day(self, day) -> Optional[int]:
        """Lot a front-trading strategy uses on ``day``: the lot of the nearest
        expiry on/after ``day`` (the front contract). Falls back to the last known
        expiry's lot when ``day`` is past the table."""
        day = _as_date(day)
        ahead = [e for e in self._sorted if e >= day]
        if ahead:
            return self._by[ahead[0]]
        return self._by[self._sorted[-1]] if self._sorted else None
