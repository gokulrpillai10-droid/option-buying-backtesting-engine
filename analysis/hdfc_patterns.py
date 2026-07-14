"""Pattern-mining on HDFCBANK 1-min bars (last ~1yr) to surface tradeable edges.

Studies, each reported with sample size + win rate so nothing is read into a
handful of days:
  A) day-of-week      -- close-to-close return by weekday
  B) gap behavior     -- gap-up/down continuation vs fade, and gap-fill rate
  C) opening-range    -- 15m ORB: does the first break of the OR carry to close?
  D) HoD/LoD timing   -- when the day's high/low tends to form (30m buckets)
  E) prior-day levels -- PDH/PDL break-and-close follow-through

Regime caveat: the window spans a ~27% decline then a recovery, so directional
stats (A, B) are split H1/H2 to check they aren't just the trend.

Run: PYTHONPATH=<repo> python3 analysis/hdfc_patterns.py
"""
import glob
import numpy as np
import pandas as pd

from sr_core import assert_no_basis_seam

CACHE = "/Users/gokul/Desktop/buildstrategies/data_equity/HDFCBANK"
LOOKBACK_DAYS = 365
MIN_BARS = 300                        # full-session gate, same as the engine

files = sorted(glob.glob(f"{CACHE}/*.parquet"))[-14:]
df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
df = df.sort_values("DateTime").set_index("DateTime")
df = df[df.index >= df.index.max() - pd.Timedelta(days=LOOKBACK_DAYS)]
assert_no_basis_seam(df)
df["day"] = df.index.normalize()

# ---- build a per-session table ------------------------------------------------
# EVERY session goes in the table (partial ones too) so shift(1) prev_* always
# reflects the truly previous session's levels; partial sessions are then
# excluded from the study universe only.
rows = []
for day, g in df.groupby("day"):
    g = g.sort_index()
    o, c = g["Open"].iloc[0], g["Close"].iloc[-1]
    hi, lo = g["High"].max(), g["Low"].min()
    or15 = g[g.index < day + pd.Timedelta(hours=9, minutes=30)]
    orh = or15["High"].max() if len(or15) else np.nan
    orl = or15["Low"].min() if len(or15) else np.nan
    hod_t = g["High"].idxmax()
    lod_t = g["Low"].idxmin()
    rows.append({
        "day": day, "weekday": day.day_name(), "open": o, "close": c,
        "high": hi, "low": lo, "orh": orh, "orl": orl,
        "hod_min": hod_t.hour * 60 + hod_t.minute,
        "lod_min": lod_t.hour * 60 + lod_t.minute,
        "n_bars": len(g),
        "bars": g,
    })
S = pd.DataFrame(rows).reset_index(drop=True)
S["prev_close"] = S["close"].shift(1)
S["prev_high"] = S["high"].shift(1)
S["prev_low"] = S["low"].shift(1)
n_partial = int((S["n_bars"] < MIN_BARS).sum())
if n_partial:
    print(f"excluded {n_partial} partial session(s) (<{MIN_BARS} bars): "
          f"{[f'{d:%Y-%m-%d}' for d in S.loc[S['n_bars'] < MIN_BARS, 'day']]}")
S = S[S["n_bars"] >= MIN_BARS].dropna(subset=["prev_close", "orh"]).reset_index(drop=True)
S["gap"] = S["open"] / S["prev_close"] - 1
S["o2c"] = S["close"] / S["open"] - 1
S["c2c"] = S["close"] / S["prev_close"] - 1
half = len(S) // 2
print(f"sessions: {len(S)}  ({S['day'].iloc[0]:%Y-%m-%d} -> {S['day'].iloc[-1]:%Y-%m-%d})")


def wr(x):
    x = np.asarray(x, float)
    return 100 * (x > 0).mean() if len(x) else float("nan")


# ---- A) day-of-week -----------------------------------------------------------
print(f"\n{'='*66}\nA) DAY-OF-WEEK  (close-to-close %)")
order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
for wd in order:
    r = S.loc[S["weekday"] == wd, "c2c"]
    print(f"  {wd:<9} n={len(r):3d}  mean={r.mean()*100:+.3f}%  win={wr(r):4.1f}%")

