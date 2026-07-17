"""FINAL TEAR SHEET for the deployed CPR strategy.

Config = the saved/validated default: CPRBreakout(book_prem=50, book_lots=4, trail_prem=12,
stop_mode='far', max_iv=0.12), deep-ITM ~150pt, 6 lots, slip 0.25%, full India F&O costs.

Reports the full quant pack: year-by-year, max DD, Sharpe/Sortino/Calmar, VaR/CVaR,
win ratio, daily & weekly returns. Ratios on TRADE-DAY net (annualized by sqrt(trade-days/yr)),
matching analysis/cpr_summary.py. Also recomputes everything under the realistic >=10x-volume
live-fill floor (validated in cpr_volfloor.py). Loads data ONCE.
"""
from datetime import date

import numpy as np
import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from engine.instruments import lot_size_for
from strategies.cpr_breakout import CPRBreakout

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)
SLIP = 0.0025

ENTRY_VOL = []


class Rec(CPRBreakout):
    def on_bar(self, ctx):
        orders = super().on_bar(ctx)
        if orders:
            o = orders[0]
            try:
                q = (ctx.snapshot().call(o.strike) if o.side == "CALL"
                     else ctx.snapshot().put(o.strike))
                vol = q.volume if q.is_traded else 0
            except Exception:
                vol = 0
            ENTRY_VOL.append({"date": pd.Timestamp(ctx.now.date()), "evol": vol, "lots": o.lots})
        return orders


print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=SLIP))

print("Running deployed CPR config (book4/trail2/+50/far/IV<=0.12, slip 0.25%) ...")
res = bt.run(Rec(book_prem=50, book_lots=4, trail_prem=12, stop_mode="far",
                 stop_prem=None, max_iv=0.12), START, END)

tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net, "entry": t.entry,
                    "qty": t.qty, "cost": t.cost}
                   for t in res.trades])
tr["outlay"] = tr.entry * tr.qty
day = tr.groupby("date").agg(net=("net", "sum"), outlay=("outlay", "sum"),
                             cost=("cost", "sum")).reset_index()
ev = pd.DataFrame(ENTRY_VOL).drop_duplicates("date")
ev["req"] = ev.lots * ev.date.dt.date.map(lot_size_for)
ev["vmult"] = ev.evol / ev.req
day = day.merge(ev[["date", "vmult"]], on="date", how="left").sort_values("date").reset_index(drop=True)
day["yr"] = day.date.dt.year
n_legs = len(tr)


