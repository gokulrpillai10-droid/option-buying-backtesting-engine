"""The ONLY interface an equity strategy sees each bar.

Deliberately duplicates ~6 one-line spot-forwarding methods from the options
BarContext instead of making its ``chain`` parameter optional — zero shared
mutable surface with the proven options context (a micro-decision from the
2026-07-03 spec: strictly the zero-risk option). Since cash equity has no
derivative layer, ONE SpotSeries serves as both signal feed and fill feed.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Optional, Tuple


class EquityBarContext:
    def __init__(self, now: datetime, spot):
        self._now = now
        self._spot = spot

    @property
    def now(self) -> datetime:
        return self._now

    @property
    def spot(self) -> float:
        """Current price (close of the as-of-now bar)."""
        return self._spot.price_at(self._now)

    @property
    def spot_bar(self):
        """Current OHLC bar."""
        return self._spot.bar_at(self._now)

    def spot_history(self):
        """All bars up to and including now (no future)."""
        return self._spot.history(self._now)

    def recent(self, n: int):
        """The up-to-``n`` most recent bars ending at now (rolling indicators)."""
        return self._spot.window(self._now, n)

    def prev_close(self) -> Optional[float]:
        """Prior session's closing price (gap gates)."""
        return self._spot.close_before(self._now)

    def prev_day_hlc(self):
        """Prior session's (high, low, close) — the CPR inputs."""
        return self._spot.prev_day_hlc(self._now)

    def opening_range(self, end_time: time) -> Optional[Tuple[float, float]]:
        """(high, low) of today's bars from open up to ``end_time`` (and never
        past ``now``). None before the first bar of the day."""
        today = [b for b in self._spot.history(self._now)
                 if b.dt.date() == self._now.date() and b.dt.time() <= end_time]
        if not today:
            return None
        return max(b.high for b in today), min(b.low for b in today)
