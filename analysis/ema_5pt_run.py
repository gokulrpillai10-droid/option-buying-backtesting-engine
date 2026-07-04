"""EMA 9/21 crossover, 5-min, +/-5 PREMIUM POINTS bracket (user spec 2026-06-24).

Entry: EMA9 x EMA21 on 5-min spot -> CALL (bull) / PUT (bear), ATM, 1 lot.
EMAs warm up from 09:15 (every bar), entries allowed 09:40-14:55.
Exit: premium +5 (target) or -5 (stop), resolved intrabar (stop checked first =
conservative), else square-off 14:58. Costs + slippage via the validated engine.
"""
from datetime import date, time

import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from strategies.ema_crossover import EMACrossover

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)

print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)


def run(slip):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=slip))
    strat = EMACrossover(exit_points=5.0, entry_start=time(9, 40),
                         entry_end=time(14, 55), square_off=time(14, 58))
    res = bt.run(strat, START, END)
    tr = pd.DataFrame([{"date": t.date, "net": t.net, "reason": t.reason, "side": t.side}
                       for t in res.trades])
    if len(tr) == 0:
        print(f"slip {slip}: no trades"); return
    tr["yr"] = pd.to_datetime(tr["date"]).dt.year
    print(f"\n=== EMA 9/21  +/-5pt premium  slip {slip*100:.2f}%   {len(tr)} trades ===")
    print(f"net Rs{res.net():,.0f}   PF {res.profit_factor():.2f}   win {res.win_rate()*100:.0f}%   "
          f"t {res.t_stat():.2f}   avg net/trade Rs{tr.net.mean():,.0f}")
    for y, s in tr.groupby("yr"):
        pf = s[s.net > 0].net.sum() / -s[s.net <= 0].net.sum() if (s.net <= 0).any() else float("inf")
        print(f"   {y}: {len(s):>4} trades  net Rs{s.net.sum():>11,.0f}  PF {pf:.2f}")
    print("   exits:", dict(tr.reason.value_counts()))


run(0.0025)
run(0.005)
