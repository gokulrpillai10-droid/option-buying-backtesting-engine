"""Combine both findings: the +50 'grown-winner' config (CPR far-close stop, target +50
book 2/6, trail 4 by 12) PLUS the low-IV day filter. Sweep max_iv to see if cutting
high-vol days flips it positive. Reports year-by-year + W/L. Loads data ONCE.
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


def run(max_iv, slip=0.01, yearly=False):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=slip))
    res = bt.run(CPRBreakout(book_prem=50, book_lots=2, trail_prem=12,
                             stop_mode="far", stop_prem=None, max_iv=max_iv), START, END)
    tr = pd.DataFrame([{"date": t.date, "net": t.net} for t in res.trades])
    if len(tr) == 0:
        print(f"max_iv={max_iv}: no trades"); return
    nets = tr.net.tolist()
    wins = [n for n in nets if n > 0]; loss = [n for n in nets if n <= 0]
    wl = (sum(wins) / len(wins)) / (-sum(loss) / len(loss)) if wins and loss else 0
    label = f"max_iv={max_iv if max_iv is not None else 'none':<5}"
    print(f"{label:<16}{len(tr):>6}{res.net():>13,.0f}{res.profit_factor():>6.2f}"
          f"{res.win_rate()*100:>5.0f}%{wl:>6.2f}")
    if yearly:
        tr["yr"] = pd.to_datetime(tr["date"]).dt.year
        for y, s in tr.groupby("yr"):
            print(f"      {y}: {len(s):>4} legs  net {s.net.sum():>12,.0f}")


print("\n+50 config + low-IV day filter (slip 1.0%)")
print(f"{'filter':<16}{'legs':>6}{'net Rs':>13}{'PF':>6}{'win':>6}{'W/L':>6}")
print("-" * 53)
run(None)
run(0.20)
run(0.16)
run(0.13, yearly=True)
print("\n(0.5% slip, best filters)")
run(0.16, slip=0.005)
run(0.13, slip=0.005)
