"""ATR-ROC High-Volatility Naked Option Buy.

A high-volatility momentum buy: only trade when ATR confirms high volatility, and use
ROC for direction. The structural opposite of the CPR low-vol edge.

  Gate      : ATR(atr_period) > ATR_MIN (default 50)  -- high-vol only.
  Direction : ROC(roc_period) > 0 -> buy 1-strike-OTM CE ; ROC < 0 -> 1-strike-OTM PE.
  Size      : LOTS lots (default 1).  One position / one trade per day.
  Exit      : combined P&L >= +TARGET or <= -STOP (rupees), or SQUARE (15:00) square-off.

Indicators are computed on the engine's bar timeframe (run the backtest at 5min or 15min).
ATR uses a simple mean of the true range over the period; ROC is percent change over the
period. History is continuous across days (indicators are warm at the open).
"""
from datetime import time

from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit


def _atm(spot):
    return int(round(spot / 50.0)) * 50


def atr(bars, period):
    """Mean true range over the last ``period`` bars (needs period+1 bars)."""
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    last = trs[-period:]
    return sum(last) / period


def roc(bars, period):
    """Percent change of close over ``period`` bars (needs period+1 bars)."""
    if len(bars) < period + 1:
        return None
    past = bars[-(period + 1)].close
    return (bars[-1].close - past) / past * 100.0 if past else None


class ATRROCBuy(Strategy):
    LOTS = 1
    MIN_DTE = 3

    def __init__(self, atr_period=14, roc_period=12, atr_min=50.0, lots=1,
                 target=3000.0, stop=3000.0, entry_cutoff=time(14, 55), square=time(15, 0)):
        self.AP, self.RP, self.ATR_MIN = atr_period, roc_period, atr_min
        self.LOTS = lots
        self.TARGET, self.STOP = target, stop
        self.ENTRY_CUTOFF, self.SQUARE = entry_cutoff, square

    def on_day_start(self, ctx):
        self.entered = False

    def on_bar(self, ctx):
        if self.entered or ctx.now.time() >= self.ENTRY_CUTOFF:
            return []
        need = max(self.AP + 1, self.RP + 1)
        bars = ctx.recent(need)
        if len(bars) < need:
            return []
        a, r = atr(bars, self.AP), roc(bars, self.RP)
        if a is None or r is None or a <= self.ATR_MIN or r == 0:
            return []                                   # gate: high-vol only, need a direction
        atm = _atm(bars[-1].close)
        side, strike = ("CALL", atm + 50) if r > 0 else ("PUT", atm - 50)   # 1-strike OTM
        self.entered = True
        return [Order(side=side, strike=strike, lots=self.LOTS)]

    def manage(self, ctx, pos):
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        per_unit = lambda rs: rs / pos.qty                       # rupee P&L -> premium move
        return Exit.bracket(pos.entry - per_unit(self.STOP),
                            pos.entry + per_unit(self.TARGET), "stop", "target")
