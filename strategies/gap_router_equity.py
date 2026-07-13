"""Two-checkpoint gap router on HDFCBANK cash equity.

09:15: small unconfirmed gaps fade toward prior close immediately; big/aligned
gaps defer. 09:30: deferred gaps that reverted fade; those still driving hard
(and big or NIFTY-aligned) go with the gap. One trade/day, square off at EOD.

Exits: FADE targets prior close (resting level) with a stop when a bar CLOSES
beyond the opening-range extreme (active only once the 09:15-09:30 range is
complete). GO holds to EOD with the same opening-range stop, no fixed target.
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
        self.pc = ctx.prev_close()
        if self.pc is None:
            return
        self.open = ctx.spot_bar.open
        self.gap = compute_gap(self.open, self.pc)
        self.ngap = self.nifty.gap_for(ctx.now.date())
        self.active = True
        d = decide_open(self.gap, self.ngap, self.thr)
        if d.branch == "none":
            self.active = False
        elif d.branch == "fade":
            self.pending = ("fade", d.side)      # open fade -> emit at first bar
        # "defer" -> handled at 09:30 in on_bar

    def on_bar(self, ctx):
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
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        # opening-range stop is live only once the 09:15-OR_END range is complete
        orng = ctx.opening_range(self.OR_END) if ctx.now.time() >= self.OR_END else None
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
