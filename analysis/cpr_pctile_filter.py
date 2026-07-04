"""CONFIRMATORY TEST: adaptive rolling-IV-PERCENTILE filter vs the fixed absolute IV<=0.12.

Framing (per the analysis): this is a ROBUSTNESS/MECHANISM check, not a parameter hunt.
Question: does selecting days by RELATIVE IV (bottom-p percentile of a trailing window)
pick ~the same days and earn ~the same P&L as the fixed absolute 0.12 cutoff?
  - If YES  -> the edge is genuinely the relative-low-vol effect; the percentile version
               is a free, more regime-robust deployment choice.
  - If WORSE -> ABSOLUTE premium cost is the binding constraint (the option-BUYING
               friction wall), and the fixed threshold is causally correct -> keep it.

Efficiency trick (from cpr_walkforward.py): run the +50 config ONCE with NO IV filter,
recording EVERY day's open ATM IV in on_day_start. Any IV gate is then applied post-hoc
in pandas -- no re-running. Percentile is computed look-ahead-safe (strictly PRIOR days).
slip 0.25%. Loads data ONCE.
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
SLIP = 0.0025
FIXED_IV = 0.12
LOOKBACK = 252          # trailing window of trading days for the percentile rank
MIN_HIST = 60           # need at least this many prior IV obs to rank (else skip the day)
PCTILES = [0.20, 0.30, 0.40]

ALLDAY_IV = []          # day-open ATM IV for EVERY trading day (for the percentile distribution)


class Rec(CPRBreakout):
    def on_day_start(self, ctx):
        super().on_day_start(ctx)
        ALLDAY_IV.append({"date": pd.Timestamp(ctx.now.date()),
                          "day_iv": self._day_open_iv(ctx)})


print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=SLIP))

print("Running CPR +50 config, NO IV filter (records every day's IV) ...")
res = bt.run(Rec(book_prem=50, book_lots=4, trail_prem=12, stop_mode="far",
                 stop_prem=None, max_iv=None), START, END)

# --- per-day net (sum legs) ---
tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net} for t in res.trades])
daynet = tr.groupby("date").net.sum().reset_index()

# --- every trading day's IV, look-ahead-safe rolling percentile rank ---
ivd = pd.DataFrame(ALLDAY_IV).drop_duplicates("date").sort_values("date").reset_index(drop=True)
ranks = []
for i in range(len(ivd)):
    today = ivd.day_iv.iloc[i]
    win = ivd.day_iv.iloc[max(0, i - LOOKBACK):i].dropna()   # STRICTLY prior days
    if pd.isna(today) or len(win) < MIN_HIST:
        ranks.append(np.nan)
    else:
        ranks.append(float((win <= today).mean()))           # fraction of prior days <= today
ivd["pct_rank"] = ranks

d = daynet.merge(ivd, on="date", how="left")                  # entry days only, with IV + rank
d["yr"] = d.date.dt.year


def summary(mask, label):
    s = d[mask]
    if len(s) == 0:
        print(f"{label:<26} no days"); return None
    nets = s.net
    pf = nets[nets > 0].sum() / -nets[nets <= 0].sum() if (nets <= 0).any() else float("inf")
    t = nets.mean() / (nets.std() / np.sqrt(len(nets))) if nets.std() else 0
    print(f"{label:<26}{len(s):>5}{nets.sum():>13,.0f}{pf:>7.2f}{t:>7.2f}")
    return s


def by_year(s, label):
    yr = s.groupby("yr").net.sum()
    cells = "  ".join(f"{y}:{v/1000:>5.0f}k" for y, v in yr.items())
    print(f"   {label}: {cells}")


print(f"\n{'filter':<26}{'days':>5}{'net Rs':>13}{'PF':>7}{'t':>7}")
print("-" * 58)

base = summary(d.day_iv.notna() | d.day_iv.isna(), "baseline (no filter)")

# Fixed absolute 0.12 (keep NaN-IV days, matching project convention)
fixed_mask = d.day_iv.isna() | (d.day_iv <= FIXED_IV)
fixed = summary(fixed_mask, f"FIXED  IV<={FIXED_IV}")

# Rolling percentile variants. FIX (review #1): SKIP un-rankable days (NaN rank =
# warmup <MIN_HIST prior obs, or NaN IV) rather than keeping them. Keeping them forced
# the filter to trade the Jan-Mar 2020 COVID high-IV crash unfiltered, inflating its
# losses. Conservative skip matches cpr_robustness_check's no-data convention.
pct_masks = {}
for p in PCTILES:
    m = d.pct_rank.notna() & (d.pct_rank <= p)
    pct_masks[p] = m
    summary(m, f"PCTILE bottom {int(p*100)}% (L{LOOKBACK})")

print("\nBy-year (k = thousands):")
by_year(d[fixed_mask], f"FIXED 0.12          ")
for p in PCTILES:
    by_year(d[pct_masks[p]], f"PCTILE bottom {int(p*100)}%     ")

# --- composition overlap: do the two filters pick the SAME days? ---
print("\nDay-selection overlap vs FIXED 0.12:")
fset = set(d[fixed_mask].date)
for p in PCTILES:
    pset = set(d[pct_masks[p]].date)
    inter = len(fset & pset)
    union = len(fset | pset)
    jac = inter / union if union else 0
    print(f"   bottom {int(p*100)}%: fixed={len(fset)} pctile={len(pset)} "
          f"shared={inter} jaccard={jac:.2f}")
