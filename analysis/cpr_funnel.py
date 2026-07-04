"""Trade funnel for the saved config: total trading days -> days with a valid CPR cross
(no filter) -> days kept by IV<=0.12. Shows why it's only ~280 trades. slip 0.25%.
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
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025))

total_days = len(md.trading_days(START, END))


def days(max_iv):
    res = bt.run(CPRBreakout(max_iv=max_iv), START, END)
    return len({t.date for t in res.trades})


cross = days(None)        # any valid CPR cross
kept = days(0.12)         # saved config

print(f"\n=== TRADE FUNNEL (2020-2026) ===")
print(f"total trading days in data        : {total_days}")
print(f"days with a valid CPR cross (entry): {cross}   ({cross/total_days*100:.0f}% of days)")
print(f"days kept by IV<=0.12 (SAVED)      : {kept}   ({kept/cross*100:.0f}% of crosses, "
      f"{kept/total_days*100:.0f}% of all days)")
print(f"\n=> {kept} trades over {(END-START).days/365.25:.1f}y  (~{kept/((END-START).days/365.25):.0f}/yr)")
