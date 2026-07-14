"""Shared swing support/resistance primitives.

Used by analysis/hdfc_sr_levels.py (single-lookback intraday S/R) and
analysis/hdfc_sr_multiframe.py (multi-timeframe S/R + confluence). Keeps the
pivot/cluster/resample logic in one place so tuning changes stay in sync.
"""
import pandas as pd


SESSION_OPEN_OFFSET = "9h15min"       # NSE cash open; anchors intraday bins
FULL_SESSION_1MIN_BARS = 375          # 09:15-15:30
MIN_BARS_PER_DAY = 300                # same gate as engine EquityKnobs


def resample(df, rule):
    """OHLC resample of a 1-min DataFrame to the given pandas offset rule.
    Bins are anchored to the 09:15 session open (matters for rules that don't
    divide 555min, e.g. 60min: default clock anchoring would produce a 45-min
    stub bar labelled 09:00 that no real chart shows)."""
    o = df["Open"].resample(rule, offset=SESSION_OPEN_OFFSET).first()
    h = df["High"].resample(rule, offset=SESSION_OPEN_OFFSET).max()
    l = df["Low"].resample(rule, offset=SESSION_OPEN_OFFSET).min()
    c = df["Close"].resample(rule, offset=SESSION_OPEN_OFFSET).last()
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}).dropna()


def drop_partial_sessions(df, min_bars=MIN_BARS_PER_DAY):
    """Drop sessions with fewer than min_bars 1-min bars (Muhurat, halts,
    mid-session lookback-cutoff remnants) so they can't form degenerate bars
    or phantom pivots. Returns (df, dropped_days)."""
    counts = df.groupby(df.index.normalize()).size()
    full = counts[counts >= min_bars].index
    dropped = sorted(d.date() for d in counts.index.difference(full))
    return df[df.index.normalize().isin(full)], dropped


def assert_no_basis_seam(df, max_gap=0.15):
    """Fail loudly on an overnight close->open step beyond max_gap: Kite
    history is refetched month-by-month and complete months are never
    refetched, so a split/bonus leaves old months on the old price basis.
    Stats/levels computed across such a seam are garbage."""
    day = df.index.normalize()
    opens = df["Open"].groupby(day).first()
    closes = df["Close"].groupby(day).last()
    gap = opens / closes.shift(1) - 1
    bad = gap[gap.abs() > max_gap]
    if len(bad):
        days = ", ".join(f"{d:%Y-%m-%d} ({g:+.0%})" for d, g in bad.items())
        raise SystemExit(f"basis seam / unadjusted corporate action in cache: "
                         f"{days} — refetch the affected months before trusting "
                         f"any output")


def pivots(bars, k):
    """Fractal pivot highs/lows: bar i is a pivot when it is the extreme of
    the +/-k window AND strictly beyond all k bars to its LEFT — so a run of
    tick-equal extremes within k bars counts once (first bar), instead of one
    pivot per tied bar inflating touch counts. Equal extremes more than k bars
    apart are genuine retests and still count separately.
    Returns (highs, lows) as lists of (timestamp, price)."""
    highs, lows = [], []
    H, L = bars["High"].values, bars["Low"].values
    idx = bars.index
    for i in range(k, len(bars) - k):
        if H[i] == H[i - k:i + k + 1].max() and H[i] > H[i - k:i].max():
            highs.append((idx[i], H[i]))
        if L[i] == L[i - k:i + k + 1].min() and L[i] < L[i - k:i].min():
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
