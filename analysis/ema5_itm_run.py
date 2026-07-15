"""5-EMA deviation strategy on NIFTY via ITM option buying — engine backtest.

PUT side on 5-min signals, CALL side on 15-min signals (separate runs, per
the source strategy's timeframe split). Validated knobs: next-open fills,
0.25% slippage/side, 10x signal-bar volume floor, min_dte=3.

Run: PYTHONPATH=<repo> python3 analysis/ema5_itm_run.py [START_YEAR] [END_YEAR]
"""
import sys
from datetime import date

import pandas as pd

from engine.backtest import Backtest, Knobs
from engine.data.catalog import Catalog
from engine.data.market import MarketData
from engine.data.spot import SpotSeries
from engine.execution.costs import CostModel
from strategies.ema5_itm import Ema5Itm

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START = date(int(sys.argv[1]) if len(sys.argv) > 1 else 2020, 1, 1)
END = date(int(sys.argv[2]) if len(sys.argv) > 2 else 2026, 12, 31)


def run_side(side, timeframe, rr):
    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, timeframe)
    md = MarketData(cat, spot, min_dte=3)
    bt = Backtest(md, CostModel(),
                  Knobs(fill_price="open", slippage_pct=0.0025,
                        timeframe=timeframe, min_volume_mult=10.0))
    res = bt.run(Ema5Itm(side=side, itm_offset=100, rr=rr, lots=1), START, END)
    return res


def report(tag, res):
    t = pd.DataFrame([{"date": x.date, "net": x.net, "reason": x.reason}
                      for x in res.trades])
    if len(t) == 0:
        print(f"{tag}: no trades"); return
    t["yr"] = pd.to_datetime(t["date"]).dt.year
    print(f"\n{tag}  ({len(t)} trades)")
    print(f"{'Year':<6}{'Trades':>7}{'Win%':>6}{'Net Rs':>12}{'PF':>7}")
    for y, s in t.groupby("yr"):
        W = s[s.net > 0]
        pf = (W.net.sum() / -s[s.net <= 0].net.sum()
              if (s.net <= 0).any() else float("inf"))
        print(f"{y:<6}{len(s):>7}{len(W)/len(s)*100:>5.0f}%{s.net.sum():>12,.0f}{pf:>7.2f}")
    print(f"{'ALL':<6}{len(t):>7}{res.win_rate()*100:>5.0f}%{res.net():>12,.0f}"
          f"{res.profit_factor():>7.2f}")
    print(f"t-stat {res.t_stat():.2f} | tail (excl top-10) Rs{res.tail_excl(10):,.0f}")
    print("exits:", dict(t.reason.value_counts()))


if __name__ == "__main__":
    for side, tf in [("PUT", "5min"), ("CALL", "15min")]:
        print(f"\n{'='*60}\nRunning {side} side on {tf} (RR 1:3) ...")
        report(f"5-EMA ITM {side} ({tf})", run_side(side, tf, rr=3.0))
