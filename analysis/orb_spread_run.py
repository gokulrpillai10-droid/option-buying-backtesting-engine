"""Backtest the ORB vertical-spread strategy through the engine.
30-min OR, 5-min close breakout, bull-call / bear-put debit spread, width 300, 3 lots,
combined P&L +9000 / -3000, 15:25 square-off. Loads data ONCE.
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

print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)


def run(slip):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=slip))
    res = bt.run(ORBSpread(width=300, lots=1, target=3000, stop=3000), START, END)
    tr = pd.DataFrame([{"date": t.date, "net": t.net, "debit": t.entry * t.qty,
                        "reason": t.reason, "side": t.side} for t in res.trades])
    if len(tr) == 0:
        print(f"slip {slip}: no trades"); return
    tr["yr"] = pd.to_datetime(tr["date"]).dt.year
    print(f"\n=== ORB SPREAD (width300, 1 lot, +3000/-3000)  slip {slip*100:.2f}%  {len(tr)} trades ===")
    print(f"net Rs{res.net():,.0f}   PF {res.profit_factor():.2f}   win {res.win_rate()*100:.0f}%   "
          f"t {res.t_stat():.2f}")
    print(f"avg debit (capital) Rs{tr.debit.mean():,.0f}   avg net/trade Rs{tr.net.mean():,.0f}")
    for y, s in tr.groupby("yr"):
        W = s[s.net > 0]
        pf = W.net.sum() / -s[s.net <= 0].net.sum() if (s.net <= 0).any() else float("inf")
        print(f"   {y}: {len(s):>4} trades  net Rs{s.net.sum():>11,.0f}  PF {pf:.2f}")
    print("   exits:", dict(tr.reason.value_counts()))


run(0.0025)
run(0.005)
