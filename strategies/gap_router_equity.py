"""Two-checkpoint gap router on HDFCBANK cash equity.

09:15: small unconfirmed gaps fade toward prior close immediately; big/aligned
gaps defer. 09:30: deferred gaps that reverted fade; those still driving hard
(and big or NIFTY-aligned) go with the gap. One trade/day, square off at EOD.

Exits: FADE targets prior close (resting level) with a stop when a bar CLOSES
beyond the opening-range extreme (active only once the 09:15-09:30 range is
complete). GO holds to EOD with the same opening-range stop, no fixed target.
The opening range itself is tracked incrementally bar-by-bar (not recomputed
via a full-history scan) since it is immutable after 09:30.
"""
from datetime import time

from engine.equity.gap_features import (Thresholds, compute_gap, signed_drive,
                                        decide_open, decide_0930)
from engine.equity.model import EquityOrder
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit


class GapRouterEquity(Strategy):
    def __init__(self, nifty, thresholds=None, capital=100_000.0,
                 or_end=time(9, 30), square=time(15, 15)):
        self.nifty = nifty
        self.thr = thresholds or Thresholds()
        self.CAPITAL = capital
        self.OR_END, self.SQUARE = or_end, square

    def on_day_start(self, ctx):
        self.active = False
        self.traded = False          # one trade/day guard
        self.decided_0930 = False
        self.pending = None          # ("fade"/"go", side) to emit next on_bar
        self.branch = self.side = None
        self._or_high = self._or_low = None   # seeded below / by _track_or
        self.pc = ctx.prev_close()
        if self.pc is None:
            return
        self.open = ctx.spot_bar.open
        self._or_high = ctx.spot_bar.high      # seed with the 09:15 bar
        self._or_low = ctx.spot_bar.low
        self.gap = compute_gap(self.open, self.pc)
        self.ngap = self.nifty.gap_for(ctx.now.date())
        self.active = True
        d = decide_open(self.gap, self.ngap, self.thr)
        if d.branch == "none":
            self.active = False
        elif d.branch == "fade":
            self.pending = ("fade", d.side)      # open fade -> emit at first bar
        # "defer" -> handled at 09:30 in on_bar

    def _track_or(self, ctx):
        # SpotSeries.history() (engine/data/spot.py:141) rebuilds the ENTIRE
        # bar list via iterrows() on every call -> O(N) per bar, O(N^2) per
        # backtest. ctx.opening_range() calls history() under the hood, so
        # calling it every bar while a position is open turned a 6-year
        # backtest quadratic. The opening range is immutable after OR_END,
        # so track it incrementally off ctx.spot_bar (O(1) dict lookup)
        # instead. Do not revert to ctx.opening_range() in manage()/on_bar().
        if ctx.spot_bar is None:
            return
        if self._or_high is None:
            self._or_high, self._or_low = ctx.spot_bar.high, ctx.spot_bar.low
            return
        if ctx.now.time() <= self.OR_END:
            self._or_high = max(self._or_high, ctx.spot_bar.high)
            self._or_low = min(self._or_low, ctx.spot_bar.low)

    def on_bar(self, ctx):
        self._track_or(ctx)
        if not self.active or self.traded:
            return []
        if self.pending is not None:
            branch, side = self.pending
            self.pending = None
            return self._enter(branch, side)
        if not self.decided_0930 and ctx.now.time() >= self.OR_END:
            self.decided_0930 = True
            drive = signed_drive(ctx.spot_bar.close, self.open, self.gap)
            d = decide_0930(self.gap, self.ngap, drive, self.thr)
            if d.branch == "none":
                self.active = False
                return []
            return self._enter(d.branch, d.side)
        return []

    def _enter(self, branch, side):
        self.traded = True
        self.branch, self.side = branch, side
        return [EquityOrder(direction=side, capital=self.CAPITAL)]

    def manage(self, ctx, pos):
        self._track_or(ctx)
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        # opening-range stop is live only once the 09:15-OR_END range is complete
        orng = (self._or_high, self._or_low) if ctx.now.time() >= self.OR_END else None
        close = ctx.spot_bar.close
        if orng is not None:
            or_high, or_low = orng
            if pos.direction == "long" and close < or_low:
                return Exit.market("or_stop")
            if pos.direction == "short" and close > or_high:
                return Exit.market("or_stop")
        if self.branch == "fade":
            # target = prior close (the gap fill)
            if pos.direction == "long":
                return Exit.at(self.pc, "rise", "fill")
            return Exit.at(self.pc, "fall", "fill")
        return None                              # GO: hold to EOD / stop only
