"""NIFTY gap-fill fade (arm F) and failed-gap-down reversal long (arm R).

Both buy an ATM weekly option and share the frozen premium exit
(strategies/gap_fill_params). Selection is measured off the SETTLED open (close
of the 09:15 bar ~= 09:20 price), not the raw auction open. Exit numbers are
frozen from 2020-2024 and never swept.

Arm F: gap-up -> PUT, gap-down -> CALL (fade back toward prior close).
Arm R: gap-down that has reclaimed its open by 09:30 -> CALL (failed gap flips).
"""
from datetime import time

from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit, TwoStage
from strategies import gap_fill_params as P

LOTS = 6
DECIDE_F = time(9, 15)      # 09:15 bar close = settled open; fills next bar ~09:20
DECIDE_R = time(9, 30)      # reclaim checkpoint
SQUARE = time(15, 15)


def _atm(snap, ref, side):
    strikes = [s for s in snap.strikes()
               if (snap.call(s) if side == "CALL" else snap.put(s)).is_traded]
    if not strikes:
        return None
    return min(strikes, key=lambda s: (abs(s - ref), -s))


class GapFillFade(Strategy):
    def __init__(self, arm="fade", lots=LOTS):
        assert arm in ("fade", "reversal")
        self.arm = arm
        self.lots = lots
        # book 4/6 at +BOOK, trail the 2/6 by TRAIL, full premium stop at -STOP
        self._two = TwoStage(P.BOOK, P.TRAIL, 4 / 6, stop_pts=P.STOP)

    def on_day_start(self, ctx):
        self.entered = False
        self.settled = None
        self.day_open = None
        self._two.reset()

    def _select(self, ctx):
        pc = ctx.prev_close()
        if pc is None:
            return None
        bar = ctx.spot_bar
        settled = bar.close                       # 09:15 bar close ~= 09:20
        self.settled = settled
        self.day_open = bar.open
        gap = settled / pc - 1.0
        if not (P.FLOOR <= abs(gap) <= P.CAP):
            return None
        if self.arm == "fade":
            side = "PUT" if gap > 0 else "CALL"
        else:                                     # reversal: gap-down only
            if gap >= 0:
                return None
            side = "CALL"
        strike = _atm(ctx.snapshot(), settled, side)
        if strike is None:
            return None
        return side, strike

    def on_bar(self, ctx):
        if self.entered:
            return []
        t = ctx.now.time()
        if self.arm == "fade":
            if t != DECIDE_F:
                return []
            sel = self._select(ctx)
            if sel is None:
                return []
        else:
            if t < DECIDE_F:
                return []
            if t == DECIDE_F:                     # record settled/open, wait for 09:30
                self._select(ctx)                 # sets self.settled/day_open only
                return []
            if t != DECIDE_R:
                return []
            # reclaim: gap-down (settled<pc) AND price back above the day open
            pc = ctx.prev_close()
            if pc is None or self.settled is None or self.settled >= pc:
                return []
            if ctx.spot_bar.close <= self.day_open:
                return []
            side = "CALL"
            strike = _atm(ctx.snapshot(), ctx.spot_bar.close, side)
            if strike is None:
                return []
            sel = (side, strike)
        side, strike = sel
        self.entered = True
        return [Order(side=side, strike=strike, lots=self.lots)]

    def manage(self, ctx, pos):
        if ctx.now.time() >= SQUARE:
            return Exit.market("eod")
        return self._two.exit_for(pos)
