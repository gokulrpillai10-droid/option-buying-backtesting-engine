"""Intraday Fibonacci retracement — trend-continuation on the day's impulse leg.

Formation window (09:15-10:00) fixes the impulse leg from the session high/low;
whichever extreme prints later sets direction. CE-only: on an UP-impulse day, buy a
CALL when price retraces into the 50-61.8% zone and reclaims the 38.2% level (the
bounce). Down-impulse days are skipped (no PUT side).
Exit on a premium bracket or EOD. Look-ahead safe: every read is <= now.
"""
from datetime import time

from engine.strategy.base import Strategy
from engine.execution.model import Order
from engine.strategy.exit import Exit


class FibRetracement(Strategy):
    FORM_END = time(10, 0)          # impulse leg fixed from 09:15-10:00 bars
    ENTRY_CUTOFF = time(14, 30)     # no new entries after this
    ZONE_LO, ZONE_HI = 0.5, 0.618   # retracement zone (fraction of the leg)
    RECLAIM = 0.382                 # bounce-confirmation level
    MIN_IMPULSE = 0.003             # min leg size as a fraction of spot (skip noise days)
    STOP_PREM, TARGET_PREM = 20.0, 40.0
    LOTS = 6
    MIN_DTE = 3

    def on_day_start(self, ctx):
        self._entered = False
        self._formed = False
        self._primed = False
        self._sh = self._sl = self._dir = None

    def _form_leg(self, ctx):
        """Fix SH/SL/direction from today's pre-10:00 bars. Uses the O(n) recent
        window, NOT spot_history() — the latter materializes ALL history per call
        (the SpotSeries.history per-bar perf trap)."""
        today = ctx.now.date()
        bars = [b for b in ctx.recent(15)
                if b.dt.date() == today and b.dt.time() < self.FORM_END]
        if len(bars) < 3:
            return
        hi = max(bars, key=lambda b: b.high)
        lo = min(bars, key=lambda b: b.low)
        self._sh, self._sl = hi.high, lo.low
        self._dir = "up" if hi.dt > lo.dt else "down"     # later extreme = impulse direction
        self._formed = True

    def on_bar(self, ctx):
        if self._entered:
            return []
        t = ctx.now.time()
        if t < self.FORM_END or t > self.ENTRY_CUTOFF:
            return []
        if not self._formed:
            self._form_leg(ctx)
            if not self._formed:
                return []

        R = self._sh - self._sl
        px = ctx.spot
        if R < self.MIN_IMPULSE * px:
            return []                                     # impulse too small -> stand aside

        if self._dir != "up":
            return []                                     # CE-only: only long-continuation up-legs trade
        zone_lo, zone_hi = self._sh - self.ZONE_HI * R, self._sh - self.ZONE_LO * R
        reclaim = self._sh - self.RECLAIM * R
        if zone_lo <= px <= zone_hi:
            self._primed = True                           # retraced into the zone
        if self._primed and px > reclaim:                 # reclaimed 38.2% -> bounce confirmed
            return [Order(side="CALL", strike=ctx.atm_strike("CALL"), lots=self.LOTS)]
        return []

    def on_fill(self, order) -> None:
        self._entered = True

    def manage(self, ctx, pos):
        return Exit.bracket(stop=pos.entry - self.STOP_PREM,
                            target=pos.entry + self.TARGET_PREM)
