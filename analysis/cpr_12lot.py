"""CPR at 12 LOTS on Rs5 lakh capital -- year-by-year returns, drawdowns, streaks.

12 lots (vs the saved 6) doubles per-trade P&L AND doubles order qty, so the >=10x
volume floor requires ~2x the minute-volume -> it rejects more thin days (NOT a clean
2x scale). We run it natively. book_lots=8 keeps the deployed 2/3 scale-out fraction
(8/12 == 4/6). Realistic live config: fill open, slip 0.25%, min_volume_mult=10.
"""
from datetime import date

import numpy as np
import pandas as pd

import strategies.cpr_breakout as cb          # set LOTS before constructing the strategy
cb.LOTS = 12

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from engine.instruments import lot_size_for
from strategies.cpr_breakout import CPRBreakout

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)
CAPITAL = 500_000

print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025, min_volume_mult=10))

print("Running CPR 12-lot, book8/trail4/+50/far/IV<=0.12, vol>=10x floor ...")
res = bt.run(CPRBreakout(book_prem=50, book_lots=8, trail_prem=12, stop_mode="far",
                         stop_prem=None, max_iv=0.12), START, END)

tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net, "entry": t.entry, "qty": t.qty}
                   for t in res.trades])
tr["outlay"] = tr.entry * tr.qty
day = tr.groupby("date").agg(net=("net", "sum"), outlay=("outlay", "sum")).reset_index()
day = day.sort_values("date").reset_index(drop=True)
day["yr"] = day.date.dt.year


def max_streak(mask):
    best = cur = 0
    for x in mask:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


eq = day.net.cumsum(); dd = eq - eq.cummax()
maxdd = dd.min()
yrs = (day.date.max() - day.date.min()).days / 365.25
ann = day.net.sum() / yrs
win_streak = max_streak(day.net > 0)
lose_streak = max_streak(day.net <= 0)

print(f"\n=== CPR 12-LOT on Rs{CAPITAL:,} capital  ({len(day)} trade-days / {yrs:.1f}y) ===")
print(f"net total            Rs{day.net.sum():>12,.0f}")
print(f"annualized           Rs{ann:>12,.0f}   ({ann/CAPITAL*100:.1f}% of Rs5L/yr)")
print(f"max drawdown         Rs{maxdd:>12,.0f}   ({-maxdd/CAPITAL*100:.1f}% of Rs5L)")
print(f"longest WIN streak   {win_streak:>3} trade-days")
print(f"longest LOSS streak  {lose_streak:>3} trade-days")
print(f"median capital/trade Rs{day.outlay.median():>12,.0f}   p90 Rs{day.outlay.quantile(0.9):,.0f}"
      f"   (max single Rs{day.outlay.max():,.0f})")
print(f"worst single day     Rs{day.net.min():>12,.0f}   best day Rs{day.net.max():,.0f}")

print(f"\n{'year':<6}{'days':>5}{'net Rs':>14}{'%5L':>7}{'maxDD Rs':>13}{'win%':>6}"
      f"{'Wstk':>6}{'Lstk':>6}")
print("-" * 63)
for y, g in day.groupby("yr"):
    g = g.sort_values("date")
    ge = g.net.cumsum(); gdd = (ge - ge.cummax()).min()
    ws = max_streak(g.net > 0); lstk = max_streak(g.net <= 0)
    print(f"{y:<6}{len(g):>5}{g.net.sum():>14,.0f}{g.net.sum()/CAPITAL*100:>6.0f}%"
          f"{gdd:>13,.0f}{(g.net>0).mean()*100:>5.0f}%{ws:>6}{lstk:>6}")
print(f"\ndrop-2025 net Rs{day[day.yr!=2025].net.sum():,.0f}")
