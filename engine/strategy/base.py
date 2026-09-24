"""Strategy base class and the BarContext it sees.

``BarContext`` is the ONLY interface a strategy gets each minute. It is built
with the full day's forward series but immediately truncates to ``<= now`` so a
strategy physically cannot read future bars through it (structural look-ahead
prevention). Strike selection also runs through it, against the as-of-now chain
snapshot, honouring tradeability.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import List, Tuple

import pandas as pd

from engine.data.chain import Chain


class BarContext:
    def __init__(self, now: datetime, chain: Chain, spot=None, forward: pd.Series = None):
        self._now = now
        self._chain = chain
        self._spot = spot
        # truncate to <= now so future bars are not even held
        self._fwd = forward[forward.index <= pd.Timestamp(now)] if forward is not None else None

    @property
    def now(self) -> datetime:
        return self._now

    @property
    def forward(self) -> float:
        return float(self._fwd.iloc[-1])

    # ---- spot (the accurate signal underlying) ----
    @property
    def spot(self) -> float:
        """Current spot price (close of the as-of-now bar)."""
        return self._spot.price_at(self._now)

    @property
    def spot_bar(self):
        """Current spot OHLC bar."""
        return self._spot.bar_at(self._now)

    def spot_history(self):
        """All spot bars up to and including now (no future)."""
        return self._spot.history(self._now)

    def recent(self, n: int):
        """The up-to-``n`` most recent spot bars ending at now (for rolling indicators)."""
        return self._spot.window(self._now, n)

    def prev_close(self):
        """Prior session's closing spot (for gap gates)."""
        return self._spot.close_before(self._now)

    def prev_day_hlc(self):
        """Prior session's (high, low, close) -- the CPR inputs."""
        return self._spot.prev_day_hlc(self._now)

    def _underlying(self) -> float:
        """The price strategies trade off: spot if wired, else legacy forward."""
        return self.spot if self._spot is not None else self.forward

    def opening_range(self, end_time: time) -> Tuple[float, float]:
        """(high, low) of the forward from session start up to ``end_time``
        (and never past ``now``)."""
        window = self._fwd[self._fwd.index.time <= end_time]
        return float(window.max()), float(window.min())

    def snapshot(self):
        return self._chain.snapshot_at(self._now)

    def atm_strike(self, side: str) -> int:
        """Nearest *traded* strike to the current underlying for ``side``."""
        snap = self.snapshot()
        ref = self._underlying()
        best, best_dist = None, None
        for strike in snap.strikes():
            quote = snap.call(strike) if side.upper() == "CALL" else snap.put(strike)
            if not quote.is_traded:
                continue
            dist = abs(strike - ref)
            if best_dist is None or dist < best_dist:
                best, best_dist = strike, dist
        return best


class Strategy:
    """Base class. Subclasses implement ``on_bar``; the rest are optional hooks.

    ``on_day_start`` clears intraday state each day (the engine is intraday-only);
    ``manage`` decides exits for an open position each bar.

    Fill lifecycle hooks (both default to no-op):
    - ``on_fill(order)`` — engine confirmed the fill; set _entered here, not in on_bar.
    - ``on_order_rejected(order, reason)`` — engine could not fill (``reason`` is
      ``"no_quote"`` when the fill bar is absent, ``"volume"`` when the signal-bar
      volume gate fails, ``"no_quote_spread"`` when a spread leg has no price).
    """

    def on_day_start(self, ctx: BarContext) -> None:
        pass

    def on_bar(self, ctx: BarContext) -> List:
        raise NotImplementedError

    def manage(self, ctx: BarContext, position):
        return None

    def on_fill(self, order) -> None:
        pass

    def on_order_rejected(self, order, reason: str) -> None:
        pass

    def on_eod(self, ctx: BarContext) -> List:
        return []
