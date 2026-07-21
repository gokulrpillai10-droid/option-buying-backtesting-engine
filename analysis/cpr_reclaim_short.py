"""PE-only RECLAIM re-entry (BULL-TRAP mirror of cpr_reclaim_long.py): after a
green UPSIDE CPR break, buy a PUT when a candle CLOSES back below the bottom edge
(the reversal -- close-confirmed, not a wick).  Same premium rules as the
deployed CPRBreakout: +50 book 4/6, trail 2 by 12, far-edge close stop (PUT:
close > top), 15:15 square, 0.25%/side slip.  One trade per day.  Standalone --
takes ONLY the reclaim PE, never the initial CALL.

Runs through the real options engine, so the P&L is actual premium.  The engine
defines the population: every day with (green close-above-top) THEN (close below
bottom) before the 14:45 entry cutoff.
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


class CPRReclaimShort(Strategy):
    """Buy PE on the close-confirmed break of the bottom edge, only AFTER a green
    upside break earlier in the same session."""

    def __init__(self, book_prem=50.0, book_lots=4, trail_prem=12.0, stop_mode="far",
                 max_iv=0.12, entry_cutoff=time(14, 45), square=time(15, 15),
                 require_green_break=True, strike_mode="atm"):
        self.BOOK_PREM = book_prem
        self.BOOK_FRAC = book_lots / LOTS
        self.TRAIL_PREM = trail_prem
        self.stop_mode = stop_mode
        self.max_iv = max_iv
        self.strike_mode = strike_mode          # "atm" | "itm"
        self.ENTRY_CUTOFF, self.SQUARE = entry_cutoff, square
        self.require_green_break = require_green_break
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
        self.broke_up = False
        self.entered = False
        self.stop_close = None
        self._two.reset()

    @staticmethod
    def _day_open_iv(ctx):
        try:
            k = ctx.atm_strike("PUT")
            return ctx.snapshot().put(k).iv if k is not None else None
        except Exception:
            return None

    def on_bar(self, ctx):
        if not self.active or self.entered:
            return []
        bar = ctx.spot_bar
        if not self.broke_up:
            # stage 0: wait for a GREEN candle that closes above the top edge
            if (bar.low <= self.top and bar.close > self.top
                    and (not self.require_green_break or bar.close > bar.open)):
                self.broke_up = True
            return []
        # stage 1: after the break, enter PE when a candle CLOSES below the bottom
        if ctx.now.time() >= self.ENTRY_CUTOFF:
            return []
        if bar.close < self.bottom:
            self.entered = True
            self.side = "PUT"
            self.stop_close = self.top if self.stop_mode == "far" else self.bottom
            if self.strike_mode == "atm":
                k = ctx.atm_strike("PUT") or _atm_strike(bar.close)
            else:
                k = _itm_strike(bar.close, "PUT")
            return [Order(side="PUT", strike=k, lots=LOTS)]
        return []

    def manage(self, ctx, pos):
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        if not self._two.scaled(pos):
            close = ctx.spot_bar.close
            if pos.side == "PUT" and close > self.stop_close:       # far-edge close stop
                return Exit.market("cpr_stop")
        return self._two.exit_for(pos)


print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)


def run(name, max_iv, strike_mode, dump=False):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025))
    res = bt.run(CPRReclaimShort(max_iv=max_iv, strike_mode=strike_mode), START, END)
    if dump:
        print(f"\n=== TRADES: {name} ===")
        hdr = (f"{'date':<12}{'entry':>6}{'exit':>6}{'side':>5}{'strike':>7}{'qty':>5}"
               f"{'entryPx':>9}{'exitPx':>9}{'gross':>9}{'cost':>8}{'net':>10}  reason")
        print(hdr); print("-" * len(hdr))
        for x in sorted(res.trades, key=lambda z: (z.date, z.entry_dt or z.date)):
            et = x.entry_dt.strftime("%H:%M") if x.entry_dt else "-"
            xt = x.exit_dt.strftime("%H:%M") if x.exit_dt else "-"
            print(f"{str(x.date.date() if hasattr(x.date,'date') else x.date):<12}"
                  f"{et:>6}{xt:>6}{x.side:>5}{x.strike:>7}{x.qty:>5}"
                  f"{x.entry:>9.1f}{x.exit:>9.1f}{x.gross:>9.0f}{x.cost:>8.0f}{x.net:>10.0f}  {x.reason}")
    t = pd.DataFrame([{"date": pd.Timestamp(x.date), "net": x.net, "reason": x.reason}
                      for x in res.trades])
    if t.empty:
        print(f"{name}: no trades"); return
    day = t.groupby("date").net.sum()
    sharpe = day.mean() / day.std() * np.sqrt(252) if day.std() > 0 else float("nan")
    print(f"\n=== {name} ===")
    print(f"  entry days (PE reclaim re-entries): {len(day)}   legs: {len(t)}")
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


import os

print("\nPE RECLAIM RE-ENTRY -- PUT on close-confirmed break-below after a green up-break")
if os.environ.get("DUMP"):
    # single-config trade dump: DUMP=atm|itm  (default atm), DUMP_IV=1 to apply the filter
    sm = os.environ["DUMP"] if os.environ["DUMP"] in ("atm", "itm") else "atm"
    iv = 0.12 if os.environ.get("DUMP_IV") else None
    run(f"{sm.upper()} {'+IVfilter' if iv else '+no filter'}", max_iv=iv, strike_mode=sm, dump=True)
else:
    print("A/B: ATM strike vs deep-ITM (150pt), each +/- the day-open IV filter\n")
    print("################  ATM STRIKE  ################")
    run("ATM  + IV filter <= 0.12", max_iv=0.12, strike_mode="atm")
    run("ATM  + no IV filter",      max_iv=None, strike_mode="atm")
    print("\n################  DEEP-ITM (150pt) -- baseline  ################")
    run("ITM  + IV filter <= 0.12", max_iv=0.12, strike_mode="itm")
    run("ITM  + no IV filter",      max_iv=None, strike_mode="itm")
