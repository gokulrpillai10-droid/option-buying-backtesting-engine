"""Characterize HDFCBANK gap-and-go (no-fill) days vs gap-fill days, and test
NIFTY co-movement -- groundwork for a gap-fill AND a gap-and-go strategy.

gap day    : |open/prev_close - 1| > GAP_THR
gap-fill   : intraday touches prev_close  (~70%)
gap-and-go : never touches prev_close     (~30%)

For each gap day we record features KNOWN EARLY (at open / by 09:30) so we can
ask: is a go-day separable from a fill-day in time to trade it?
  - gap size / direction
  - r15  = return open -> 09:30 close (early drive, in gap direction = +)
  - away = did 09:30 price move AWAY from prev_close vs toward it?
  - nifty_gap = NIFTY's own gap that day (same sign? bigger?)
Then compares the two groups and checks NIFTY alignment.

Run: PYTHONPATH=<repo> python3 analysis/hdfc_gapandgo.py
"""
import glob
import numpy as np
import pandas as pd

from engine.equity.gap_features import compute_gap, signed_drive

HDFC = "/Users/gokul/Desktop/buildstrategies/data_equity/HDFCBANK"
NIFTY_CSV = "/Users/gokul/Desktop/tradealgo/nifty_5min_5yr.csv"
GAP_THR = 0.0015

# ---- HDFC 1-min -> per session -----------------------------------------------
h = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(f"{HDFC}/*.parquet"))],
              ignore_index=True)
h = h.sort_values("DateTime").set_index("DateTime")
h["day"] = h.index.normalize()

# ---- NIFTY 5-min -> per-day open/close (for its own gap) ----------------------
n = pd.read_csv(NIFTY_CSV, parse_dates=["date"]).sort_values("date").set_index("date")
n["day"] = n.index.normalize()
nd = n.groupby("day").agg(nopen=("open", "first"), nclose=("close", "last"))
nd["npc"] = nd["nclose"].shift(1)
nd["ngap"] = nd["nopen"] / nd["npc"] - 1

rows = []
for day, g in h.groupby("day"):
    g = g.sort_index()
    o, c = g["Open"].iloc[0], g["Close"].iloc[-1]
    hi, lo = g["High"].max(), g["Low"].min()
    cut = day + pd.Timedelta(hours=9, minutes=30)
    early = g[g.index < cut]
    if len(early) == 0:
        continue
    p0930 = early["Close"].iloc[-1]
    rows.append({"day": day, "open": o, "close": c, "high": hi, "low": lo,
                 "p0930": p0930})
S = pd.DataFrame(rows)
S["pc"] = S["close"].shift(1)
S = S.dropna(subset=["pc"]).reset_index(drop=True)
S["gap"] = S["open"].combine(S["pc"], compute_gap)
S = S[S["gap"].abs() > GAP_THR].reset_index(drop=True)
S["dir"] = np.sign(S["gap"])                                   # +1 up, -1 down
S["filled"] = (S["low"] <= S["pc"]) & (S["pc"] <= S["high"])
S["go"] = ~S["filled"]
S["o2c"] = (S["close"] / S["open"] - 1) * S["dir"]             # + = closed in gap dir
S["r15"] = S.apply(lambda r: signed_drive(r["p0930"], r["open"], r["gap"]), axis=1)
# distance of 09:30 px from prev_close, normalized; + = moved AWAY from fill
S["away15"] = (S["p0930"] - S["pc"]) / S["pc"] * S["dir"]
S = S.merge(nd[["ngap"]], left_on="day", right_index=True, how="left")
S["nifty_same"] = np.sign(S["ngap"]) == S["dir"]

print(f"gap days: {len(S)}   fill={100*S['filled'].mean():.1f}%   "
      f"go={100*S['go'].mean():.1f}%   (2020->2026)")


def line(tag, sub):
    print(f"  {tag:<20} n={len(sub):4d}  gap={sub['gap'].abs().mean()*100:5.3f}%  "
          f"r15={sub['r15'].mean()*100:+6.3f}%  away15={sub['away15'].mean()*100:+6.3f}%  "
          f"o2c={sub['o2c'].mean()*100:+6.3f}%  niftySame={100*sub['nifty_same'].mean():4.0f}%")


print(f"\n{'='*88}\nGO vs FILL -- group means (features known by 09:30)")
print(f"  {'':<20} {'n':>6}  {'|gap|':>7}  {'r15(drive)':>11}  {'away15':>8}  {'o2c':>8}  niftySame")
line("gap-and-go", S[S["go"]])
line("gap-fill", S[S["filled"]])

# split by direction
print(f"\n{'='*88}\nBY DIRECTION")
for d, lbl in [(1, "UP gaps"), (-1, "DOWN gaps")]:
    sd = S[S["dir"] == d]
    print(f" {lbl}: n={len(sd)}  go={100*sd['go'].mean():.1f}%")
    line("  go", sd[sd["go"]])
    line("  fill", sd[sd["filled"]])

# ---- separability: does early drive / away predict a go day? ------------------
print(f"\n{'='*88}\nEARLY SEPARABILITY  (P(go) conditioned on 09:30 features)")
S["r15_bucket"] = pd.cut(S["r15"], [-1, -0.003, 0, 0.003, 0.006, 1],
                         labels=["<-0.3%", "-0.3..0", "0..0.3%", "0.3..0.6%", ">0.6%"])
for b, sub in S.groupby("r15_bucket", observed=True):
    print(f"  r15 drive {str(b):<10}  n={len(sub):4d}  P(go)={100*sub['go'].mean():4.1f}%  "
          f"o2c={sub['o2c'].mean()*100:+.3f}%")

print(f"\n  NIFTY alignment:")
for label, mask in [("NIFTY gap SAME dir", S["nifty_same"]),
                    ("NIFTY gap opp/none", ~S["nifty_same"])]:
    sub = S[mask]
    print(f"    {label:<20} n={len(sub):4d}  P(go)={100*sub['go'].mean():4.1f}%  "
          f"o2c={sub['o2c'].mean()*100:+.3f}%")

# big-gap + aligned + early-drive = strongest go setup?
strong = S[(S["gap"].abs() > 0.004) & S["nifty_same"] & (S["r15"] > 0.001)]
print(f"\n  COMBINED go-setup (|gap|>0.4% & NIFTY-aligned & early-drive>0.1%):"
      f"\n    n={len(strong)}  P(go)={100*strong['go'].mean():.1f}%  "
      f"o2c={strong['o2c'].mean()*100:+.3f}%  o2c_win={100*(strong['o2c']>0).mean():.1f}%")
