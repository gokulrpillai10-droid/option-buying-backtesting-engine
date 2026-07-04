"""Compare lot-split at the +50 target on the current config (IV<=0.12, slip 0.25%):
  A) book 2 / trail 4   (current)
  B) book 4 / trail 2   (book more early, trail less)
Full risk metrics side by side. Loads data ONCE.
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
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025))


def metrics(book_lots):
    res = bt.run(CPRBreakout(book_prem=50, book_lots=book_lots, trail_prem=12,
                             stop_mode="far", stop_prem=None, max_iv=0.12), START, END)
    tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net} for t in res.trades])
    day = tr.groupby("date").net.sum().sort_index()
    eq = day.cumsum(); maxdd = (eq - eq.cummax()).min()
    ls = cur = 0
    for n in day:
        cur = cur + 1 if n <= 0 else 0; ls = max(ls, cur)
    yrs = (day.index.max() - day.index.min()).days / 365.25
    ann = day.sum() / yrs
    mu, sd = day.mean(), day.std()
    dn = day[day < 0].std()
    tdpy = len(day) / yrs
    drop25 = day[day.index.year != 2025].sum()
    m = day.resample("ME").sum()
    return {
        "net": res.net(), "PF": res.profit_factor(), "win_leg": res.win_rate() * 100,
        "annual": ann, "sharpe": (mu / sd) * np.sqrt(tdpy), "sortino": (mu / dn) * np.sqrt(tdpy),
        "calmar": ann / -maxdd, "maxdd": maxdd, "streak": ls,
        "day_win": (day > 0).mean() * 100, "pos_mo": (m > 0).mean() * 100,
        "worst_day": day.min(), "best_day": day.max(), "drop25": drop25,
        "by_year": day.groupby(day.index.year).sum(),
    }


A = metrics(2)
B = metrics(4)
rows = [("net Rs", "net", ",.0f"), ("annual Rs", "annual", ",.0f"), ("PF", "PF", ".2f"),
        ("Sharpe(ann)", "sharpe", ".2f"), ("Sortino(ann)", "sortino", ".2f"),
        ("Calmar", "calmar", ".2f"), ("max DD Rs", "maxdd", ",.0f"),
        ("longest lose streak", "streak", ".0f"), ("win leg %", "win_leg", ".0f"),
        ("trade-day win %", "day_win", ".0f"), ("positive months %", "pos_mo", ".0f"),
        ("worst day Rs", "worst_day", ",.0f"), ("best day Rs", "best_day", ",.0f"),
        ("drop-2025 Rs", "drop25", ",.0f")]

print(f"\n{'metric':<22}{'A: book2/trail4':>18}{'B: book4/trail2':>18}")
print("-" * 58)
for label, key, fmt in rows:
    print(f"{label:<22}{format(A[key], fmt):>18}{format(B[key], fmt):>18}")

print(f"\n{'year':<8}{'book2/trail4':>16}{'book4/trail2':>16}")
for y in A["by_year"].index:
    print(f"{y:<8}{A['by_year'][y]:>16,.0f}{B['by_year'].get(y,0):>16,.0f}")
