"""Capital profile of the current config (CPR +50/book2/trail12/far/IV<=0.12, slip 0.25%).
Deployed capital per trade = entry premium x full 6-lot qty (the cash to buy the position).
The strategy holds ONE intraday position at a time (flat overnight), so peak deployment
= one position. Total capital = max position + max-drawdown buffer. Loads data ONCE.
"""
from datetime import date

import numpy as np
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
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025))
res = bt.run(CPRBreakout(book_prem=50, book_lots=2, trail_prem=12, stop_mode="far",
                         stop_prem=None, max_iv=0.12), START, END)

tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net, "entry": t.entry, "qty": t.qty}
                   for t in res.trades])
tr["outlay"] = tr.entry * tr.qty
day = tr.groupby("date").agg(net=("net", "sum"), deployed=("outlay", "sum")).sort_values("date")

eq = day.net.cumsum(); maxdd = (eq - eq.cummax()).min()
d = day.deployed
print(f"\n=== CAPITAL ({len(day)} trades, 1 position at a time) ===")
print(f"deployed capital per trade (entry premium x 6 lots):")
print(f"   mean   Rs{d.mean():,.0f}")
print(f"   median Rs{d.median():,.0f}")
print(f"   min    Rs{d.min():,.0f}")
print(f"   p90    Rs{np.percentile(d,90):,.0f}")
print(f"   max    Rs{d.max():,.0f}")
print(f"\nmax drawdown            Rs{maxdd:,.0f}")
print(f"\n--- total capital to run it ---")
print(f"max single position     Rs{d.max():,.0f}")
print(f"+ max drawdown buffer    Rs{-maxdd:,.0f}")
print(f"= total capital          Rs{d.max() - maxdd:,.0f}")
print(f"\nannual net Rs{day.net.sum()/((day.index.max()-day.index.min()).days/365.25):,.0f}"
      f"  -> ROC on total capital ~{day.net.sum()/((day.index.max()-day.index.min()).days/365.25)/(d.max()-maxdd)*100:.0f}%/yr")
print(f"   -> ROC on avg deployed ~{day.net.sum()/((day.index.max()-day.index.min()).days/365.25)/d.mean()*100:.0f}%/yr (gross of DD buffer)")
