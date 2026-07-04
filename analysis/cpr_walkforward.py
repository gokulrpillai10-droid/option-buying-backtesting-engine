"""Walk-forward test of the IV-filter edge (the parameter that made CPR +50 profitable).

Run the +50 config ONCE with NO IV filter, recording each day's open ATM IV. Then, for
each test year, select the IV threshold that maximised net on PRIOR years only (expanding
window) and apply it OUT-OF-SAMPLE to the test year. Filtering recorded days by recorded
day-IV exactly reproduces any max_iv run, so no re-running is needed. Compares the
walk-forward equity to the in-sample (hindsight) +518k. slip 0.25%. Loads data ONCE.
"""
from datetime import date

import numpy as np
import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from strategies.cpr_breakout import CPRBreakout

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)
GRID = [0.11, 0.12, 0.13, 0.14, 0.15, 0.16, 0.18, 0.20, 1.0]   # 1.0 = no filter

IVLOG = []


class WFRec(CPRBreakout):
    def on_day_start(self, ctx):
        super().on_day_start(ctx)
        self._rec_iv = self._day_open_iv(ctx) if self.active else None

    def on_bar(self, ctx):
        orders = super().on_bar(ctx)
        if orders:
            IVLOG.append({"date": pd.Timestamp(ctx.now.date()), "day_iv": self._rec_iv})
        return orders


print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025))

res = bt.run(WFRec(book_prem=50, book_lots=2, trail_prem=12, stop_mode="far",
                   stop_prem=None, max_iv=None), START, END)

tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net} for t in res.trades])
day = tr.groupby("date").net.sum().reset_index()
ivl = pd.DataFrame(IVLOG).drop_duplicates("date")
day = day.merge(ivl, on="date", how="left")
day["yr"] = day.date.dt.year


def net_at(d, x):                       # net if we keep days with IV<=x (NaN-IV always kept)
    m = d.day_iv.isna() | (d.day_iv <= x)
    return d[m].net.sum()


print("\n=== WALK-FORWARD (expanding window; pick max_iv on PRIOR years, trade next) ===")
print(f"{'test yr':<8}{'picked IV':>10}{'train net':>13}{'OOS net':>13}")
oos = []
for Y in range(2022, 2027):
    train = day[day.yr < Y]
    test = day[day.yr == Y]
    if len(train) < 50 or len(test) == 0:
        continue
    x_star = max(GRID, key=lambda x: net_at(train, x))
    onet = net_at(test, x_star)
    oos.append({"yr": Y, "x": x_star, "oos": onet, "n": (test.day_iv.isna() | (test.day_iv <= x_star)).sum()})
    print(f"{Y:<8}{x_star:>10.2f}{net_at(train, x_star):>13,.0f}{onet:>13,.0f}")

o = pd.DataFrame(oos)
wf_total = o.oos.sum()
print(f"\nWALK-FORWARD OOS total (2022-2026): Rs{wf_total:,.0f}")
print(f"  vs in-sample hindsight (fixed IV<=0.13, all yrs): Rs{net_at(day, 0.13):,.0f}")
print(f"  vs no filter at all (all crossing days):          Rs{net_at(day, 1.0):,.0f}")
print(f"  OOS drop-2025:                                    Rs{o[o.yr != 2025].oos.sum():,.0f}")

# rolling 2-year window variant
print("\n=== WALK-FORWARD (rolling 2-yr train) ===")
roll = []
for Y in range(2022, 2027):
    train = day[(day.yr >= Y - 2) & (day.yr < Y)]
    test = day[day.yr == Y]
    if len(train) < 50 or len(test) == 0:
        continue
    x_star = max(GRID, key=lambda x: net_at(train, x))
    roll.append(net_at(test, x_star))
    print(f"   {Y}: pick IV<={x_star:.2f}  OOS Rs{net_at(test, x_star):,.0f}")
print(f"rolling-2yr OOS total: Rs{sum(roll):,.0f}")
