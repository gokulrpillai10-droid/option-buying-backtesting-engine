"""Backtest gap-down deep-ITM PUT through the engine: scale-out vs cheap wider-exit.

Loads data ONCE, runs both configs.
"""
from datetime import date

import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from strategies.gap_down_put import GapDownPut

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)

print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.01))


def run(name, strat):
    res = bt.run(strat, START, END)
    t = pd.DataFrame([{"date": x.date, "net": x.net, "premium": x.entry * x.qty,
                       "reason": x.reason} for x in res.trades])
    if len(t) == 0:
        print(f"\n{name}: no trades"); return
    t["yr"] = pd.to_datetime(t["date"]).dt.year
    print(f"\n=== {name}  ({len(t)} legs) ===")
    print(f"{'Year':<6}{'Legs':>6}{'Net Rs':>12}{'PF':>7}")
    for y, s in t.groupby("yr"):
        W = s[s.net > 0]
        pf = W.net.sum() / -s[s.net <= 0].net.sum() if (s.net <= 0).any() else float("inf")
        print(f"{y:<6}{len(s):>6}{s.net.sum():>12,.0f}{pf:>7.2f}")
    print(f"{'ALL':<6}{len(t):>6}{res.net():>12,.0f}{res.profit_factor():>7.2f}  "
          f"win {res.win_rate()*100:.0f}%  t {res.t_stat():.2f}  "
          f"tail(excl10) Rs{res.tail_excl(10):,.0f}")
    print("  exits:", dict(t.reason.value_counts()))


print("\nRunning configs ...")
run("PREMIUM-TRAIL 20 (exit when put premium gives back 20 pts)",
    GapDownPut(prem_trail=20))
run("SCALE-OUT (stop30, book half +25, ride to +50 trail 20, spot)",
    GapDownPut(stop=30, book1=25, book2=50, trail=20, scale_out=True))
