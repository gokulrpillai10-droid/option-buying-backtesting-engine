"""Given the liquidity check (deep-ITM legs are heavily traded; our 6 lots are ~0.01%
of daily volume; spread realistically ~1-5 ticks = ~0.05-0.25%), re-run the profitable
IV<=0.13 config across REALISTIC slippage levels. Loads data ONCE.
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


def run(slip, yearly=False):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=slip))
    res = bt.run(CPRBreakout(book_prem=50, book_lots=2, trail_prem=12,
                             stop_mode="far", stop_prem=None, max_iv=0.13), START, END)
    tr = pd.DataFrame([{"date": t.date, "net": t.net} for t in res.trades])
    ticks = slip * 206 / 0.05         # ~spread in NIFTY ticks at the median premium
    print(f"slip {slip*100:>4.2f}% (~{ticks:.0f} ticks){len(tr):>7} legs{res.net():>13,.0f}"
          f"  PF {res.profit_factor():.2f}  win {res.win_rate()*100:.0f}%")
    if yearly:
        tr["yr"] = pd.to_datetime(tr["date"]).dt.year
        for y, s in tr.groupby("yr"):
            print(f"      {y}: net {s.net.sum():>12,.0f}")


print("\nIV<=0.13 config across realistic slippage (median premium ~Rs206, tick Rs0.05)")
print("-" * 64)
run(0.0010)
run(0.0025, yearly=True)
run(0.0050)
run(0.0100)
