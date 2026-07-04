"""Average win/loss per day for the saved config (CPRBreakout() defaults, slip 0.25%)."""
from datetime import date

import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from strategies.cpr_breakout import CPRBreakout

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)

print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
res = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025)).run(
    CPRBreakout(), START, END)

day = (pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net} for t in res.trades])
       .groupby("date").net.sum())
W = day[day > 0]
L = day[day <= 0]

print("\n=== PER-DAY P&L (saved config) ===")
print(f"total days {len(day)}   net Rs{day.sum():,.0f}")
print(f"\nLOSING days   : {len(L):>3}   total Rs{L.sum():,.0f}   avg Rs{L.mean():,.0f}   worst Rs{L.min():,.0f}")
print(f"WINNING days  : {len(W):>3}   total Rs{W.sum():,.0f}   avg Rs{W.mean():,.0f}   best  Rs{W.max():,.0f}")
print(f"\nall-day average (expectancy) Rs{day.mean():,.0f}/day")
print(f"avg win / avg loss ratio     {W.mean()/-L.mean():.2f}")
