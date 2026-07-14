"""Multi-timeframe support/resistance for HDFCBANK over a ~1-year lookback.

Timeframes: 5m, 15m, 1h, daily. Per timeframe: fractal swing pivots (window k)
-> mean-anchored clustering within tol% -> rank zones by touch-count weighted by
recency. Then cross-timeframe confluence: zones whose price bands overlap across
2+ timeframes (the levels that matter most).

Run: PYTHONPATH=<repo> python3 analysis/hdfc_sr_multiframe.py
"""
import glob
import pandas as pd

from sr_core import (assert_no_basis_seam, cluster, drop_partial_sessions,
                     pivots, resample)

CACHE = "/Users/gokul/Desktop/buildstrategies/data_equity/HDFCBANK"
LOOKBACK_DAYS = 365

# load enough months to cover the lookback (14 months of files, then trim by date)
files = sorted(glob.glob(f"{CACHE}/*.parquet"))[-14:]
df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
df = df.sort_values("DateTime").set_index("DateTime")
cutoff = df.index.max() - pd.Timedelta(days=LOOKBACK_DAYS)
df = df[df.index >= cutoff]
assert_no_basis_seam(df)
# drop Muhurat/halted sessions and the partial day the mid-session cutoff
# leaves behind — a 1-bar "day" otherwise becomes a degenerate 1D pivot
df, _dropped = drop_partial_sessions(df)
if _dropped:
    print(f"skipped partial sessions: {_dropped}")

LAST = float(df["Close"].iloc[-1])
print(f"data range: {df.index.min()} -> {df.index.max()} ({len(df)} 1-min bars)")
print(f"lookback: {LOOKBACK_DAYS}d   last close: {LAST:.2f}   "
      f"year range: {df['Low'].min():.2f} - {df['High'].max():.2f}")

# per-timeframe: resample rule, pivot window k, cluster tol (fraction of price)
FRAMES = [
    ("5min",  "5m",  6, 0.0030),
    ("15min", "15m", 5, 0.0040),
    ("60min", "1h",  4, 0.0055),
    ("1D",    "1D",  3, 0.0070),
]
TF_ORDER = [label for _, label, _, _ in FRAMES]   # single source for tf sorting


def side_of(z):
    """RES / SUP / AT by the zone BAND vs last close — a band straddling the
    close is neither side; it's the level price is trading inside."""
    if z["lo"] > LAST:
        return "RES"
    if z["hi"] < LAST:
        return "SUP"
    return "AT "


def sr_for(rule, k, tol):
    bars = resample(df, rule)
    hi, lo = pivots(bars, k)
    zones = cluster(hi + lo, tol)
    return bars, zones


all_zones = []          # tagged zones for confluence

for rule, label, k, tol in FRAMES:
    bars, zones = sr_for(rule, k, tol)
    for z in zones:
        all_zones.append({**z, "tf": label})
    res = sorted([z for z in zones if z["lo"] > LAST], key=lambda z: -z["score"])
    sup = sorted([z for z in zones if z["hi"] < LAST], key=lambda z: -z["score"])
    inside = [z for z in zones if z["lo"] <= LAST <= z["hi"]]

    def zline(z):
        return (f"    {z['level']:8.2f}  [{z['lo']:.2f}-{z['hi']:.2f}]  "
                f"touches={z['touches']:2d}  last={z['last_touch']:%Y-%m-%d}  "
                f"score={z['score']:.1f}")

    print(f"\n{'='*70}\n{label} timeframe  "
          f"({len(bars)} bars, pivot k={k}, tol={tol*100:.2f}%)")
    print("  RESISTANCE (band above close, by strength):")
    for z in res[:6]:
        print(zline(z))
    print(f"  --- last close {LAST:.2f} ---")
    for z in inside:
        print(f"  PRICE INSIDE ZONE:\n{zline(z)}")
    print("  SUPPORT (band below close, by strength):")
    for z in sup[:6]:
        print(zline(z))


def confluence(zones):
    """Group zones from different timeframes that sit at the same price.
    Band-aware: a zone joins the current group while its CENTER lies inside
    the group's running band — a fixed center-distance test would split
    genuinely overlapping bands (per-TF cluster tols differ: 0.30% on 5m vs
    0.70% on 1D) and drop the level from the report, while raw band-overlap
    chaining merges everything into mega-bands. Report groups spanning 2+
    distinct timeframes."""
    if not zones:
        return []
    items = sorted(zones, key=lambda z: z["level"])
    groups, cur, cur_hi = [], [items[0]], items[0]["hi"]
    for z in items[1:]:
        if z["level"] <= cur_hi:
            cur.append(z)
            cur_hi = max(cur_hi, z["hi"])
        else:
            groups.append(cur)
            cur, cur_hi = [z], z["hi"]
    groups.append(cur)
    out = []
    for g in groups:
        tfs = sorted({z["tf"] for z in g}, key=TF_ORDER.index)
        if len(tfs) >= 2:
            tw = sum(z["touches"] for z in g)
            out.append({
                "level": sum(z["level"] * z["touches"] for z in g) / tw,
                "lo": min(z["lo"] for z in g),
                "hi": max(z["hi"] for z in g),
                "tfs": tfs,
                "n_tf": len(tfs),
                "touches": tw,
            })
    return out


conf = confluence(all_zones)
conf.sort(key=lambda z: (-z["n_tf"], -z["touches"]))

print(f"\n{'#'*70}\nCONFLUENCE ZONES (present in 2+ timeframes), strongest first")
print(f"{'#'*70}")
for z in conf:
    dist = (z["level"] - LAST) / LAST * 100
    print(f"  {side_of(z)} {z['level']:8.2f}  [{z['lo']:.2f}-{z['hi']:.2f}]  "
          f"tf={'+'.join(z['tfs']):<14}  touches={z['touches']:3d}  "
          f"({dist:+.1f}% from close)")

print(f"\nConfluence zones sorted by price (context around close {LAST:.2f}):")
for z in sorted(conf, key=lambda z: -z["level"]):
    print(f"  {side_of(z)} {z['level']:8.2f}  [{z['lo']:.2f}-{z['hi']:.2f}]  "
          f"{'+'.join(z['tfs'])}")
