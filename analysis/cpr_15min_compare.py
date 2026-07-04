"""Validate the CPR strategy on a 15-MINUTE signal timeframe vs the deployed 5-min.

Same deployable config end-to-end (CPRBreakout() defaults: book +50 / book 4 of 6 /
trail 2 by 12 / far-edge CPR close stop / IV<=0.12) and the same realistic execution
knobs (fill_price='open', slippage 0.25%, min_volume_mult=10). ONLY the signal
timeframe changes: SpotSeries.from_index(ROOT, tf) + Knobs(timeframe=tf).

Reports net / PF / win / t-stat / trade-days / max intraday-equity drawdown / yearly.
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

print("Loading catalog ...")
cat = Catalog(ROOT)


def max_drawdown(daily_net: pd.Series) -> float:
    """Max peak-to-trough drawdown of the cumulative day-by-day equity curve."""
    eq = daily_net.sort_index().cumsum()
    return float((eq - eq.cummax()).min())


def run(tf: str, mult: float = 10.0):
    spot = SpotSeries.from_index(ROOT, tf)
    md = MarketData(cat, spot, min_dte=3)
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025,
                                         timeframe=tf, min_volume_mult=mult))
    res = bt.run(CPRBreakout(book_prem=50, book_lots=4, trail_prem=12, stop_mode="far",
                             stop_prem=None, max_iv=0.12), START, END)
    if not res.trades:
        print(f"{tf:<7} NO TRADES"); return
    t = pd.DataFrame([{"date": pd.Timestamp(x.date), "net": x.net,
                       "reason": x.reason} for x in res.trades])
    daily = t.groupby("date").net.sum()
    dd = max_drawdown(daily)
    print(f"\n=== timeframe={tf}  (slip 0.25%, min_volume_mult={mult:g}) ===")
    print(f"  legs {len(t):>4}   trade-days {daily.size:>4}   net Rs{res.net():>11,.0f}")
    print(f"  PF {res.profit_factor():.2f}   win {res.win_rate()*100:.0f}%   "
          f"t {res.t_stat():.2f}   maxDD Rs{dd:>10,.0f}   "
          f"tail(excl top5) Rs{res.tail_excl(5):>11,.0f}")
    t["yr"] = t.date.dt.year
    yrs = t.groupby("yr").net.sum()
    print("  yearly:", "  ".join(f"{y}:{v:>+9,.0f}" for y, v in yrs.items()))
    print("  exits :", dict(t.reason.value_counts()))


print("\n" + "=" * 78)
print("CPR DEPLOYABLE CONFIG  -  5-min (validated baseline) vs 15-min")
print("=" * 78)
run("5min")
run("15min")
