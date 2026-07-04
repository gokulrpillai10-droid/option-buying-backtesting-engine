"""Opening-Range Breakout traded with vertical DEBIT SPREADS.

Defined-risk structure (vs naked buying): the short leg finances the long leg and
offsets its theta/vega -- the right tool for the friction wall that killed naked buying.

  Opening range : high/low of spot over [09:15, OR_END)  (default 30 min -> 09:45).
  Entry (1/day) : a 5-min candle CLOSES above OR-high -> BULL CALL SPREAD
                  (buy ATM call, sell ATM+WIDTH call); closes below OR-low -> BEAR PUT
                  SPREAD (buy ATM put, sell ATM-WIDTH put). ATM = nearest 50.
  Size          : LOTS lots per leg (default 3).
  Exit          : combined P&L >= +TARGET or <= -STOP (rupees), or SQUARE (15:25). No re-entry.
"""
from datetime import time

from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit


def _atm(spot):
    return int(round(spot / 50.0)) * 50


class ORBSpread(Strategy):
    def __init__(self, or_end=time(9, 45), width=300, lots=3,
                 target=9000.0, stop=3000.0, entry_cutoff=time(15, 0), square=time(15, 25),
                 min_iv=None):
        self.OR_END = or_end
        self.WIDTH, self.LOTS = width, lots
        self.TARGET, self.STOP = target, stop
        self.ENTRY_CUTOFF, self.SQUARE = entry_cutoff, square
        self.min_iv = min_iv                         # high-vol regime gate: skip days whose day-open ATM IV < this (None=off)

    def on_day_start(self, ctx):
        self.or_high = -1e18
        self.or_low = 1e18
        self.entered = False
        self.side = None
        self.active = True
        # high-vol regime filter (mirror of CPR's max_iv): breakouts need vol -> skip calm days
        if self.min_iv is not None:
            iv = self._day_open_iv(ctx)
            if iv is None or iv < self.min_iv:
                self.active = False

    @staticmethod
    def _day_open_iv(ctx):
        """Day-open ATM implied vol (the VIX proxy) -- None if unavailable."""
        try:
            k = ctx.atm_strike("CALL")
            return ctx.snapshot().call(k).iv if k is not None else None
        except Exception:
            return None

    def on_bar(self, ctx):
        bar = ctx.spot_bar
        tt = ctx.now.time()
        if tt < self.OR_END:                         # building the opening range
            self.or_high = max(self.or_high, bar.high)
            self.or_low = min(self.or_low, bar.low)
            return []
        if not self.active or self.entered or tt >= self.ENTRY_CUTOFF or self.or_high < -1e17:
            return []
        atm = _atm(bar.close)
        if bar.close > self.or_high:                 # bullish breakout -> bull call spread
            self.entered, self.side = True, "CALL"
            return [Order(action="buy", side="CALL", strike=atm, lots=self.LOTS),
                    Order(action="sell", side="CALL", strike=atm + self.WIDTH, lots=self.LOTS)]
        if bar.close < self.or_low:                  # bearish breakdown -> bear put spread
            self.entered, self.side = True, "PUT"
            return [Order(action="buy", side="PUT", strike=atm, lots=self.LOTS),
                    Order(action="sell", side="PUT", strike=atm - self.WIDTH, lots=self.LOTS)]
        return []

    def manage(self, ctx, pos):
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        snap = ctx.snapshot()
        strikes = set(snap.strikes())
        prices = []
        for lg in pos.legs:
            if lg.strike not in strikes:
                return None                          # can't price a leg -> hold
            q = snap.call(lg.strike) if lg.side == "CALL" else snap.put(lg.strike)
            prices.append(q.close)
        pnl = pos.unrealized(prices)
        if pnl >= self.TARGET:
            return Exit.market("target")
        if pnl <= -self.STOP:
            return Exit.market("stop")
        return None
