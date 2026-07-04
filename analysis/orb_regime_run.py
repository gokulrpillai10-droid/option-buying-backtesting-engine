"""ORB vertical-spread gated to a HIGH-VOL regime (mirror of CPR's max_iv).

Thesis: breakouts need volatility/trend. ORB bled in the low-vol 2023-25 regime
and won in high-vol 2020-21. Gate entries to days whose day-open ATM IV >= min_iv
(look-ahead-safe: IV read at day open, entries after the 30-min OR) and see whether
isolating the trending regime turns the edge positive.

Sweeps several min_iv thresholds vs the ungated baseline. Loads data ONCE.
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
SLIP = 0.0025

print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=SLIP))


def run(min_iv):
    strat = ORBSpread(width=300, lots=1, target=3000, stop=3000, min_iv=min_iv)
    res = bt.run(strat, START, END)
    tr = pd.DataFrame([{"date": t.date, "net": t.net, "reason": t.reason} for t in res.trades])
    tag = "BASELINE (no gate)" if min_iv is None else f"min_iv >= {min_iv:.2f}"
    if len(tr) == 0:
        print(f"\n=== ORB regime  {tag} ===  no trades"); return
    tr["yr"] = pd.to_datetime(tr["date"]).dt.year
    print(f"\n=== ORB regime  {tag}   slip {SLIP*100:.2f}%   {len(tr)} trades ===")
    print(f"net Rs{res.net():,.0f}   PF {res.profit_factor():.2f}   win {res.win_rate()*100:.0f}%   "
          f"t {res.t_stat():.2f}")
    for y, s in tr.groupby("yr"):
        pf = s[s.net > 0].net.sum() / -s[s.net <= 0].net.sum() if (s.net <= 0).any() else float("inf")
        print(f"   {y}: {len(s):>4} trades  net Rs{s.net.sum():>11,.0f}  PF {pf:.2f}")


for miv in (None, 0.12, 0.15, 0.18, 0.20):
    run(miv)
