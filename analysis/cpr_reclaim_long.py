"""CE-only RECLAIM re-entry: after a red downside CPR break, buy a deep-ITM CALL
when a candle CLOSES back above the top edge (the reversal -- close-confirmed,
not a wick).  Same premium rules as the deployed CPRBreakout: +50 book 4/6,
trail 2 by 12, far-edge close stop (CALL: close < bottom), 15:15 square,
0.25%/side slip.  One trade per day.  Standalone -- takes ONLY the reclaim CE,
never the initial PUT.

Runs through the real options engine, so the P&L is actual premium.  The engine
defines the population: every day with (red close-below-bottom) THEN (close above
top) before the 14:45 entry cutoff.
"""
from datetime import date, time

import numpy as np
import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.execution.model import Order
from engine.backtest import Backtest, Knobs
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit, TwoStage

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2019, 1, 1), date(2026, 12, 31)
LOTS = 6
ITM_DEPTH = 150


def _itm_strike(spot, side):
    base = (int(spot) // 50) * 50
    return base - ITM_DEPTH if side == "CALL" else base + ITM_DEPTH


def _atm_strike(spot):
    return int(round(spot / 50.0) * 50)      # nearest 50-pt strike to spot


class CPRReclaimLong(Strategy):
    """Buy CE on the close-confirmed reclaim of the top edge, only AFTER a red
    downside break earlier in the same session."""

    def __init__(self, book_prem=50.0, book_lots=4, trail_prem=12.0, stop_mode="far",
                 max_iv=0.12, entry_cutoff=time(14, 45), square=time(15, 15),
                 require_red_break=True, strike_mode="atm"):
        self.BOOK_PREM = book_prem
        self.BOOK_FRAC = book_lots / LOTS
        self.TRAIL_PREM = trail_prem
        self.stop_mode = stop_mode
        self.max_iv = max_iv
        self.strike_mode = strike_mode          # "atm" | "itm"
        self.ENTRY_CUTOFF, self.SQUARE = entry_cutoff, square
        self.require_red_break = require_red_break
        self._two = TwoStage(book_prem, trail_prem, self.BOOK_FRAC, stop_pts=None)

    def on_day_start(self, ctx):
        hlc = ctx.prev_day_hlc()
        if hlc is None:
            self.active = False
            return
        h, l, c = hlc
        p = (h + l + c) / 3.0
        bc = (h + l) / 2.0
        tc = 2.0 * p - bc
        self.top, self.bottom = max(tc, bc), min(tc, bc)
        self.active = True
        if self.max_iv is not None:
            iv = self._day_open_iv(ctx)
            if iv is not None and iv > self.max_iv:
                self.active = False
        self.broke_down = False
        self.entered = False
        self.stop_close = None
        self._two.reset()

    @staticmethod
    def _day_open_iv(ctx):
        try:
            k = ctx.atm_strike("CALL")
            return ctx.snapshot().call(k).iv if k is not None else None
        except Exception:
            return None

    def on_bar(self, ctx):
        if not self.active or self.entered:
            return []
        bar = ctx.spot_bar
        if not self.broke_down:
            # stage 0: wait for a RED candle that closes below the bottom edge
            if (bar.high >= self.bottom and bar.close < self.bottom
                    and (not self.require_red_break or bar.close < bar.open)):
                self.broke_down = True
            return []
        # stage 1: after the break, enter CE when a candle CLOSES above the top
        if ctx.now.time() >= self.ENTRY_CUTOFF:
            return []
        if bar.close > self.top:
            self.entered = True
            self.side = "CALL"
            self.stop_close = self.bottom if self.stop_mode == "far" else self.top
            if self.strike_mode == "atm":
                k = ctx.atm_strike("CALL") or _atm_strike(bar.close)
            else:
                k = _itm_strike(bar.close, "CALL")
            return [Order(side="CALL", strike=k, lots=LOTS)]
        return []

    def manage(self, ctx, pos):
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        if not self._two.scaled(pos):
            close = ctx.spot_bar.close
            if pos.side == "CALL" and close < self.stop_close:      # far-edge close stop
                return Exit.market("cpr_stop")
        return self._two.exit_for(pos)


print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)


def run(name, max_iv, strike_mode):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025))
    res = bt.run(CPRReclaimLong(max_iv=max_iv, strike_mode=strike_mode), START, END)
    t = pd.DataFrame([{"date": pd.Timestamp(x.date), "net": x.net, "reason": x.reason}
                      for x in res.trades])
    if t.empty:
        print(f"{name}: no trades"); return
    day = t.groupby("date").net.sum()
    sharpe = day.mean() / day.std() * np.sqrt(252) if day.std() > 0 else float("nan")
    print(f"\n=== {name} ===")
    print(f"  entry days (CE reclaim re-entries): {len(day)}   legs: {len(t)}")
    print(f"  NET  Rs{res.net():,.0f}    PF {res.profit_factor():.2f}    "
          f"win {res.win_rate()*100:.0f}%    t {res.t_stat():.2f}")
    print(f"  day-level: positive {int((day > 0).sum())} / negative {int((day <= 0).sum())}"
          f"    Sharpe {sharpe:.2f}    expectancy Rs{day.mean():,.0f}/day")
    t["yr"] = t.date.dt.year
    print("  by year:")
    for y, s in t.groupby("yr"):
        d = s.groupby("date").net.sum()
        print(f"     {y}: {len(d):>3} days   net Rs{s.net.sum():>11,.0f}   "
              f"pos {int((d>0).sum()):>2}/{len(d):<2}")
    print("  exit reasons:", dict(t.reason.value_counts()))


print("\nCE RECLAIM RE-ENTRY -- CALL on close-confirmed reclaim after a red down-break")
print("A/B: ATM strike vs deep-ITM (150pt), each +/- the day-open IV filter\n")
print("################  ATM STRIKE  ################")
run("ATM  + IV filter <= 0.12", max_iv=0.12, strike_mode="atm")
run("ATM  + no IV filter",      max_iv=None, strike_mode="atm")
print("\n################  DEEP-ITM (150pt) -- baseline  ################")
run("ITM  + IV filter <= 0.12", max_iv=0.12, strike_mode="itm")
run("ITM  + no IV filter",      max_iv=None, strike_mode="itm")
