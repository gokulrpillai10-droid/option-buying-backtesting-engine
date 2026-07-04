"""Backtest the CPR breakout through the validated engine.

Exit config (user's latest): 6 lots; PREMIUM stop -25; PREMIUM target +50 books 3 lots;
trail remaining 3 lots by 12 premium pts. Entry = CPR cross, deep-ITM. Loads data ONCE.
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


def run(name, slip, ratio=None, yearly=False):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=slip))
    res = bt.run(CPRBreakout(book_prem=50, book_lots=3, stop_prem=25, trail_prem=12,
                             max_cpr_ratio=ratio), START, END)
    t = pd.DataFrame([{"date": x.date, "net": x.net, "side": x.side,
                       "reason": x.reason} for x in res.trades])
    if len(t) == 0:
        print(f"{name:<42} no trades"); return
    print(f"{name:<42}{len(t):>6}{res.net():>13,.0f}{res.profit_factor():>6.2f}"
          f"{res.win_rate()*100:>5.0f}%{res.t_stat():>7.2f}")
    if yearly:
        t["yr"] = pd.to_datetime(t["date"]).dt.year
        for y, s in t.groupby("yr"):
            print(f"     {y}: {len(s):>4} legs  net {s.net.sum():>12,.0f}")
        print("     exits:", dict(t.reason.value_counts()))


print("\nCPR breakout -- premium stop -25, target +50 book 3, trail 3 by 12")
print(f"{'config':<42}{'legs':>6}{'net Rs':>13}{'PF':>6}{'win':>6}{'t':>7}")
print("-" * 80)
run("slip 1.0%, no filter", 0.01, None, yearly=True)
run("slip 0.5%, no filter", 0.005, None)
run("slip 1.0%, narrow<=0.25", 0.01, 0.25)
run("slip 1.0%, narrow<=0.15", 0.01, 0.15)
