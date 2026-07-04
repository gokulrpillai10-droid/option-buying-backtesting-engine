"""Wider analysis: sweep gap-down deep-ITM put across exit configs x slippage.

The sensitivity-sweep deliverable -- map the parameter space and friction sensitivity
to see if ANY config of the strategy is robustly positive, or if friction is the wall.
Loads data once; runs the grid.
"""
from datetime import date

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

CONFIGS = [
    ("prem-trail 20", dict(prem_trail=20)),
    ("prem-trail 40", dict(prem_trail=40)),
    ("prem-trail 60", dict(prem_trail=60)),
    ("prem-trail 80", dict(prem_trail=80)),
    ("spot scale s30/b25/t20", dict(stop=30, book1=25, book2=50, trail=20, scale_out=True)),
    ("spot scale s40/b25/t30", dict(stop=40, book1=25, book2=60, trail=30, scale_out=True)),
    ("spot single s30/arm40/t35", dict(stop=30, book1=40, trail=35, scale_out=False)),
]
SLIPS = [0.005, 0.01, 0.015]

print(f"\n{'config':<28}{'slip':>6}{'legs':>6}{'net Rs':>12}{'PF':>6}{'win':>5}{'t':>7}")
print("-" * 72)
for name, kw in CONFIGS:
    for slip in SLIPS:
        bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=slip))
        res = bt.run(GapDownPut(**kw), START, END)
        n = len(res.trades)
        if n == 0:
            print(f"{name:<28}{slip:>6.3f}  no trades"); continue
        flag = " <==+" if res.net() > 0 else ""
        print(f"{name:<28}{slip:>6.3f}{n:>6}{res.net():>12,.0f}{res.profit_factor():>6.2f}"
              f"{res.win_rate()*100:>4.0f}%{res.t_stat():>7.2f}{flag}")
    print()
