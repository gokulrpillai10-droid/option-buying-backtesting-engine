"""Gap continuation / reversal / breakout behavior across the full 1-min cache.

Motivated by the ORB-short NO-GO (edge existed only in the 2024-25 decline
regime): every signal here is reported PER YEAR, and the summary counts how
many years each signal's mean is positive — regime-stability is the metric,
not the full-sample average.

Signals:
  A) gap continuation/fade by gap-size bucket (o2c from the open)
  B) gap fill rate (touch of prev close) by bucket
  C) 09:30 checkpoint reversal: gap-up that lost its open by 09:30 -> short
     from the 09:30 price; gap-down that reclaimed its open -> long (and the
     confirm-continuation variants)
  D) breakouts: 15m ORB long/short (first 5m close beyond OR), PDH/PDL tag ->
     close-beyond follow-through

Run: PYTHONPATH=<repo> python3 analysis/gap_breakout_study.py [SYMBOL]
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from engine.equity.kite_loader import load_cached

sys.path.insert(0, str(Path(__file__).parent))
from sr_core import assert_no_basis_seam  # noqa: E402

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "ADANIENT"
MIN_BARS = 300

df = load_cached(SYMBOL).set_index("DateTime")
assert_no_basis_seam(df)
df["day"] = df.index.normalize()

rows = []
for day, g in df.groupby("day"):
    if len(g) < MIN_BARS:
        continue
    g = g.sort_index()
    or15 = g[g.index < day + pd.Timedelta(hours=9, minutes=30)]
    post = g[g.index >= day + pd.Timedelta(hours=9, minutes=30)]
    if len(or15) == 0 or len(post) == 0:
        continue
    rows.append({
        "day": day, "year": day.year,
        "open": g["Open"].iloc[0], "close": g["Close"].iloc[-1],
        "high": g["High"].max(), "low": g["Low"].min(),
        "orh": or15["High"].max(), "orl": or15["Low"].min(),
        "c0930": or15["Close"].iloc[-1],          # price at the 09:30 checkpoint
        "post": post[["Open", "High", "Low", "Close"]],
    })
S = pd.DataFrame(rows)
S["pc"] = S["close"].shift(1)
S["ph"] = S["high"].shift(1)
S["pl"] = S["low"].shift(1)
S = S.dropna(subset=["pc"]).reset_index(drop=True)
S["gap"] = S["open"] / S["pc"] - 1
S["o2c"] = S["close"] / S["open"] - 1
S["filled"] = (S["low"] <= S["pc"]) & (S["pc"] <= S["high"])
years = sorted(S["year"].unique())
print(f"{SYMBOL}: {len(S)} sessions {S['day'].iloc[0]:%Y-%m-%d} -> "
      f"{S['day'].iloc[-1]:%Y-%m-%d}\n")


def line(name, r):
    r = np.asarray(r, float) * 100
    return (f"  {name:<34} n={len(r):4d}  mean={r.mean():+.3f}%  "
            f"win={100 * (r > 0).mean():4.1f}%")


def per_year(name, sel, ret):
    """Yearly sign map + stability count for a signal. ret = per-day return
    of TAKING the signal (already signed)."""
    marks, pos = [], 0
    for y in years:
        r = ret[sel & (S["year"] == y)]
        if len(r) < 5:
            marks.append(f"{y}:  · ")
            continue
        m = r.mean() * 100
        pos += m > 0
        marks.append(f"{y}:{m:+.2f}")
    print(f"  {name:<34} [{' | '.join(marks)}]  +yrs={pos}")


# ---- A/B) gap continuation + fill by bucket ------------------------------------
print("=" * 78)
print("A) GAP CONTINUATION vs FADE by size (o2c from open) + B) fill rate")
buckets = [("up 0.15-0.5%", (S["gap"] > 0.0015) & (S["gap"] <= 0.005)),
           ("up 0.5-1.5%", (S["gap"] > 0.005) & (S["gap"] <= 0.015)),
           ("up >1.5%", S["gap"] > 0.015),
           ("dn 0.15-0.5%", (S["gap"] < -0.0015) & (S["gap"] >= -0.005)),
           ("dn 0.5-1.5%", (S["gap"] < -0.005) & (S["gap"] >= -0.015)),
           ("dn >1.5%", S["gap"] < -0.015)]
for name, m in buckets:
    sub = S[m]
    if len(sub) == 0:
        continue
    print(line(f"gap {name}", sub["o2c"]) + f"  filled={100 * sub['filled'].mean():4.1f}%")
print("\n  continuation = go WITH the gap from the open, per year (mean %):")
per_year("with-gap up>0.5%", S["gap"] > 0.005, S["o2c"])
per_year("with-gap dn>0.5% (short)", S["gap"] < -0.005, -S["o2c"])
per_year("fade gap-up>0.5% (short)", S["gap"] > 0.005, -S["o2c"])
per_year("fade gap-dn>0.5% (long)", S["gap"] < -0.005, S["o2c"])

# ---- C) 09:30 checkpoint: reversal vs confirmation ------------------------------
print("\n" + "=" * 78)
print("C) 09:30 CHECKPOINT (entry at 09:30 price, exit at close)")
S["r0930"] = S["close"] / S["c0930"] - 1          # long return from 09:30
gu, gd = S["gap"] > 0.005, S["gap"] < -0.005
lost_open = S["c0930"] < S["open"]
recl_open = S["c0930"] > S["open"]
cases = [("gap-up LOST open -> short", gu & lost_open, -S["r0930"]),
         ("gap-up HELD open -> long", gu & recl_open, S["r0930"]),
         ("gap-dn RECLAIMED open -> long", gd & recl_open, S["r0930"]),
         ("gap-dn STILL below open -> short", gd & lost_open, -S["r0930"])]
for name, sel, ret in cases:
    print(line(name, ret[sel]))
for name, sel, ret in cases:
    per_year(name, sel, ret)

# ---- D) breakouts ---------------------------------------------------------------
print("\n" + "=" * 78)
print("D) BREAKOUTS")
orb = {"long": pd.Series(np.nan, index=S.index),
       "short": pd.Series(np.nan, index=S.index)}
for i, row in S.iterrows():
    p5 = row["post"].resample("5min").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    for _, b in p5.iterrows():
        if b["Close"] > row["orh"]:
            orb["long"].iloc[i] = row["close"] / b["Close"] - 1
            break
        if b["Close"] < row["orl"]:
            orb["short"].iloc[i] = b["Close"] / row["close"] - 1
            break
print(line("ORB long (close>ORH -> close)", orb["long"].dropna()))
print(line("ORB short (close<ORL -> close)", orb["short"].dropna()))
per_year("ORB long", orb["long"].notna(), orb["long"].fillna(0))
per_year("ORB short", orb["short"].notna(), orb["short"].fillna(0))

tag_h, tag_l = S["high"] >= S["ph"], S["low"] <= S["pl"]
print(f"\n  PDH tagged n={tag_h.sum()}: closed above "
      f"{100 * (S.loc[tag_h, 'close'] > S.loc[tag_h, 'ph']).mean():.1f}%   "
      f"PDL tagged n={tag_l.sum()}: closed below "
      f"{100 * (S.loc[tag_l, 'close'] < S.loc[tag_l, 'pl']).mean():.1f}%")
for y in years:
    yy = S["year"] == y
    th, tl = tag_h & yy, tag_l & yy
    if th.sum() < 5 or tl.sum() < 5:
        continue
    print(f"    {y}: PDH-hold {100 * (S.loc[th, 'close'] > S.loc[th, 'ph']).mean():4.1f}%"
          f" (n={th.sum():3d})   PDL-hold "
          f"{100 * (S.loc[tl, 'close'] < S.loc[tl, 'pl']).mean():4.1f}% (n={tl.sum():3d})")
