"""G12-CE as an engine Strategy (faithful port of analysis/g12_ce_corrected.py).

This is the CALIBRATION strategy used to reconcile the engine: it must reproduce
g12_ce_spot.py trade-by-trade. Deep-ITM call on a fib-bounce after an 80pt drop.

Mapping to the engine API (the split is exact because G12 PAUSES swing tracking
while in a trade -- it `continue`s past it):
  - on_day_start: reset all state.
  - on_bar (flat): swing tracking -> lock swing high on >=80pt drop -> ratchet low ->
    enter on the first 5-min candle CLOSING above the 0.44 retrace (10:00-12:15).
  - manage (in-trade): exits are SPOT-driven (t1/t2/t3 fib targets, 0.22 stop, trail
    12 prem-pts after the 1.22 extension, EOD 15:15) -> Exit.market() so the option
    fills at the bar's price (Knobs(fill_price='close') matches the script).

CORE version (no F1/F2/F3/F8/F9 filters) -- matches g12_ce_spot core run.
"""
from datetime import time

from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit

MIN_DROP, DELTA, TRAIL_PREM, LOTS = 80.0, 0.70, 12.0, 3
EA, EB, EOD = time(10, 0), time(12, 15), time(15, 15)


def _fib(hi, lo):
    r = hi - lo
    return {"f000": lo, "f022": lo + 0.22 * r, "f044": lo + 0.44 * r,
            "f077": lo + 0.77 * r, "f100": hi, "f122": lo + 1.22 * r}


def _strike(spot):
    return (int(spot) // 50) * 50 - 150


class G12CE(Strategy):
    def on_day_start(self, ctx):
        self.locked = False
        self.swing_high = -1e18
        self.running_low = 1e18
        self.swing_low = None
        self.fib = None
        self.below_044 = False
        self.tcount = 0
        self.sl_done = False
        self._reset_trade()

    def _reset_trade(self):
        self.tfib = None
        self.t1 = self.t2 = self.t3 = self.trailing = False
        self.peak = -1e18

    # ---- ENTRY (flat) ----
    def on_bar(self, ctx):
        bar = ctx.spot_bar
        h, l, c = bar.high, bar.low, bar.close
        tt = ctx.now.time()
        if tt >= EOD:
            return []

        if not self.locked:                                   # tracking: find locked swing high
            if h > self.swing_high:
                self.swing_high, self.running_low = h, l
            else:
                self.running_low = min(self.running_low, l)
            if self.swing_high - self.running_low >= MIN_DROP:
                self.locked, self.swing_low = True, self.running_low
                self.fib, self.below_044 = _fib(self.swing_high, self.swing_low), False
            return []

        if h > self.swing_high:                               # invalidate -> resume tracking
            self.locked, self.swing_high, self.running_low = False, h, l
            self.fib, self.below_044 = None, False
            return []
        if l < self.swing_low:                                # ratchet low DOWN, re-arm
            self.swing_low = l
            self.fib, self.below_044 = _fib(self.swing_high, self.swing_low), False
        if l < self.fib["f044"]:
            self.below_044 = True
        if self.sl_done or self.tcount >= 2 or not self.below_044:
            return []
        if tt < EA or tt >= EB:
            return []
        if c > self.fib["f044"]:                              # reclaim -> BUY deep-ITM call
            self.tfib = dict(self.fib)
            self.t1 = self.t2 = self.t3 = self.trailing = False
            self.peak = c
            return [Order(side="CALL", strike=_strike(c), lots=LOTS)]
        return []

    # ---- EXIT (in trade), spot-driven ----
    def manage(self, ctx, pos):
        bar = ctx.spot_bar
        h, l = bar.high, bar.low
        tt = ctx.now.time()
        if tt >= EOD:
            return Exit.market("EOD")
        f = self.tfib
        if not self.t1 and h >= f["f077"]:
            self.t1 = True
        if not self.t2 and h >= f["f100"]:
            self.t2 = True
        if not self.t3 and h >= f["f122"]:
            self.t3, self.trailing, self.peak = True, True, max(self.peak, h)
        if self.trailing:
            self.peak = max(self.peak, h)
            if l <= self.peak - TRAIL_PREM / DELTA:
                self.tcount += 1
                self.below_044 = False
                self._reset_trade()
                return Exit.market("Trail SL")
        else:
            if l <= f["f022"]:
                self.tcount += 1
                self.sl_done = True
                self._reset_trade()
                return Exit.market("Stop Loss")
        return None
