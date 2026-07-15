"""Opening 5-min candle (09:15-09:20) anatomy + the price action that follows.

Two parts, per session over the lookback:
  1) candle stats: size vs day range, color counts, day-extreme-set-in-5min,
     direction/body-size predictiveness, first range-break edge, gap x color
  2) path profile by class (color x body-size tercile): mean cumulative path
     from the candle close at intraday checkpoints, MFE/MAE, which extreme
     comes first, time-below, break/failed-break rates, and whether the
     broken candle levels flip (retest rejection rates)

Headline finding on ADANIENT (1yr to 2026-07-14): conviction beats color —
big-body opens (either color) resolve up; small-body opens bleed to the
close with a repeatable trap (early candle-high break fails ~75%, then
staircase down). Candle levels are chop zones, not flip lines (43%/36%).
STABILITY UNCHECKED across regimes — rerun on the full cache before trusting.

Run: PYTHONPATH=<repo> python3 analysis/opening_candle_study.py [SYMBOL] [DAYS]
"""
import sys

import numpy as np
import pandas as pd

from engine.equity.kite_loader import load_cached

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "ADANIENT"
LOOKBACK_DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 365

df = load_cached(SYMBOL).set_index("DateTime")
df = df[df.index >= df.index.max() - pd.Timedelta(days=LOOKBACK_DAYS)]

sessions = []
for day, g in df.groupby(df.index.normalize()):
    if len(g) < 300:
        continue
    g = g.sort_index()
    t20 = day + pd.Timedelta(hours=9, minutes=20)
    c5, rest = g[g.index < t20], g[g.index >= t20]
    if len(c5) == 0 or len(rest) < 100:
        continue
    sessions.append((day, c5["Open"].iloc[0], c5["High"].max(),
                     c5["Low"].min(), c5["Close"].iloc[-1], g, rest))

S = pd.DataFrame([{"day": d, "o": o, "h": h, "l": l, "c": c,
                   "dh": g["High"].max(), "dl": g["Low"].min(),
                   "dc": g["Close"].iloc[-1],
                   "hod_in5": g["High"].idxmax() < d + pd.Timedelta(hours=9, minutes=20),
                   "lod_in5": g["Low"].idxmin() < d + pd.Timedelta(hours=9, minutes=20)}
                  for d, o, h, l, c, g, _ in sessions])
S["pc"] = S["dc"].shift(1)
S = S.dropna(subset=["pc"])
S["gap"] = S["o"] / S["pc"] - 1
S["rng"] = (S["h"] - S["l"]) / S["o"] * 100
S["day_rng"] = (S["dh"] - S["dl"]) / S["o"] * 100
S["rest_ret"] = S["dc"] / S["c"] - 1
S["body"] = (S["c"] - S["o"]) / S["o"]
S["green"] = S["body"] > 0

print(f"{SYMBOL} {len(S)} sessions ({S['day'].iloc[0]:%Y-%m-%d} -> {S['day'].iloc[-1]:%Y-%m-%d})")
print(f"candles: green {S['green'].sum()} ({100*S['green'].mean():.0f}%)  "
      f"red {(~S['green']).sum()} ({100*(~S['green']).mean():.0f}%)")
print(f"size: mean rng {S['rng'].mean():.2f}% = {100*(S['rng']/S['day_rng']).mean():.0f}% of day range")
print(f"day extreme set in first 5m: HoD {100*S['hod_in5'].mean():.1f}%  "
      f"LoD {100*S['lod_in5'].mean():.1f}%  either {100*(S['hod_in5']|S['lod_in5']).mean():.1f}%")
for lbl, seg in [("green", S[S["green"]]), ("red", S[~S["green"]])]:
    r = seg["rest_ret"] * 100
    print(f"{lbl} candle (n={len(seg)}): rest-of-day mean={r.mean():+.3f}%  win={100*(r>0).mean():.0f}%")

