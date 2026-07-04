"""Gap-down continuation via DEEP-ITM PUT, with configurable exits incl. scale-out.

Thesis (unchanged): real index-level gap-down continuation edge (PF 1.36), monetized
with a near-delta-1 deep-ITM put. The v1 single-trailing exit lost on payoff asymmetry
(small wins vs bigger stops + friction). This version supports the PROVEN scale-out:
book half at +BOOK1, ride the rest for +BOOK2 then a trail -- letting the big down-days
pay for the stops.

  Gate    : open >= GAP pts below prior close.
  Trigger : break opening-range (09:15-09:29) low by 2 pts, 09:30-11:30.
  Strike  : floor(spot/50)*50 + 150 (deep-ITM put).
  Exits   : hard stop at +STOP adverse; if scale_out, book half at +BOOK1 then ride the
            rest (arm at +BOOK2, trail TRAIL); else single position armed at +BOOK1.
"""
from datetime import time

from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit

GAP, BREAK_BUF = 20.0, 2.0
OR_END, ENTRY_CUTOFF, SQUARE = time(9, 25), time(11, 30), time(15, 15)
LOTS = 3


def _put_strike(spot):
    return (int(spot) // 50) * 50 + 150


class GapDownPut(Strategy):
    def __init__(self, stop=30.0, book1=25.0, book2=50.0, trail=20.0, scale_out=True,
                 prem_trail=None):
        self.STOP, self.BOOK1, self.BOOK2, self.TRAIL = stop, book1, book2, trail
        self.scale_out = scale_out
        self.prem_trail = prem_trail          # if set: trail the option PREMIUM by this many pts

    def on_day_start(self, ctx):
        pc = ctx.prev_close()
        gap = (ctx.spot_bar.open - pc) if pc is not None else 0.0
        self.active = pc is not None and gap <= -GAP
        self.or_low = 1e18
        self.entered = False
        self.entry_spot = None
        self.min_spot = 1e18
        self.booked = False
        self.armed = False

    def on_bar(self, ctx):
        bar = ctx.spot_bar
        tt = ctx.now.time()
        if tt <= OR_END:
            self.or_low = min(self.or_low, bar.low)
        if not self.active or self.entered:
            return []
        if OR_END < tt < ENTRY_CUTOFF and bar.low < self.or_low - BREAK_BUF:
            self.entry_spot = bar.close
            self.min_spot = bar.low
            self.entered = True
            return [Order(side="PUT", strike=_put_strike(bar.close), lots=LOTS)]
        return []

    def manage(self, ctx, pos):
        bar = ctx.spot_bar
        if ctx.now.time() >= SQUARE:
            return Exit.market("eod")
        if self.prem_trail is not None:                           # trail the PREMIUM (not spot)
            return Exit.trail(self.prem_trail, "ptrail")
        if bar.high >= self.entry_spot + self.STOP:               # adverse stop (full remainder)
            return Exit.market("stop")
        self.min_spot = min(self.min_spot, bar.low)
        if self.scale_out and not self.booked and self.min_spot <= self.entry_spot - self.BOOK1:
            self.booked = True
            return Exit.market("book1", fraction=0.5)             # book half at +BOOK1
        arm_at = self.BOOK2 if self.scale_out else self.BOOK1     # ride the rest to +BOOK2
        if not self.armed and self.min_spot <= self.entry_spot - arm_at:
            self.armed = True
        if self.armed and bar.high >= self.min_spot + self.TRAIL:
            return Exit.market("trail")                           # exit remainder on the bounce
        return None
