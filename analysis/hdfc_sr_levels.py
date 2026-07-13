"""Support/resistance for HDFCBANK on 5m and 15m from cached 1-min bars.
Method: fractal swing pivots (window k) -> cluster within tol% -> rank by
touch-count and recency. Reports nearest levels around the last close."""
import glob
import pandas as pd

from sr_core import cluster, pivots, resample

CACHE = "/Users/gokul/Desktop/buildstrategies/data_equity/HDFCBANK"

# recent context: last two cached months is plenty for intraday S/R
files = sorted(glob.glob(f"{CACHE}/*.parquet"))[-2:]
df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
df = df.sort_values("DateTime").set_index("DateTime")
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
    res = sorted([z for z in zones if z["level"] > LAST], key=lambda z: z["level"])
    sup = sorted([z for z in zones if z["level"] < LAST], key=lambda z: z["level"], reverse=True)
    return bars, res, sup


def show(rule, k, tol):
    bars, res, sup = sr_for(rule, k, tol)
    print(f"\n{'='*64}\n{rule} timeframe  "
          f"({len(bars)} bars, last {N_SESSIONS} sessions, pivot k={k}, tol={tol*100:.2f}%)")
    print(f"last close: {LAST:.2f}   session high/low: "
          f"{bars['High'].iloc[-375:].max():.2f} / {bars['Low'].iloc[-375:].min():.2f}")
    print("\n  RESISTANCE (above):")
    for z in res[:5]:
        print(f"    {z['level']:8.2f}  [{z['lo']:.2f}-{z['hi']:.2f}]  "
              f"touches={z['touches']}  last={z['last_touch']:%m-%d %H:%M}  score={z['score']:.1f}")
    print("  RESISTANCE ^ / SUPPORT v   last close = %.2f" % LAST)
    print("  SUPPORT (below):")
    for z in sup[:5]:
        print(f"    {z['level']:8.2f}  [{z['lo']:.2f}-{z['hi']:.2f}]  "
              f"touches={z['touches']}  last={z['last_touch']:%m-%d %H:%M}  score={z['score']:.1f}")


show("5min", k=4, tol=0.0015)
show("15min", k=3, tol=0.0020)
