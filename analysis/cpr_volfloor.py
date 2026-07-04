"""MIN-VOLUME FLOOR ROBUSTNESS TEST for the deployed CPR config.

Question: does the +Rs745k edge survive when we REJECT entries whose signal-minute volume
can't comfortably absorb our 6-lot order? If P&L barely moves, the edge does NOT lean on
the thin/un-fillable bars (mostly 2020) -> deployment-safe.

METHOD (post-hoc, exact): the strategy sets entered=True before the engine decides, so a
floored rejection = a dead day with NO re-entry. Thus a floored run == the no-floor run
MINUS the trades on days whose entry-minute volume < floor. So: run ONCE (deployed config),
record each entry day's signal-minute volume + required qty, then sweep floors in pandas.
Floors expressed as multiples k of our order size (volume >= k * req_qty). slip 0.25%.
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
FLOORS = [0, 1, 5, 10, 20, 50]      # k: require signal-minute volume >= k * order qty

ENTRY_VOL = []                       # per entry day: signal-minute volume of the deep-ITM strike


class VolRec(CPRBreakout):
    def on_bar(self, ctx):
        orders = super().on_bar(ctx)
        if orders:
            o = orders[0]
            vol = 0
            try:
                snap = ctx.snapshot()
                q = snap.call(o.strike) if o.side == "CALL" else snap.put(o.strike)
                vol = q.volume if q.is_traded else 0
            except Exception:
                vol = 0
            ENTRY_VOL.append({"date": pd.Timestamp(ctx.now.date()),
                              "volume": vol, "lots": o.lots})
        return orders


print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=SLIP))

print("Running DEPLOYED CPR config (max_iv=0.12) ...")
res = bt.run(VolRec(book_prem=50, book_lots=4, trail_prem=12, stop_mode="far",
                    stop_prem=None, max_iv=0.12), START, END)

# per-day net (sum the scale-out legs)
tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net} for t in res.trades])
daynet = tr.groupby("date").net.sum().reset_index()

ev = pd.DataFrame(ENTRY_VOL).drop_duplicates("date")
ev["lotsize"] = ev.date.dt.date.map(lot_size_for)
ev["req_qty"] = ev.lots * ev.lotsize

d = daynet.merge(ev, on="date", how="left")
d["yr"] = d.date.dt.year
d["volmult"] = d.volume / d.req_qty       # entry-minute volume / our order size


def stats(s):
    nets = s.net
    pf = nets[nets > 0].sum() / -nets[nets <= 0].sum() if (nets <= 0).any() else float("inf")
    t = nets.mean() / (nets.std() / np.sqrt(len(nets))) if len(nets) > 1 and nets.std() else 0
    return len(s), nets.sum(), pf, t


base_n = len(d)
base_net = d.net.sum()
print(f"\n=== MIN-VOLUME FLOOR SWEEP  (baseline = {base_n} trade-days, Rs{base_net:,.0f}) ===")
print(f"{'floor':<22}{'days':>6}{'dropped':>8}{'net Rs':>13}{'% kept':>8}{'PF':>6}{'t':>6}")
print("-" * 69)
for k in FLOORS:
    keep = d[d.volmult >= k] if k > 0 else d
    n, net, pf, t = stats(keep)
    label = "baseline (no floor)" if k == 0 else f"volume >= {k:>2}x order"
    print(f"{label:<22}{n:>6}{base_n - n:>8}{net:>13,.0f}{net/base_net*100:>7.0f}%{pf:>6.2f}{t:>6.2f}")

# where do the dropped trades' P&L sit? (are the thin bars winners or losers?)
print("\nP&L of trades DROPPED at each floor (negative = floor removes losers = good):")
for k in FLOORS[1:]:
    dropped = d[d.volmult < k]
    if len(dropped):
        print(f"   < {k:>2}x: drops {len(dropped):>3} days, their net = Rs{dropped.net.sum():>11,.0f}")

# by-year at the strictest practical floor (10x)
print("\nBy-year: baseline vs volume>=10x")
strict = d[d.volmult >= 10]
for y in sorted(d.yr.unique()):
    b = d[d.yr == y].net.sum()
    s = strict[strict.yr == y].net.sum()
    print(f"   {y}: baseline Rs{b:>11,.0f}   >=10x Rs{s:>11,.0f}   (kept {len(strict[strict.yr==y])}/{len(d[d.yr==y])})")
