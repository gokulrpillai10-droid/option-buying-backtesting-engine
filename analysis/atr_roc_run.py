"""Backtest ATR-ROC high-vol naked buy on BOTH 5-min and 15-min timeframes.
ATR14/ROC12, ATR>50 gate, 1-strike-OTM, 1 lot, +/-3000 P&L, 15:00 square-off.
Loads each timeframe's spot ONCE.
"""
from datetime import date

import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from strategies.atr_roc_buy import ATRROCBuy

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)

print("Loading catalog ...")
cat = Catalog(ROOT)


def run_tf(tf):
    print(f"\nLoading {tf} spot ...")
    spot = SpotSeries.from_index(ROOT, tf)
    md = MarketData(cat, spot, min_dte=3)
    for slip in (0.0025, 0.005):
        bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=slip, timeframe=tf))
        res = bt.run(ATRROCBuy(atr_period=14, roc_period=12, atr_min=50, lots=1,
                               target=3000, stop=3000), START, END)
        tr = pd.DataFrame([{"date": t.date, "net": t.net, "side": t.side, "reason": t.reason}
                           for t in res.trades])
        if len(tr) == 0:
            print(f"  {tf} slip {slip*100:.2f}%: NO TRADES (ATR>50 never triggered)"); continue
        tr["yr"] = pd.to_datetime(tr["date"]).dt.year
        print(f"\n=== ATR-ROC  {tf}  slip {slip*100:.2f}%  {len(tr)} trades ===")
        print(f"net Rs{res.net():,.0f}  PF {res.profit_factor():.2f}  win {res.win_rate()*100:.0f}%  "
              f"t {res.t_stat():.2f}")
        for y, s in tr.groupby("yr"):
            print(f"   {y}: {len(s):>4} trades  net Rs{s.net.sum():>10,.0f}")
        print("   exits:", dict(tr.reason.value_counts()), " side:", dict(tr.side.value_counts()))


run_tf("15min")
run_tf("5min")
