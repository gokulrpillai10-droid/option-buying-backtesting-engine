"""CPR on a 3-MINUTE signal timeframe, deployable config (floor=10) + no-floor.

Same config/knobs as the validated 5-min: book +50 / book 4 of 6 / trail 2 by 12 /
far-edge CPR close stop / IV<=0.12 ; fill_price='open', slippage 0.25%. Only the
signal timeframe changes to 3min.
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
cat = Catalog(ROOT)


def dd(s):
    eq = s.sort_index().cumsum()
    return float((eq - eq.cummax()).min())


def run(tf, mult):
    spot = SpotSeries.from_index(ROOT, tf)
    md = MarketData(cat, spot, min_dte=3)
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025,
                                         timeframe=tf, min_volume_mult=mult))
    res = bt.run(CPRBreakout(book_prem=50, book_lots=4, trail_prem=12, stop_mode="far",
                             stop_prem=None, max_iv=0.12), START, END)
    t = pd.DataFrame([{"date": pd.Timestamp(x.date), "net": x.net, "reason": x.reason}
                      for x in res.trades])
    daily = t.groupby("date").net.sum()
    yrs = t.assign(yr=t.date.dt.year).groupby("yr").net.sum()
    red = int((yrs < 0).sum())
    print(f"\n=== timeframe={tf}  floor={mult:g} ===")
    print(f"  legs {len(t):>4}  trade-days {daily.size:>4}  net Rs{res.net():>10,.0f}  "
          f"PF {res.profit_factor():.2f}  win {res.win_rate()*100:.0f}%  t {res.t_stat():.2f}  "
          f"maxDD Rs{dd(daily):>9,.0f}  tail(excl5) Rs{res.tail_excl(5):>10,.0f}  redYrs {red}")
    print("  yearly:", "  ".join(f"{y}:{v:>+8,.0f}" for y, v in yrs.items()))
    print("  exits :", dict(t.reason.value_counts()))


print("--- CPR 3-MINUTE timeframe vs validated 5-min ---")
run("3min", 10)
run("3min", 0)
