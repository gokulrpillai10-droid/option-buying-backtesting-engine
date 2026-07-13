"""Shared swing support/resistance primitives.

Used by analysis/hdfc_sr_levels.py (single-lookback intraday S/R) and
analysis/hdfc_sr_multiframe.py (multi-timeframe S/R + confluence). Keeps the
pivot/cluster/resample logic in one place so tuning changes stay in sync.
"""
import pandas as pd


def resample(df, rule):
    """OHLC resample of a 1-min DataFrame to the given pandas offset rule."""
    o = df["Open"].resample(rule).first()
    h = df["High"].resample(rule).max()
    l = df["Low"].resample(rule).min()
    c = df["Close"].resample(rule).last()
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}).dropna()


def pivots(bars, k):
    """Fractal pivot highs/lows: strict extreme within +/-k bars.
    Returns (highs, lows) as lists of (timestamp, price)."""
    highs, lows = [], []
    H, L = bars["High"].values, bars["Low"].values
    idx = bars.index
    for i in range(k, len(bars) - k):
        win_h = H[i - k:i + k + 1]
        win_l = L[i - k:i + k + 1]
        if H[i] == win_h.max() and (H[i] > win_h).sum() >= k:
            highs.append((idx[i], H[i]))
        if L[i] == win_l.min() and (L[i] < win_l).sum() >= k:
            lows.append((idx[i], L[i]))
    return highs, lows


def cluster(points, tol_frac):
    """Group (timestamp, price) pivots into zones. A point joins the current
    zone only if within tol_frac of the zone MEAN (not the previous point) ->
    bounds zone width, avoids chained mega-bands. Returns zone dicts ranked-
    ready with level/lo/hi/touches/last_touch/score (recency-weighted)."""
    if not points:
        return []
    pts = sorted(points, key=lambda p: p[1])
    zones, cur, cur_mean = [], [pts[0]], pts[0][1]
    for t, p in pts[1:]:
        if abs(p - cur_mean) / cur_mean <= tol_frac:
            cur.append((t, p))
            cur_mean = sum(x for _, x in cur) / len(cur)
        else:
            zones.append(cur)
            cur, cur_mean = [(t, p)], p
    zones.append(cur)
    last_ts = max(t for t, _ in points)
    span = (last_ts - min(t for t, _ in points)).total_seconds() or 1
    out = []
    for z in zones:
        prices = [p for _, p in z]
        recent = max(t for t, _ in z)
        recency = 1 - (last_ts - recent).total_seconds() / span
        out.append({
            "level": sum(prices) / len(prices),
            "lo": min(prices), "hi": max(prices),
            "touches": len(z),
            "last_touch": recent,
            "score": len(z) * (0.5 + 0.5 * recency),
        })
    return out
