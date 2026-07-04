"""Cross-check: 5-min vs 15-min CPR with the volume floor OFF (mult=0),
to confirm the 15-min underperformance is structural, not a floor artifact."""
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


def run(tf):
    spot = SpotSeries.from_index(ROOT, tf)
    md = MarketData(cat, spot, min_dte=3)
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025,
                                         timeframe=tf, min_volume_mult=0))
    res = bt.run(CPRBreakout(book_prem=50, book_lots=4, trail_prem=12, stop_mode="far",
                             stop_prem=None, max_iv=0.12), START, END)
    t = pd.DataFrame([{"date": pd.Timestamp(x.date), "net": x.net} for x in res.trades])
    daily = t.groupby("date").net.sum()
    yrs = t.assign(yr=t.date.dt.year).groupby("yr").net.sum()
    red = int((yrs < 0).sum())
    print(f"{tf:<6} NOFLOOR  legs {len(t):>4}  days {daily.size:>4}  net Rs{res.net():>10,.0f}  "
          f"PF {res.profit_factor():.2f}  t {res.t_stat():>5.2f}  maxDD Rs{dd(daily):>9,.0f}  redYrs {red}")
    print("        yearly:", "  ".join(f"{y}:{v:>+8,.0f}" for y, v in yrs.items()))


print("--- CPR deployable config, volume floor OFF, 5min vs 15min ---")
run("5min")
run("15min")
