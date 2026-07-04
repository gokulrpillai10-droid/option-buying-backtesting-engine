"""Quant robustness + risk workup for the IV<=0.13 CPR config at realistic slippage.

Equity curve, max drawdown (+ duration), Sharpe/Sortino/Calmar, losing streaks,
monthly stats, year split / drop-2025 / half-split robustness, and capital / ROC for
a deployability read. Daily = per trade-day (the strategy is flat most of the session).
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

print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)


def trades_at(slip):
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=slip))
    res = bt.run(CPRBreakout(book_prem=50, book_lots=2, trail_prem=12,
                             stop_mode="far", stop_prem=None, max_iv=0.13), START, END)
    return pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net,
                          "entry": t.entry, "qty": t.qty} for t in res.trades])


# net across cost assumptions (shape of DD is ~cost-insensitive; use 0.25% as central)
for s in (0.001, 0.0025, 0.005):
    print(f"  slip {s*100:.2f}%  net Rs{trades_at(s).net.sum():,.0f}")

tr = trades_at(0.0025)
tr["outlay"] = tr.entry * tr.qty
day = tr.groupby("date").agg(net=("net", "sum"), outlay=("outlay", "sum")).reset_index()
day["yr"] = day.date.dt.year
day = day.sort_values("date").reset_index(drop=True)

# ---- equity / drawdown ----
eq = day.net.cumsum()
peak = eq.cummax()
dd = eq - peak
maxdd = dd.min()
# underwater duration (longest run of consecutive trade-days below the prior peak)
under = (dd < 0).astype(int)
run = uw = 0
for u in under:
    run = run + 1 if u else 0
    uw = max(uw, run)
# losing streak (consecutive negative trade-days)
ls = cur = 0
for n in day.net:
    cur = cur + 1 if n <= 0 else 0
    ls = max(ls, cur)

yrs = (day.date.max() - day.date.min()).days / 365.25
ann = day.net.sum() / yrs
mu, sd = day.net.mean(), day.net.std()
dn = day.net[day.net < 0].std()
tdpy = len(day) / yrs
sharpe = (mu / sd) * np.sqrt(tdpy) if sd else 0
sortino = (mu / dn) * np.sqrt(tdpy) if dn else 0
calmar = ann / -maxdd if maxdd else 0

print(f"\n=== RISK (0.25% slip, {len(day)} trade-days over {yrs:.1f}y) ===")
print(f"net Rs{day.net.sum():,.0f}   annual Rs{ann:,.0f}   per-day mean Rs{mu:,.0f} std Rs{sd:,.0f}")
print(f"max drawdown Rs{maxdd:,.0f}   underwater {uw} trade-days   longest losing streak {ls} days")
print(f"Sharpe(ann) {sharpe:.2f}   Sortino(ann) {sortino:.2f}   Calmar {calmar:.2f}")
print(f"trade-day win rate {(day.net>0).mean()*100:.0f}%   "
      f"best Rs{day.net.max():,.0f}  worst Rs{day.net.min():,.0f}")

# ---- monthly ----
m = day.set_index("date").net.resample("ME").sum()
print(f"\nmonths: {len(m)}   positive {(m>0).mean()*100:.0f}%   "
      f"best Rs{m.max():,.0f}  worst Rs{m.min():,.0f}   median Rs{m.median():,.0f}")

# ---- robustness splits ----
print("\n=== ROBUSTNESS ===")
by = day.groupby("yr").net.agg(["sum", "count"])
for y, r in by.iterrows():
    print(f"   {y}: {int(r['count']):>3} days  net Rs{r['sum']:>11,.0f}")
print(f"   drop-2025          net Rs{day[day.yr != 2025].net.sum():>11,.0f}")
print(f"   2020-2022 (first)  net Rs{day[day.yr <= 2022].net.sum():>11,.0f}")
print(f"   2023-2026 (second) net Rs{day[day.yr >= 2023].net.sum():>11,.0f}")

# ---- capital / ROC ----
cap = day.outlay.quantile(0.9)            # ~capital to fund one position (p90 premium outlay)
buffer = cap - maxdd                       # position + drawdown buffer
print(f"\n=== CAPITAL ===")
print(f"per-trade premium outlay: median Rs{day.outlay.median():,.0f}  p90 Rs{cap:,.0f}")
print(f"working capital (position + max DD buffer) ~ Rs{buffer:,.0f}")
print(f"annual return on that capital ~ {ann / buffer * 100:.0f}%")
