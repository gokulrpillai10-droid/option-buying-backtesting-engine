"""Backtest EMA 9/21 crossover (±Rs2000 P&L exit) through the VALIDATED engine.

First real strategy on the engine that reconciled G12 582/582. fill at next 1-min
open, 1% slippage/side, date-keyed lot, nearest expiry DTE>=3 (not expiry-based).
"""
import sys
from datetime import date

import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from strategies.ema_crossover import EMACrossover

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START = date(int(sys.argv[1]) if len(sys.argv) > 1 else 2020, 1, 1)
END = date(int(sys.argv[2]) if len(sys.argv) > 2 else 2026, 12, 31)

print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.01))

print(f"Running EMA 9/21 crossover {START}..{END} through the engine ...")
res = bt.run(EMACrossover(), START, END)
t = pd.DataFrame([{"date": x.date, "net": x.net, "premium": x.entry * x.qty,
                   "reason": x.reason} for x in res.trades])
t.to_csv("analysis/ema_engine_trades.csv", index=False)      # save first (cheap insurance)
t["yr"] = pd.to_datetime(t["date"]).dt.year

print(f"\nEMA 9/21 CROSSOVER  ({len(t)} trades, +/-Rs2000 exit, engine)")
print(f"{'Year':<6}{'Trades':>7}{'Win%':>6}{'Net Rs':>12}{'PF':>7}")
for y, s in t.groupby("yr"):
    W = s[s.net > 0]
    pf = W.net.sum() / -s[s.net <= 0].net.sum() if (s.net <= 0).any() else float("inf")
    print(f"{y:<6}{len(s):>7}{len(W)/len(s)*100:>5.0f}%{s.net.sum():>12,.0f}{pf:>7.2f}")
print(f"{'ALL':<6}{len(t):>7}{res.win_rate()*100:>5.0f}%{res.net():>12,.0f}{res.profit_factor():>7.2f}")
print(f"\nt-stat {res.t_stat():.2f} | tail (excl top-10) Rs{res.tail_excl(10):,.0f}")
print("exit reasons:", dict(t.reason.value_counts()))
