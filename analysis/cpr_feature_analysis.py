"""New CPR config (+30 book 2/6, trail 4 by 12, premium stop -15, no CPR-close stop)
PLUS a winning-day vs losing-day feature analysis to find filters.

Records per-entry day-features (CPR width/ratio, prior-day range = vol regime, gap,
entry IV = vol level, intraday IV momentum, side, weekday), aggregates net to the DAY,
then (a) compares win vs loss means and (b) buckets each factor into terciles to show
where win-rate / net concentrates -> candidate filters. Loads data ONCE.
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

ENTRIES = []


class RecordingCPR(CPRBreakout):
    def on_day_start(self, ctx):
        super().on_day_start(ctx)
        self._day_open = ctx.spot_bar.open if self.active else None
        self._open_iv = None
        if self.active:
            try:
                k = ctx.atm_strike("CALL")
                self._open_iv = ctx.snapshot().call(k).iv if k is not None else None
            except Exception:
                self._open_iv = None

    def on_bar(self, ctx):
        orders = super().on_bar(ctx)
        if orders:
            o = orders[0]
            try:
                snap = ctx.snapshot()
                q = snap.call(o.strike) if o.side == "CALL" else snap.put(o.strike)
                iv = q.iv
            except Exception:
                iv = None
            hlc = ctx.prev_day_hlc()
            prior_range = (hlc[0] - hlc[1]) if hlc else np.nan
            pc = ctx.prev_close()
            width = self.top - self.bottom
            ENTRIES.append({
                "date": ctx.now.date(), "side": o.side,
                "cpr_width": width,
                "cpr_ratio": width / prior_range if prior_range else np.nan,
                "prior_range": prior_range,
                "gap_pct": (self._day_open - pc) / pc * 100 if pc else np.nan,
                "entry_iv": iv,
                "iv_mom": (iv - self._open_iv) if (iv is not None and self._open_iv is not None) else np.nan,
                "entry_hr": ctx.now.hour + ctx.now.minute / 60,
                "dow": ctx.now.weekday(),
            })
        return orders


print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.01))

res = bt.run(RecordingCPR(book_prem=30, book_lots=2, trail_prem=12,
                          stop_mode="none", stop_prem=15), START, END)

# ---- performance ----
tr = pd.DataFrame([{"date": t.date, "net": t.net} for t in res.trades])
tr["yr"] = pd.to_datetime(tr["date"]).dt.year
print(f"\n=== NEW CONFIG (+30 book2, trail 12, premium stop -15)  {len(tr)} legs ===")
print(f"net Rs{res.net():,.0f}  PF {res.profit_factor():.2f}  win {res.win_rate()*100:.0f}%  "
      f"t {res.t_stat():.2f}")
for y, s in tr.groupby("yr"):
    print(f"   {y}: {len(s):>4} legs  net {s.net.sum():>12,.0f}")

# ---- aggregate to the DAY, join features ----
day = tr.groupby("date")["net"].sum().reset_index().rename(columns={"net": "day_net"})
feat = pd.DataFrame(ENTRIES)
df = feat.merge(day, on="date", how="inner")
df["win"] = df["day_net"] > 0
NUM = ["cpr_width", "cpr_ratio", "prior_range", "gap_pct", "entry_iv", "iv_mom", "entry_hr"]

print(f"\n=== WIN vs LOSS days  ({df.win.sum()} win / {(~df.win).sum()} loss of {len(df)}) ===")
print(f"{'feature':<12}{'win mean':>12}{'loss mean':>12}{'diff':>10}")
for f in NUM:
    w, l = df[df.win][f].mean(), df[~df.win][f].mean()
    print(f"{f:<12}{w:>12.3f}{l:>12.3f}{w - l:>10.3f}")

# ---- tercile buckets: win-rate + net per factor band (the filter hunt) ----
print("\n=== TERCILE win-rate / avg-day-net per factor (low / mid / high) ===")
for f in NUM:
    s = df.dropna(subset=[f])
    if s[f].nunique() < 3:
        continue
    try:
        s = s.assign(b=pd.qcut(s[f], 3, labels=["low", "mid", "high"]))
    except ValueError:
        continue        # too many ties to form 3 clean terciles (e.g. entry_hr)
    print(f"\n{f}:")
    for b, g in s.groupby("b", observed=True):
        print(f"   {b:<5} n={len(g):>4}  win {g.win.mean()*100:>4.0f}%  "
              f"avg net {g.day_net.mean():>9,.0f}  range[{g[f].min():.2f},{g[f].max():.2f}]")

# ---- categoricals ----
print("\n=== side / weekday ===")
for f in ["side", "dow"]:
    for b, g in df.groupby(f):
        print(f"   {f}={b}: n={len(g):>4}  win {g.win.mean()*100:>4.0f}%  avg net {g.day_net.mean():>9,.0f}")
