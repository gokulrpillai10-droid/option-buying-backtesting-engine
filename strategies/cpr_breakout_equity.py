"""CPR (Central Pivot Range) breakout on cash equity — HDFCBANK v1.

Same prior-day CPR band and cross-through entry as strategies/cpr_breakout.py
(the walk-forward-validated options winner), ported off options: an up-cross
buys shares, a down-cross short-sells (intraday MIS). Deliberately RAW per the
2026-07-03 spec — no IV gate (no per-stock IV in the store), no scale-out or
premium trail (those were calibrated in option-premium points): single
close-beyond-band stop + single fixed price-point target + EOD square-off.

    Pivot = (H + L + C) / 3 ;  BC = (H + L) / 2 ;  TC = 2*Pivot - BC
    top = max(TC, BC) ;  bottom = min(TC, BC)

Entry (one breakout per day, 5-min): a candle CROSSES a band edge and CLOSES
beyond it — the low/high must touch the line, rejecting gaps that open already
outside the CPR.

Exits: stop_mode "far" (opposite edge, default) / "broken" (edge just crossed)
/ "none"; target_pts price points from entry (None = no target — the sweep
baseline); square-off at ``square``.
"""
from datetime import time

from engine.equity.model import EquityOrder
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit


class CPRBreakoutEquity(Strategy):
    def __init__(self, target_pts=10.0, stop_mode="far", capital=100_000.0,
                 entry_cutoff=time(14, 45), square=time(15, 15)):
        self.TARGET_PTS = target_pts   # price points; None = stop/EOD only
        self.stop_mode = stop_mode     # "far" | "broken" | "none"
        self.CAPITAL = capital         # fixed rupees per trade (spec: 100k default)
        self.ENTRY_CUTOFF, self.SQUARE = entry_cutoff, square

    def on_day_start(self, ctx):
        self.active = False
        self.entered = False
        self.stop_close = None
        hlc = ctx.prev_day_hlc()
        if hlc is None:
            return
        h, l, c = hlc
        pivot = (h + l + c) / 3.0
        bc = (h + l) / 2.0
        tc = 2.0 * pivot - bc
        self.top, self.bottom = max(tc, bc), min(tc, bc)
        self.active = True

    def on_bar(self, ctx):
        if not self.active or self.entered:
            return []
        if ctx.now.time() >= self.ENTRY_CUTOFF:
            return []
        bar = ctx.spot_bar
        direction = None
        if bar.low <= self.top and bar.close > self.top:            # crossed UP
            direction = "long"
        elif bar.high >= self.bottom and bar.close < self.bottom:   # crossed DOWN
            direction = "short"
        if direction is None:
            return []
        self.entered = True
        # close-based CPR stop level, same semantics as the options version
        if self.stop_mode == "none":
            self.stop_close = -1e18 if direction == "long" else 1e18
        elif direction == "long":
            self.stop_close = self.bottom if self.stop_mode == "far" else self.top
        else:
            self.stop_close = self.top if self.stop_mode == "far" else self.bottom
        return [EquityOrder(direction=direction, capital=self.CAPITAL)]

    def manage(self, ctx, pos):
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        close = ctx.spot_bar.close
        if pos.direction == "long" and close < self.stop_close:    # CPR-close stop
            return Exit.market("cpr_stop")
        if pos.direction == "short" and close > self.stop_close:
            return Exit.market("cpr_stop")
        if self.TARGET_PTS is None:
            return None
        if pos.direction == "long":
            return Exit.at(pos.entry + self.TARGET_PTS, "rise", "target")
        return Exit.at(pos.entry - self.TARGET_PTS, "fall", "target")