# ---- B) gap behavior ----------------------------------------------------------
print(f"\n{'='*66}\nB) GAP BEHAVIOR  (open-to-close by gap sign; fill = touches prev_close)")
for name, mask in [("gap-up  >0.15%", S["gap"] > 0.0015),
                   ("gap-dn  <-0.15%", S["gap"] < -0.0015),
                   ("flat  |gap|<0.15%", S["gap"].abs() <= 0.0015)]:
    sub = S[mask]
    if len(sub) == 0:
        continue
    fills = []
    for _, row in sub.iterrows():
        pc = row["prev_close"]
        fills.append(row["low"] <= pc <= row["high"])
    o2c = sub["o2c"]
    print(f"  {name:<18} n={len(sub):3d}  o2c_mean={o2c.mean()*100:+.3f}%  "
          f"o2c_win={wr(o2c):4.1f}%  gap_filled={100*np.mean(fills):4.1f}%")

# continuation vs fade split by half (regime robustness)
for lbl, seg in [("H1", S.iloc[:half]), ("H2", S.iloc[half:])]:
    up = seg.loc[seg["gap"] > 0.0015, "o2c"]
    dn = seg.loc[seg["gap"] < -0.0015, "o2c"]
    print(f"    [{lbl}] gap-up o2c_win={wr(up):4.1f}% (n={len(up)})   "
          f"gap-dn o2c_win={wr(dn):4.1f}% (n={len(dn)})")

# ---- C) opening-range breakout (15m) ------------------------------------------
print(f"\n{'='*66}\nC) OPENING-RANGE BREAKOUT  (first 15m OR; first close beyond it)")
res = {"long": [], "short": [], "none": 0}
for _, row in S.iterrows():
    g = row["bars"]
    post = g[g.index >= row["day"] + pd.Timedelta(hours=9, minutes=30)]
    trig = None
    for ts, bar in post.iterrows():
        if bar["Close"] > row["orh"]:
            trig = ("long", row["orh"], bar["Close"]); break
        if bar["Close"] < row["orl"]:
            trig = ("short", row["orl"], bar["Close"]); break
    if trig is None:
        res["none"] += 1
        continue
    side, lvl, entry = trig
    if side == "long":
        res["long"].append(row["close"] / entry - 1)      # entry->close
    else:
        res["short"].append(entry / row["close"] - 1)      # short: profit if close<entry
for side in ("long", "short"):
    r = np.array(res[side], float)
    print(f"  {side:<5} breaks n={len(r):3d}  edge(entry->close)={r.mean()*100:+.3f}%  "
          f"win={wr(r):4.1f}%")
print(f"  no-break days n={res['none']}")

# ---- D) HoD / LoD timing ------------------------------------------------------
# equal-width buckets: unequal widths make the row percentages incomparable
# (a 2h bucket shows ~4x a 30m bucket under a uniform distribution)
print(f"\n{'='*66}\nD) HIGH-OF-DAY / LOW-OF-DAY FORMATION TIME  (30-min buckets)")
SESSION_OPEN_MIN, SESSION_END_MIN = 9 * 60 + 15, 15 * 60 + 30    # 09:15, 15:30
for lo in range(SESSION_OPEN_MIN, SESSION_END_MIN, 30):
    hi = min(lo + 30, SESSION_END_MIN)
    lbl = f"{lo//60:02d}:{lo%60:02d}-{hi//60:02d}:{hi%60:02d}"
    h = ((S["hod_min"] >= lo) & (S["hod_min"] < hi)).mean() * 100
    l = ((S["lod_min"] >= lo) & (S["lod_min"] < hi)).mean() * 100
    print(f"  {lbl}   HoD={h:4.1f}%   LoD={l:4.1f}%")

# ---- E) prior-day high/low breakout -------------------------------------------
print(f"\n{'='*66}\nE) PRIOR-DAY LEVEL BREAKS  (did price tag PDH/PDL, then close beyond?)")
tag_h = S["high"] >= S["prev_high"]
close_above = S.loc[tag_h, "close"] > S.loc[tag_h, "prev_high"]
tag_l = S["low"] <= S["prev_low"]
close_below = S.loc[tag_l, "close"] < S.loc[tag_l, "prev_low"]
print(f"  tagged PDH: n={tag_h.sum():3d}  closed ABOVE PDH={100*close_above.mean():4.1f}%")
print(f"  tagged PDL: n={tag_l.sum():3d}  closed BELOW PDL={100*close_below.mean():4.1f}%")
# inside days (neither tagged) -> next-day expansion?
inside = (~tag_h) & (~tag_l)
print(f"  inside days (no PDH/PDL tag): n={inside.sum()}  "
      f"({100*inside.mean():.1f}% of sessions)")