def tearsheet(d, title):
    d = d.sort_values("date").reset_index(drop=True)
    eq = d.net.cumsum(); peak = eq.cummax(); dd = eq - peak
    maxdd = dd.min()
    yrs = (d.date.max() - d.date.min()).days / 365.25
    tdpy = len(d) / yrs
    mu, sd = d.net.mean(), d.net.std()
    dn = d.net[d.net < 0].std()
    ann = d.net.sum() / yrs
    sharpe = (mu / sd) * np.sqrt(tdpy) if sd else 0
    sortino = (mu / dn) * np.sqrt(tdpy) if dn else 0
    calmar = ann / -maxdd if maxdd else 0
    cap = d.outlay.quantile(0.9)                      # ~1-position capital
    ringfence = cap - maxdd                           # capital + DD buffer
    # VaR / CVaR on trade-day P&L (rupees) and as % of 1-position capital
    var95, var99 = d.net.quantile(0.05), d.net.quantile(0.01)
    cvar95 = d.net[d.net <= var95].mean()
    cvar99 = d.net[d.net <= var99].mean()
    # daily / weekly returns as % of capital deployed
    d["ret"] = d.net / d.outlay
    wk = d.set_index("date").net.resample("W-FRI").sum()
    wk_nonzero = wk[wk != 0]
    # FIX (review #3): count ACTIVE months only (months with trades), not every
    # calendar month -- resample('ME') zero-fills no-trade months and counts them as
    # losses, understating the positive-months rate for a ~46-trade/yr strategy.
    mo = d.groupby(d.date.dt.to_period("M")).net.sum()
    # FIX (review #2): leg-win for THIS sheet's days, not the global all-legs frame.
    legs = tr[tr.date.isin(set(d.date))]
    # underwater + losing streak
    uw = cur = 0
    for x in (dd < 0).astype(int):
        cur = cur + 1 if x else 0; uw = max(uw, cur)
    ls = cur = 0
    for nx in d.net:
        cur = cur + 1 if nx <= 0 else 0; ls = max(ls, cur)

    print(f"\n{'='*64}\n {title}\n{'='*64}")
    print(f"period {d.date.min().date()}..{d.date.max().date()}  ({yrs:.1f}y)   "
          f"trade-days {len(d)}   ~{tdpy:.0f}/yr")
    print(f"\n RETURNS")
    print(f"   net total            Rs{d.net.sum():>12,.0f}")
    print(f"   annualized           Rs{ann:>12,.0f}")
    print(f"   mean / trade-day     Rs{mu:>12,.0f}   (std Rs{sd:,.0f})")
    print(f"   avg weekly (active)  Rs{wk_nonzero.mean():>12,.0f}   ({len(wk_nonzero)} active wks)")
    print(f"   mean daily return     {d.ret.mean()*100:>11.2f}%  (on capital deployed)")
    print(f"   median daily return   {d.ret.median()*100:>11.2f}%")
    print(f"\n RISK-ADJUSTED   (Sharpe/Sortino on TRADE-DAY series, ann by sqrt({tdpy:.0f} td/yr);")
    print(f"                    NOT a calendar-daily Sharpe -- flat days excluded)")
    print(f"   Sharpe (trade-day,ann){sharpe:>11.2f}")
    print(f"   Sortino (trade-day)   {sortino:>11.2f}")
    print(f"   Calmar                {calmar:>11.2f}")
    print(f"\n DRAWDOWN / TAILS")
    print(f"   max drawdown         Rs{maxdd:>12,.0f}   ({-maxdd/cap*100:.0f}% of 1-pos capital)")
    print(f"   longest underwater    {uw} trade-days   longest losing streak {ls}")
    print(f"   VaR  95% (day)       Rs{var95:>12,.0f}   ({var95/cap*100:.1f}% of capital)")
    print(f"   CVaR 95% (day)       Rs{cvar95:>12,.0f}   ({cvar95/cap*100:.1f}%)  [expected shortfall]")
    print(f"   VaR  99% (day)       Rs{var99:>12,.0f}      CVaR 99% Rs{cvar99:,.0f}")
    print(f"   best day Rs{d.net.max():,.0f}   worst day Rs{d.net.min():,.0f}")
    print(f"\n WIN RATIOS")
    print(f"   trade-day win         {(d.net>0).mean()*100:>10.0f}%")
    print(f"   leg win               {(legs.net>0).mean()*100:>10.0f}%  (scale-out legs)")
    print(f"   positive weeks        {(wk_nonzero>0).mean()*100:>10.0f}%")
    print(f"   positive months       {(mo>0).mean()*100:>10.0f}%  ({len(mo)} active months)")
    print(f"\n CAPITAL")
    print(f"   median outlay/trade  Rs{d.outlay.median():>12,.0f}")
    print(f"   p90 outlay (1-pos)   Rs{cap:>12,.0f}")
    print(f"   ring-fence (cap+DD)  Rs{ringfence:>12,.0f}   ->  ROC {ann/ringfence*100:.0f}%/yr")
    gross = d.net.sum() + d.cost.sum()                # engine net is after costs
    print(f"\n COSTS (brokerage+STT+exchange+SEBI+stamp+GST; excl. the 0.25% slip)")
    print(f"   total costs          Rs{d.cost.sum():>12,.0f}")
    print(f"   avg / trade-day      Rs{d.cost.mean():>12,.0f}")
    print(f"   avg / leg            Rs{legs.cost.mean():>12,.0f}   ({len(legs)} legs)")
    print(f"   costs vs gross P&L    {d.cost.sum()/gross*100 if gross else 0:>10.0f}%  "
          f"(gross Rs{gross:,.0f} -> net Rs{d.net.sum():,.0f})")
    print(f"\n YEAR-BY-YEAR")
    print(f"   {'yr':<6}{'days':>5}{'net Rs':>13}{'win%':>6}{'maxDD Rs':>12}")
    for y, g in d.groupby("yr"):
        ge = g.net.cumsum(); gdd = (ge - ge.cummax()).min()
        print(f"   {y:<6}{len(g):>5}{g.net.sum():>13,.0f}{(g.net>0).mean()*100:>5.0f}%{gdd:>12,.0f}")
    print(f"   drop-2025 net Rs{d[d.yr!=2025].net.sum():,.0f}")


tearsheet(day, "DEPLOYED CONFIG (as backtested, all fillable entries)")
tearsheet(day[day.vmult >= 10].copy(), "REALISTIC LIVE FILL (volume >= 10x order floor)")
