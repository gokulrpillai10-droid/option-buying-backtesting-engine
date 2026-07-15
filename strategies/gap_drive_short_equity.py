"""Gap-down continuation short on cash equity (built for ADANIENT).

Study basis (analysis/gap_breakout_study.py, 6.5y ADANIENT): a moderate
gap-down that is STILL driving below the open after the first 15 minutes
continues into the close — positive 6 of 7 years, +0.81%/trade in the
0.5-1.5% gap band with 09:30 confirmation. Crash gaps (>1.5%) bounce as often
as they continue (excluded), and reversal setups (gap reclaimed by 09:30)
have no stable edge (not traded).

Rules: gap in [-gap_max, -gap_min] vs prior close; observe 09:15-09:30; on
the last opening-range bar (label ``decide_at``, close = the 09:30 checkpoint
price) require close <= open*(1 - drive_thr); short, filling at the next bar
open (~09:30). Stop = resting level at the OR-high (the -23% worst case of
the unstopped study version is exactly what this caps). EOD square-off.
One trade/day.
"""
from datetime import time

from engine.equity.model import EquityOrder
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit


class GapDriveShortEquity(Strategy):
    def __init__(self, capital=100_000.0, qty=None,
                 gap_min=0.005, gap_max=0.015, drive_thr=0.0025,
                 decide_at=time(9, 25), or_end=time(9, 30),
                 square=time(15, 15)):
        self.CAPITAL, self.QTY = capital, qty
        self.GAP_MIN, self.GAP_MAX = gap_min, gap_max
        self.DRIVE_THR = drive_thr
        self.DECIDE_AT = decide_at          # last OR bar label (tf-dependent)
        self.OR_END, self.SQUARE = or_end, square

    def on_day_start(self, ctx):
        self.active = False
        self.traded = False
        self._or_high = None
        pc = ctx.prev_close()
        if pc is None:
            return
        self.open = ctx.spot_bar.open
        gap = self.open / pc - 1.0
        if not (-self.GAP_MAX <= gap <= -self.GAP_MIN):
            return                          # not a moderate gap-down day
        self._or_high = ctx.spot_bar.high
        self.active = True

    def on_bar(self, ctx):
        if not self.active or self.traded:
            return []
        if ctx.now.time() < self.OR_END:    # build the OR incrementally
            self._or_high = max(self._or_high, ctx.spot_bar.high)
        if ctx.now.time() == self.DECIDE_AT:
            # this bar's close IS the 09:30 checkpoint price (last 1-min close
            # before 09:30); fill lands at the next bar's ~09:30 open
            if ctx.spot_bar.close / self.open - 1.0 <= -self.DRIVE_THR:
                self.traded = True
                return [EquityOrder(direction="short", capital=self.CAPITAL,
                                    qty=self.QTY)]
            self.active = False             # gap reclaimed/drifting: no trade
        return []

    def manage(self, ctx, pos):
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        return Exit.at(self._or_high, "rise", "or_stop")
