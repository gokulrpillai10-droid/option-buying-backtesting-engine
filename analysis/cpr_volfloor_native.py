"""Verify the ENGINE-NATIVE min_volume_mult floor reproduces the post-hoc result.

Post-hoc (cpr_volfloor.py) predicted: at volume>=10x order, 207 trade-days, +Rs868,525.
This runs the deployed config through the engine with Knobs(min_volume_mult=10) and checks
the native gate yields the same numbers (proves the wiring matches the validated semantics).
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

for mult in (0, 10):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025,
                                         min_volume_mult=mult))
    res = bt.run(CPRBreakout(book_prem=50, book_lots=4, trail_prem=12, stop_mode="far",
                             stop_prem=None, max_iv=0.12), START, END)
    tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net} for t in res.trades])
    days = tr.date.nunique()
    label = "no floor" if mult == 0 else f"min_volume_mult={mult}"
    print(f"{label:<22} trade-days {days:>4}   net Rs{res.net():>11,.0f}   "
          f"PF {res.profit_factor():.2f}   t {res.t_stat():.2f}")

print("\nEXPECTED (post-hoc cpr_volfloor.py): no floor=280d/+744,824 ; >=10x=207d/+868,525")
