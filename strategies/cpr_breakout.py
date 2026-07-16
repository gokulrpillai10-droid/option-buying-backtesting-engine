"""CPR (Central Pivot Range) breakout, deep-ITM option buying.

CPR from the PRIOR day's H/L/C:
    Pivot = (H + L + C) / 3 ;  BC = (H + L) / 2 ;  TC = 2*Pivot - BC
    top = max(TC, BC) ;  bottom = min(TC, BC)        (the CPR band [bottom, top])

Entry (one breakout per day, 5-min):
    CE : a candle CROSSES UP through the top (low <= top) and CLOSES above it  -> deep-ITM CALL
    PE : a candle CROSSES DOWN through the bottom (high >= bottom) and CLOSES below it -> deep-ITM PUT
  The cross requirement (low/high touching the line) rejects gaps that open already beyond
  the CPR -- the candle must physically trade through the edge.

Exits (configurable -- DEFAULTS = the walk-forward-validated deployable config):
    target +50 premium, book 4 of 6 lots, trail the remaining 2 by 12 premium pts,
    CPR far-edge close stop, low-IV day filter IV<=0.12.
    (slip 0.25%: net +Rs745k/6.2y, PF 1.43, Sharpe 1.08, Sortino 2.77, Calmar 0.98,
     max DD -Rs123k, 6/7 years green, drop-2025 +Rs364k. See analysis/cpr_*.py.)

    CPR stop   : a later candle CLOSING beyond a CPR edge -- full exit.
                 stop_mode="far"   -> CE: close < bottom ; PE: close > top   (default)
                 stop_mode="broken"-> CE: close < top    ; PE: close > bottom (tighter)
                 stop_mode="none"  -> no CPR-close stop (premium loss-cap only)
    Loss cap   : optional premium hard-stop at -STOP_PREM (full exit, None=off).
    Target     : at +BOOK_PREM premium points, book BOOK_LOTS of 6, carry the rest.
    Trail      : the carried lots trail the PREMIUM by TRAIL_PREM points.
    IV filter  : skip days whose day-open ATM IV exceeds max_iv (expensive/whippy regime).
    EOD        : square off at SQUARE.
"""
from datetime import time

from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit, TwoStage

LOTS = 6
ITM_DEPTH = 150          # deep-ITM (~delta 0.8)


def _strike(spot, side):
    base = (int(spot) // 50) * 50
    return base - ITM_DEPTH if side == "CALL" else base + ITM_DEPTH


class CPRBreakout(Strategy):
    def __init__(self, book_prem=50.0, book_lots=4, trail_prem=12.0,
                 stop_mode="far", stop_prem=None, max_cpr_ratio=None, max_iv=0.12,
                 entry_cutoff=time(14, 45), square=time(15, 15)):
        self.BOOK_PREM = book_prem                 # premium-points gain to scale out at
        self.BOOK_FRAC = book_lots / LOTS          # 2/6 -> book 1/3, carry 4
        self.TRAIL_PREM = trail_prem
        self.stop_mode = stop_mode                 # "far" (original) or "broken" (tighter) CPR-close stop
        self.STOP_PREM = stop_prem                 # optional premium loss-cap (None = off)
        self.max_cpr_ratio = max_cpr_ratio         # skip days where width/(H-L) exceeds this (None=off)
        self.max_iv = max_iv                       # skip high-vol days: day-open ATM IV > this (None=off)
        self.ENTRY_CUTOFF, self.SQUARE = entry_cutoff, square
        # book BOOK_FRAC at +BOOK_PREM (with the optional loss-cap), trail the rest
        self._two = TwoStage(book_prem, trail_prem, self.BOOK_FRAC, stop_pts=stop_prem)

    def on_day_start(self, ctx):
        hlc = ctx.prev_day_hlc()
        if hlc is None:
            self.active = False
            return
        h, l, c = hlc
        pivot = (h + l + c) / 3.0
        bc = (h + l) / 2.0
        tc = 2.0 * pivot - bc
        self.top, self.bottom = max(tc, bc), min(tc, bc)
        self.active = True
        # narrow-CPR filter: a WIDE CPR (relative to the prior day's range) = choppy/rangebound -> skip
        if self.max_cpr_ratio is not None:
            rng = h - l
            ratio = (self.top - self.bottom) / rng if rng > 0 else 1e9
            if ratio > self.max_cpr_ratio:
                self.active = False
        # vol-regime filter: skip days whose day-open ATM IV exceeds max_iv (expensive premium)
        if self.active and self.max_iv is not None:
            iv = self._day_open_iv(ctx)
            if iv is not None and iv > self.max_iv:
                self.active = False
        self.entered = False
        self.side = None
        self.stop_close = None
        self._two.reset()

    @staticmethod
    def _day_open_iv(ctx):
        """Day-open ATM implied vol (the VIX proxy) -- None if unavailable."""
        try:
            k = ctx.atm_strike("CALL")
            return ctx.snapshot().call(k).iv if k is not None else None
        except Exception:
            return None

    def on_bar(self, ctx):
        if not self.active or self.entered:
            return []
        if ctx.now.time() >= self.ENTRY_CUTOFF:
            return []
        bar = ctx.spot_bar
        side = None
        if bar.low <= self.top and bar.close > self.top:          # crossed UP through top
            side = "CALL"
        elif bar.high >= self.bottom and bar.close < self.bottom:  # crossed DOWN through bottom
            side = "PUT"
        if side is None:
            return []
        self.entered = True
        self.side = side
        # the close-based CPR stop level. "none" -> unreachable (premium stop only);
        # "far" = opposite edge (original); "broken" = the edge just crossed.
        if self.stop_mode == "none":
            self.stop_close = -1e18 if side == "CALL" else 1e18
        elif side == "CALL":
            self.stop_close = self.bottom if self.stop_mode == "far" else self.top
        else:
            self.stop_close = self.top if self.stop_mode == "far" else self.bottom
        return [Order(side=side, strike=_strike(bar.close, side), lots=LOTS)]

    def manage(self, ctx, pos):
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        if not self._two.scaled(pos):                              # stage 1: full size, not yet booked
            close = ctx.spot_bar.close
            if pos.side == "CALL" and close < self.stop_close:     # CPR-close stop (full)
                return Exit.market("cpr_stop")
            if pos.side == "PUT" and close > self.stop_close:
                return Exit.market("cpr_stop")
        # stage 1: loss-cap (optional, full) + partial target scale-out, intrabar;
        # stage 2: trail the carried lots' premium
        return self._two.exit_for(pos)
