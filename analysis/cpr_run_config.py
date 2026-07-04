"""CPR breakout: CPR opposite-side CLOSE stop, target +50 premium (book 2/6), trail rest by 12.
The 'grow the winners' test -- same 66%-win stop, but book later (+50 vs +25) to lift avg win.
Reports avg win/loss vs the +25 baseline (avgWin 4,954 / avgLoss -16,651 / W/L 0.30).
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


def run(slip):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=slip))
    res = bt.run(CPRBreakout(book_prem=50, book_lots=2, trail_prem=12,
                             stop_mode="far", stop_prem=None), START, END)
    tr = pd.DataFrame([{"date": t.date, "net": t.net, "reason": t.reason} for t in res.trades])
    tr["yr"] = pd.to_datetime(tr["date"]).dt.year
    nets = tr.net.tolist()
    wins = [n for n in nets if n > 0]; loss = [n for n in nets if n <= 0]
    aw = sum(wins) / len(wins) if wins else 0
    al = sum(loss) / len(loss) if loss else 0
    print(f"\n=== CPR far-close stop, target +50 book2, trail4 x12  (slip {slip:.3f}, {len(tr)} legs) ===")
    print(f"net Rs{res.net():,.0f}  PF {res.profit_factor():.2f}  win {res.win_rate()*100:.0f}%  "
          f"t {res.t_stat():.2f}")
    print(f"avgWin {aw:,.0f}  avgLoss {al:,.0f}  W/L {(aw/-al if al else 0):.2f}   (was 0.30 at +25)")
    for y, s in tr.groupby("yr"):
        print(f"   {y}: {len(s):>4} legs  net {s.net.sum():>12,.0f}")
    print("   exits:", dict(tr.reason.value_counts()))


run(0.01)
run(0.005)
