"""Work the original 66%-win CPR setup (+25 book 2/6, trail 12, far-edge stop):
add a premium LOSS-CAP and sweep it. The win rate is high; the losses are big
(far-edge stop a whole CPR-width away). Capping losses should lift PF -- IF it
doesn't noise-stop the winners. Reports avg win/loss to show the trade-off.
Loads data ONCE.
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


def run(name, stop_prem, slip=0.01):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=slip))
    res = bt.run(CPRBreakout(book_prem=25, book_lots=2, trail_prem=12,
                             stop_mode="far", stop_prem=stop_prem), START, END)
    nets = [t.net for t in res.trades]
    wins = [n for n in nets if n > 0]
    loss = [n for n in nets if n <= 0]
    aw = sum(wins) / len(wins) if wins else 0
    al = sum(loss) / len(loss) if loss else 0
    ratio = (aw / -al) if al else float("inf")
    print(f"{name:<22}{len(nets):>6}{res.net():>13,.0f}{res.profit_factor():>6.2f}"
          f"{res.win_rate()*100:>5.0f}%{aw:>9,.0f}{al:>10,.0f}{ratio:>7.2f}")


print("\nOriginal 66%-win setup + premium loss-cap sweep (slip 1.0%)")
print(f"{'loss-cap':<22}{'legs':>6}{'net Rs':>13}{'PF':>6}{'win':>6}{'avgWin':>9}{'avgLoss':>10}{'W/L':>7}")
print("-" * 79)
run("none (far-edge only)", None)
run("-60 premium", 60)
run("-50 premium", 50)
run("-40 premium", 40)
run("-30 premium", 30)
print("\n(breakeven needs W/L ~ 0.52 at 66% win; ~0.43 at 70%)")
