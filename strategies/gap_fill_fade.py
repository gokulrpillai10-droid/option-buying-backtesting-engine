"""NIFTY gap-fill fade (arm F) and failed-gap-down reversal long (arm R).

Both buy an ATM weekly option and share the frozen premium exit
(strategies/gap_fill_params). Selection is measured off the SETTLED open (close
of the 09:15 bar ~= 09:20 price), stashed at 09:15 and used by the single
authoritative selector `_select` for both arms. Exit numbers are frozen from
2020-2024 and never swept.

Arm F: gap-up -> PUT, gap-down -> CALL (fade back toward prior close).
Arm R: gap-down that has reclaimed its 09:15 open by 09:30 -> CALL.
"""
from datetime import time

from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit, TwoStage
from strategies import gap_fill_params as P

LOTS = 6
RECORD = time(9, 15)        # stash settled open / day open (both arms)
DECIDE_F = time(9, 15)      # fade decides at 09:15 (fills next bar ~09:20)
DECIDE_R = time(9, 30)      # reversal decides at 09:30
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
        # book 4/6 at +BOOK, trail 2/6 by TRAIL, full premium stop at -STOP
        self._two = TwoStage(P.BOOK, P.TRAIL, 4 / 6, stop_pts=P.STOP)

    def on_day_start(self, ctx):
        self.entered = False
        self.settled = None
        self.day_open = None
        self.pc = None
        self._two.reset()

    def _record_open(self, ctx):
        """Stash the 09:15 settled open (bar close), raw day open, prev close."""
        self.pc = ctx.prev_close()
        bar = ctx.spot_bar
        self.settled = bar.close
        self.day_open = bar.open

    def _select(self, ctx):
        """Single authoritative selector for BOTH arms, using the stashed 09:15
        settled open. Returns (side, strike) or None (stand aside)."""
        if self.pc is None or self.settled is None:
            return None
        gap = self.settled / self.pc - 1.0
        if not (P.FLOOR <= abs(gap) <= P.CAP):
            return None
        if self.arm == "fade":
            side = "PUT" if gap > 0 else "CALL"
            ref = self.settled
        else:                                  # reversal: gap-down that reclaimed the open
            if gap >= 0 or ctx.spot_bar.close <= self.day_open:
                return None
            side = "CALL"
            ref = ctx.spot_bar.close
        strike = _atm(ctx.snapshot(), ref, side)
        if strike is None:
            return None
        return side, strike

    def on_bar(self, ctx):
        if self.entered:
            return []
        t = ctx.now.time()
        if t == RECORD:
            self._record_open(ctx)
        decide = DECIDE_F if self.arm == "fade" else DECIDE_R
        if t != decide:
            return []
        sel = self._select(ctx)
        if sel is None:
            return []
        side, strike = sel
        self.entered = True
        return [Order(side=side, strike=strike, lots=self.lots)]

    def manage(self, ctx, pos):
        if ctx.now.time() >= SQUARE:
            return Exit.market("eod")
        return self._two.exit_for(pos)
