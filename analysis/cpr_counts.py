"""Trade counts for the saved strategy (CPRBreakout() defaults, slip 0.25%).
Reports leg-level (engine Trade records: book/trail/stop exits) and position-level
(trade-DAYS: one entry per day) positive/negative counts. Loads data ONCE.
"""
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
    CPRBreakout(), START, END)                       # defaults = the saved deployable config

tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net} for t in res.trades])
day = tr.groupby("date").net.sum()

print("\n=== TRADE COUNTS (saved config, slip 0.25%) ===")
print(f"position-level (one entry per DAY):")
print(f"   total trades   {len(day)}")
print(f"   positive       {(day > 0).sum()}")
print(f"   negative/flat  {(day <= 0).sum()}")
print(f"\nleg-level (engine fills: book / trail / stop / eod exits):")
print(f"   total legs     {len(tr)}")
print(f"   positive       {(tr.net > 0).sum()}")
print(f"   negative/flat  {(tr.net <= 0).sum()}")
print(f"\nnet Rs{res.net():,.0f}  over {len(day)} trading days")
