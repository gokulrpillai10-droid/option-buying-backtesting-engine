"""ORB-spread KNOB SENSITIVITY SWEEP on the proven engine.

The falsification (orb_regime_run.py / orb_regime_out.txt) used ONE knob setting
(fill_price=open, slip 0.25%, min_dte=3). This is the airtight final check: vary the
engine's accuracy knobs and confirm the ORB-spread loss is robust to ALL of them, not
an artifact of one fill-timing / slippage / DTE choice.

Baseline strategy (no IV gate): ORBSpread(width=300, lots=1, target=3000, stop=3000).
Loads spot+catalog ONCE per min_dte (chain selection depends on min_dte).
"""
from datetime import date

import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from strategies.orb_spread import ORBSpread

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)

FILL_PRICES = ("open", "close")
SLIPS = (0.0025, 0.005)
MIN_DTES = (0, 3)

print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
cost = CostModel()

print(f"\n{'fill':>5} {'slip%':>6} {'dte':>4} | {'trades':>6} {'net Rs':>12} {'PF':>5} "
      f"{'win%':>5} {'t':>6}", flush=True)
print("-" * 60, flush=True)

for dte in MIN_DTES:
    md = MarketData(cat, spot, min_dte=dte)
    for fp in FILL_PRICES:
        for slip in SLIPS:
            bt = Backtest(md, cost, Knobs(fill_price=fp, slippage_pct=slip))
            strat = ORBSpread(width=300, lots=1, target=3000, stop=3000)
            res = bt.run(strat, START, END)
            n = len(res.trades)
            print(f"{fp:>5} {slip*100:>6.2f} {dte:>4} | {n:>6} {res.net():>12,.0f} "
                  f"{res.profit_factor():>5.2f} {res.win_rate()*100:>5.0f} {res.t_stat():>6.2f}",
                  flush=True)
