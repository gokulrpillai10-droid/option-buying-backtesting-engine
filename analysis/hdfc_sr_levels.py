"""Support/resistance for HDFCBANK on 5m and 15m from cached 1-min bars.
Method: fractal swing pivots (window k) -> cluster within tol% -> rank by
touch-count and recency. Reports nearest levels around the last close."""
import glob
import pandas as pd

from sr_core import (assert_no_basis_seam, cluster, drop_partial_sessions,
                     pivots, resample)

CACHE = "/Users/gokul/Desktop/buildstrategies/data_equity/HDFCBANK"

# recent context: last two cached months is plenty for intraday S/R
files = sorted(glob.glob(f"{CACHE}/*.parquet"))[-2:]
df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
df = df.sort_values("DateTime").set_index("DateTime")
assert_no_basis_seam(df)
df, _dropped = drop_partial_sessions(df)
if _dropped:
    print(f"skipped partial sessions: {_dropped}")
print("data range:", df.index.min(), "->", df.index.max(), f"({len(df)} 1-min bars)")

LAST = float(df["Close"].iloc[-1])
N_SESSIONS = 15                       # intraday S/R lookback (trading days)


def sr_for(rule, k, tol):
    bars = resample(df, rule)
    # keep last N_SESSIONS trading days
    days = sorted(set(bars.index.normalize()))[-N_SESSIONS:]
    bars = bars[bars.index.normalize().isin(days)]
    hi, lo = pivots(bars, k)
    zones = cluster(hi + lo, tol)
    # classify by the zone BAND, not just its mean: a zone straddling the last
    # close is the level price is trading inside, not support or resistance
    res = sorted([z for z in zones if z["lo"] > LAST], key=lambda z: z["level"])
    sup = sorted([z for z in zones if z["hi"] < LAST], key=lambda z: z["level"], reverse=True)
    inside = [z for z in zones if z["lo"] <= LAST <= z["hi"]]
    return bars, res, sup, inside


def zone_line(z):
    return (f"    {z['level']:8.2f}  [{z['lo']:.2f}-{z['hi']:.2f}]  "
            f"touches={z['touches']}  last={z['last_touch']:%m-%d %H:%M}  score={z['score']:.1f}")


def show(rule, k, tol):
    bars, res, sup, inside = sr_for(rule, k, tol)
    today = bars[bars.index.normalize() == bars.index[-1].normalize()]
    print(f"\n{'='*64}\n{rule} timeframe  "
          f"({len(bars)} bars, last {N_SESSIONS} sessions, pivot k={k}, tol={tol*100:.2f}%)")
    print(f"last close: {LAST:.2f}   session high/low: "
          f"{today['High'].max():.2f} / {today['Low'].min():.2f}")
    print("\n  RESISTANCE (band above close):")
    for z in res[:5]:
        print(zone_line(z))
    for z in inside:
        print(f"  PRICE INSIDE ZONE:\n{zone_line(z)}")
    print("  SUPPORT (band below close):")
    for z in sup[:5]:
        print(zone_line(z))


show("5min", k=4, tol=0.0015)
show("15min", k=3, tol=0.0020)