# ---- class = color x body-size tercile (within color) --------------------------
cls = {}
for color in (True, False):
    sub = S[S["green"] == color]
    q1, q2 = sub["body"].abs().quantile(.33), sub["body"].abs().quantile(.67)
    for _, r in sub.iterrows():
        sz = "small" if abs(r["body"]) <= q1 else ("big" if abs(r["body"]) > q2 else "mid")
        cls[r["day"]] = ("green" if color else "red") + "-" + sz

MARKS = ["09:45", "10:15", "11:00", "12:00", "13:30", "15:00"]
rows = []
for day, o, h, l, c, g, rest in sessions:
    if day not in cls:
        continue
    p = rest["Close"] / c - 1
    st = {"cls": cls[day], "close": p.iloc[-1],
          "mfe": rest["High"].max() / c - 1, "mae": rest["Low"].min() / c - 1,
          "up_first": rest["High"].idxmax() < rest["Low"].idxmin(),
          "pct_below": (rest["Close"] < c).mean(),
          "lo_break": (rest["Low"] < l).any(), "hi_break": (rest["High"] > h).any()}
    st["lo_break_fail"] = st["lo_break"] and rest["Close"].iloc[-1] > l
    st["hi_break_fail"] = st["hi_break"] and rest["Close"].iloc[-1] < h
    for mk in MARKS:
        upto = p[p.index.time <= pd.Timestamp(f"2000-01-01 {mk}").time()]
        st[mk] = upto.iloc[-1] if len(upto) else np.nan
    rows.append(st)
P = pd.DataFrame(rows)

print(f"\npath AFTER the candle (% from candle close):")
print("class       n  " + "  ".join(f"{m:>6}" for m in MARKS)
      + "   close    MFE    MAE  up1st  t<c  loBrk/fail  hiBrk/fail")
for cl in ["red-big", "red-mid", "red-small", "green-big", "green-mid", "green-small"]:
    seg = P[P["cls"] == cl]
    if len(seg) == 0:
        continue
    line = f"{cl:<10} {len(seg):3d}"
    for m in MARKS + ["close"]:
        line += f" {seg[m].mean()*100:+5.2f}%"
    lb, hb = max(seg["lo_break"].sum(), 1), max(seg["hi_break"].sum(), 1)
    line += (f" {seg['mfe'].median()*100:+.2f} {seg['mae'].median()*100:+.2f}"
             f"  {100*seg['up_first'].mean():3.0f}% {100*seg['pct_below'].mean():3.0f}%"
             f"  {100*seg['lo_break'].mean():3.0f}%/{100*seg['lo_break_fail'].sum()/lb:3.0f}%"
             f"  {100*seg['hi_break'].mean():3.0f}%/{100*seg['hi_break_fail'].sum()/hb:3.0f}%")
    print(line)

# ---- do broken candle levels flip? ---------------------------------------------
lo_r = lo_t = hi_s = hi_t = 0
for day, o, h, l, c, g, rest in sessions:
    br = rest[rest["Low"] < l]
    if len(br):
        after = rest[rest.index > br.index[0]]
        back = after[after["High"] >= l]
        if len(back):
            lo_t += 1
            w = after[(after.index >= back.index[0])
                      & (after.index <= back.index[0] + pd.Timedelta(minutes=30))]
            if len(w) and w["Close"].iloc[-1] < l:
                lo_r += 1
    br = rest[rest["High"] > h]
    if len(br):
        after = rest[rest.index > br.index[0]]
        back = after[after["Low"] <= h]
        if len(back):
            hi_t += 1
            w = after[(after.index >= back.index[0])
                      & (after.index <= back.index[0] + pd.Timedelta(minutes=30))]
            if len(w) and w["Close"].iloc[-1] > h:
                hi_s += 1
print(f"\nlevel flips on retest: broken low held as resistance "
      f"{100*lo_r/max(lo_t,1):.0f}% (n={lo_t});  broken high held as support "
      f"{100*hi_s/max(hi_t,1):.0f}% (n={hi_t})")
