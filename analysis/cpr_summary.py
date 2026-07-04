"""One-page summary of the CURRENT strategy (walk-forward-endorsed config):
CPR cross, deep-ITM, 6 lots, CPR opposite-close stop, target +50 book 2/6, trail 4 by 12,
low-IV day filter IV<=0.12. Performance + quant ratios + max drawdown. slip 0.25% central.
Loads data ONCE.
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
MAX_IV = 0.12

print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)


def run(slip):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=slip))
    return bt.run(CPRBreakout(book_prem=50, book_lots=2, trail_prem=12, stop_mode="far",
                              stop_prem=None, max_iv=MAX_IV), START, END)


print("\n=== CURRENT STRATEGY: CPR +50 / book2 / trail12 / far-stop / IV<=0.12 ===")
print("net across slippage:")
results = {}
for s in (0.001, 0.0025, 0.005, 0.01):
    results[s] = run(s)
for s, res in results.items():
    print(f"   slip {s*100:>4.2f}%  net Rs{res.net():>11,.0f}  PF {res.profit_factor():.2f}  "
          f"win(leg) {res.win_rate()*100:.0f}%")

res = results[0.0025]
tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net, "entry": t.entry, "qty": t.qty}
                   for t in res.trades])
tr["outlay"] = tr.entry * tr.qty
day = tr.groupby("date").agg(net=("net", "sum"), outlay=("outlay", "sum")).reset_index()
day["yr"] = day.date.dt.year
day = day.sort_values("date").reset_index(drop=True)

eq = day.net.cumsum(); peak = eq.cummax(); dd = eq - peak
maxdd = dd.min()
under = (dd < 0).astype(int); r = uw = 0
for u in under:
    r = r + 1 if u else 0; uw = max(uw, r)
ls = cur = 0
for n in day.net:
    cur = cur + 1 if n <= 0 else 0; ls = max(ls, cur)
yrs = (day.date.max() - day.date.min()).days / 365.25
ann = day.net.sum() / yrs
mu, sd = day.net.mean(), day.net.std()
dn = day.net[day.net < 0].std()
tdpy = len(day) / yrs
sharpe = (mu / sd) * np.sqrt(tdpy) if sd else 0
sortino = (mu / dn) * np.sqrt(tdpy) if dn else 0
calmar = ann / -maxdd if maxdd else 0
cap = day.outlay.quantile(0.9)
m = day.set_index("date").net.resample("ME").sum()

print(f"\n--- RISK / QUANT RATIOS (0.25% slip, {len(day)} trade-days / {yrs:.1f}y) ---")
print(f"net Rs{day.net.sum():,.0f}   annual Rs{ann:,.0f}   per-day mean Rs{mu:,.0f}  std Rs{sd:,.0f}")
print(f"Sharpe(ann) {sharpe:.2f}    Sortino(ann) {sortino:.2f}    Calmar {calmar:.2f}")
print(f"max drawdown Rs{maxdd:,.0f}  ({-maxdd/cap*100:.0f}% of 1-position capital)   "
      f"underwater {uw} trade-days   longest losing streak {ls}")
print(f"trade-day win {(day.net>0).mean()*100:.0f}%   positive months {(m>0).mean()*100:.0f}%   "
      f"best day Rs{day.net.max():,.0f}  worst day Rs{day.net.min():,.0f}")
print(f"per-trade outlay (capital): median Rs{day.outlay.median():,.0f}  p90 Rs{cap:,.0f}   "
      f"~ROC {ann/(cap - maxdd)*100:.0f}%/yr")

print("\n--- year-by-year ---")
for y, g in day.groupby("yr"):
    print(f"   {y}: {len(g):>3} days  net Rs{g.net.sum():>11,.0f}")
print(f"   drop-2025 net Rs{day[day.yr != 2025].net.sum():,.0f}")
