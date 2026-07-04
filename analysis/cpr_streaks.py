"""Consecutive losing-day streaks for the current config (IV<=0.12, slip 0.25%).
A 'losing day' = a trade-day with net <= 0 (non-trade days don't break a streak; they
simply aren't trades). Reports the longest streak (by count) and the worst (by total Rs),
with dates. Loads data ONCE.
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
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025))
res = bt.run(CPRBreakout(book_prem=50, book_lots=2, trail_prem=12, stop_mode="far",
                         stop_prem=None, max_iv=0.12), START, END)

day = (pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net} for t in res.trades])
       .groupby("date").net.sum().sort_index())

# collect runs of consecutive losing trade-days (net <= 0)
runs, cur = [], []
for dt, n in day.items():
    if n <= 0:
        cur.append((dt, n))
    elif cur:
        runs.append(cur); cur = []
if cur:
    runs.append(cur)

by_len = sorted(runs, key=len, reverse=True)
by_loss = sorted(runs, key=lambda r: sum(n for _, n in r))   # most negative total

print(f"\n{len(runs)} losing streaks total. Trade-days = {len(day)}.")
print("\n=== LONGEST streaks (by number of consecutive losing days) ===")
for r in by_len[:3]:
    tot = sum(n for _, n in r)
    print(f"  {len(r)} days  {r[0][0].date()} -> {r[-1][0].date()}  total Rs{tot:,.0f}")
    for dt, n in r:
        print(f"       {dt.date()}  Rs{n:,.0f}")

print("\n=== WORST streak (by total rupees lost) ===")
r = by_loss[0]
print(f"  {len(r)} days  {r[0][0].date()} -> {r[-1][0].date()}  total Rs{sum(n for _,n in r):,.0f}")
for dt, n in r:
    print(f"       {dt.date()}  Rs{n:,.0f}")
