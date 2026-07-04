"""Detailed quant report for the EMA 9/21 +/-5 premium-point scalp (user spec).
Runs once at 0.25% slip (primary), saves trades CSV, prints full per-year + portfolio stats.
"""
from datetime import date, time

import numpy as np
import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from strategies.ema_crossover import EMACrossover

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)
SLIP = 0.0025

print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=SLIP))

strat = EMACrossover(exit_points=5.0, entry_start=time(9, 40),
                     entry_end=time(14, 55), square_off=time(14, 58))
res = bt.run(strat, START, END)
tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "side": t.side, "strike": t.strike,
                    "entry": t.entry, "qty": t.qty, "net": t.net, "reason": t.reason}
                   for t in res.trades])
tr.to_csv("analysis/ema_5pt_trades.csv", index=False)
tr["yr"] = tr["date"].dt.year


def block(s):
    w = s[s.net > 0].net
    l = s[s.net <= 0].net
    pf = w.sum() / -l.sum() if l.sum() != 0 else float("inf")
    avg_w = w.mean() if len(w) else 0.0
    avg_l = l.mean() if len(l) else 0.0
    wl = avg_w / -avg_l if avg_l != 0 else float("inf")
    exp = s.net.mean()
    wr = len(w) / len(s) * 100
    return len(s), s.net.sum(), wr, pf, avg_w, avg_l, wl, exp


def maxdd(equity):
    peak = np.maximum.accumulate(equity)
    return (equity - peak).min()


def maxlosestreak(net):
    cur = best = 0
    for x in net:
        cur = cur + 1 if x <= 0 else 0
        best = max(best, cur)
    return best


print(f"\n{'='*78}\nEMA 9/21 crossover  ·  +/-5 premium-point bracket  ·  1 lot  ·  slip {SLIP*100:.2f}%")
print(f"entries 09:40-14:55 (EMAs warm from 09:15)  ·  square-off 14:58  ·  2020-2026")
print('='*78)

# ---- per-year table ----
hdr = f"{'Year':<6}{'Trades':>7}{'Net Rs':>13}{'Win%':>7}{'PF':>6}{'AvgWin':>9}{'AvgLoss':>9}{'W/L':>6}{'Exp/tr':>8}"
print("\nYEAR-BY-YEAR\n" + hdr + "\n" + "-"*len(hdr))
for y, s in tr.groupby("yr"):
    n, net, wr, pf, aw, al, wl, exp = block(s)
    print(f"{y:<6}{n:>7}{net:>13,.0f}{wr:>6.0f}%{pf:>6.2f}{aw:>9,.0f}{al:>9,.0f}{wl:>6.2f}{exp:>8,.0f}")
n, net, wr, pf, aw, al, wl, exp = block(tr)
print("-"*len(hdr))
print(f"{'ALL':<6}{n:>7}{net:>13,.0f}{wr:>6.0f}%{pf:>6.2f}{aw:>9,.0f}{al:>9,.0f}{wl:>6.2f}{exp:>8,.0f}")

# ---- portfolio quant metrics ----
daily = tr.groupby("date").net.sum().sort_index()
equity = tr.sort_values("date").net.cumsum().values
sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0.0
downside = daily[daily < 0].std()
sortino = daily.mean() / downside * np.sqrt(252) if downside > 0 else 0.0
dd = maxdd(equity)
calmar = (tr.net.sum() / 6.2) / -dd if dd < 0 else float("inf")
top10 = tr.net.nlargest(10).sum()

print("\nPORTFOLIO QUANT METRICS")
print(f"  Net P&L                 Rs{tr.net.sum():>12,.0f}")
print(f"  Trades                  {len(tr):>13,}   ({len(daily)} trading days)")
print(f"  Profit factor           {pf:>13.2f}")
print(f"  Win rate                {wr:>12.1f}%")
print(f"  Expectancy / trade      Rs{exp:>12,.0f}")
print(f"  Avg win / Avg loss      Rs{aw:>6,.0f} / Rs{al:,.0f}   (W/L {wl:.2f})")
print(f"  t-statistic             {tr.net.mean()/tr.net.std()*np.sqrt(len(tr)):>13.2f}")
print(f"  Sharpe (daily, ann.)    {sharpe:>13.2f}")
print(f"  Sortino (daily, ann.)   {sortino:>13.2f}")
print(f"  Calmar                  {calmar:>13.2f}")
print(f"  Max drawdown            Rs{dd:>12,.0f}")
print(f"  Max consecutive losses  {maxlosestreak(tr.sort_values('date').net.values):>13}")
print(f"  Best / Worst trade      Rs{tr.net.max():>6,.0f} / Rs{tr.net.min():,.0f}")
print(f"  Net excl. top-10 wins   Rs{tr.net.sum()-top10:>12,.0f}")

# ---- by side / by exit reason ----
print("\nBY SIDE")
for sd, s in tr.groupby("side"):
    n, net, wr2, pf2, *_ = block(s)
    print(f"  {sd:<5} {n:>5} trades  net Rs{net:>11,.0f}  win {wr2:.0f}%  PF {pf2:.2f}")
print("\nBY EXIT REASON")
for rs, s in tr.groupby("reason"):
    print(f"  {rs:<8} {len(s):>5} trades  net Rs{s.net.sum():>11,.0f}  "
          f"avg Rs{s.net.mean():>6,.0f}  ({len(s)/len(tr)*100:.0f}%)")
