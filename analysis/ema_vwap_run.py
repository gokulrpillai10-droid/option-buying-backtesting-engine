"""EMA 9/21 crossover -- 100-pt ITM, stop -15 / target +35 premium points, 6 lots.
User spec 2026-06-24. Detailed quant report + trades CSV. Primary slip 0.25%.
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

strat = EMACrossover(stop_points=15.0, target_points=35.0, itm_offset=100, lots=6, max_trades=3, use_vwap=True,
                     entry_start=time(9, 40), entry_end=time(14, 55), square_off=time(14, 58))
res = bt.run(strat, START, END)
tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "side": t.side, "strike": t.strike,
                    "entry": t.entry, "qty": t.qty, "net": t.net, "reason": t.reason}
                   for t in res.trades])
tr.to_csv("analysis/ema_vwap_trades.csv", index=False)
tr["yr"] = tr["date"].dt.year


def block(s):
    w, l = s[s.net > 0].net, s[s.net <= 0].net
    pf = w.sum() / -l.sum() if l.sum() != 0 else float("inf")
    return (len(s), s.net.sum(), len(w)/len(s)*100, pf,
            w.mean() if len(w) else 0, l.mean() if len(l) else 0, s.net.mean())


def maxdd(eq):
    return (eq - np.maximum.accumulate(eq)).min()


def streak(net):
    cur = best = 0
    for x in net:
        cur = cur+1 if x <= 0 else 0
        best = max(best, cur)
    return best


print(f"\n{'='*80}\nEMA 9/21  ·  100-pt ITM  ·  stop -15 / target +35 premium pts  ·  6 lots  ·  MAX 3/day  ·  VWAP-gated  ·  slip {SLIP*100:.2f}%")
print(f"entries 09:40-14:55 (EMA warm 09:15)  ·  square 14:58  ·  2020-2026")
print('='*80)
hdr = f"{'Year':<6}{'Trades':>7}{'Net Rs':>14}{'Win%':>7}{'PF':>6}{'AvgWin':>10}{'AvgLoss':>10}{'Exp/tr':>9}"
print("\nYEAR-BY-YEAR\n" + hdr + "\n" + "-"*len(hdr))
for y, s in tr.groupby("yr"):
    n, net, wr, pf, aw, al, exp = block(s)
    print(f"{y:<6}{n:>7}{net:>14,.0f}{wr:>6.0f}%{pf:>6.2f}{aw:>10,.0f}{al:>10,.0f}{exp:>9,.0f}")
n, net, wr, pf, aw, al, exp = block(tr)
print("-"*len(hdr))
print(f"{'ALL':<6}{n:>7}{net:>14,.0f}{wr:>6.0f}%{pf:>6.2f}{aw:>10,.0f}{al:>10,.0f}{exp:>9,.0f}")

daily = tr.groupby("date").net.sum().sort_index()
eq = tr.sort_values("date").net.cumsum().values
sharpe = daily.mean()/daily.std()*np.sqrt(252) if daily.std() > 0 else 0
dn = daily[daily < 0].std()
sortino = daily.mean()/dn*np.sqrt(252) if dn > 0 else 0
dd = maxdd(eq)
print("\nPORTFOLIO QUANT METRICS (6 lots)")
print(f"  Net P&L                 Rs{tr.net.sum():>13,.0f}")
print(f"  Trades / days           {len(tr):>7,} / {len(daily)}")
print(f"  Profit factor           {pf:>13.2f}")
print(f"  Win rate                {wr:>12.1f}%   (breakeven needs {15/(15+35)*100:.0f}% at 35:15)")
print(f"  Expectancy / trade      Rs{exp:>12,.0f}")
print(f"  Avg win / Avg loss      Rs{aw:,.0f} / Rs{al:,.0f}   (W/L {aw/-al:.2f})")
print(f"  t-statistic             {tr.net.mean()/tr.net.std()*np.sqrt(len(tr)):>13.2f}")
print(f"  Sharpe (daily, ann.)    {sharpe:>13.2f}")
print(f"  Sortino (daily, ann.)   {sortino:>13.2f}")
print(f"  Max drawdown            Rs{dd:>13,.0f}")
print(f"  Max consec. losses      {streak(tr.sort_values('date').net.values):>13}")
print(f"  Net excl. top-10 wins   Rs{tr.net.sum()-tr.net.nlargest(10).sum():>13,.0f}")
print(f"  Avg entry premium       Rs{tr.entry.mean():>13,.1f}   (qty/trade {tr.qty.mean():.0f})")

print("\nBY SIDE")
for sd, s in tr.groupby("side"):
    n, net, wr2, pf2, *_ = block(s)
    print(f"  {sd:<5} {n:>5}  net Rs{net:>12,.0f}  win {wr2:.0f}%  PF {pf2:.2f}")
print("\nBY EXIT REASON")
for rs, s in tr.groupby("reason"):
    print(f"  {rs:<8} {len(s):>5}  net Rs{s.net.sum():>12,.0f}  avg Rs{s.net.mean():>7,.0f}  ({len(s)/len(tr)*100:.0f}%)")
