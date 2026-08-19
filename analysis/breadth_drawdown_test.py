"""Do breadth / skew forecast forward DRAWDOWN or RETURNS -- incremental to VIX?

The vol-surface + breadth detector was real but VIX-redundant FOR VOL. Breadth is
classically a returns/internals signal, not a vol one, so this is its last honest
shot: does low breadth (or high skew) predict the next-10d drawdown / return BEYOND
what VIX already says? Tested standalone AND within middle-VIX days (VIX ambiguous),
with Welch t. Breadth runs to today (no chain needed); skew stops at the chain end.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from analysis.regime_accuracy import forward_drawdown, forward_return, welch_t
from analysis.regime_vol import expanding_percentile

from analysis.regime_data import load_closes_vix
BREADTH_CSV = "analysis/nifty_breadth_daily.csv"
FACTORS_CSV = "analysis/vol_surface_daily.csv"
W = 10
WARMUP = 252
T_GATE = 3.0


def _tercile_t(x: pd.Series, y: pd.Series, low_is_signal: bool):
    """Welch t of forward outcome for the signal tercile vs the other. If
    low_is_signal (breadth: low=stress), compare bottom vs top tercile."""
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(d) < 30:
        return np.nan, np.nan, np.nan, 0
    lo, hi = d["x"].quantile(1 / 3), d["x"].quantile(2 / 3)
    a = d.loc[d["x"] <= lo, "y"] if low_is_signal else d.loc[d["x"] >= hi, "y"]
    b = d.loc[d["x"] >= hi, "y"] if low_is_signal else d.loc[d["x"] <= lo, "y"]
    return a.mean(), b.mean(), welch_t(a, b), len(d)


def main():
    closes, vix = load_closes_vix()
    breadth = pd.read_csv(BREADTH_CSV, parse_dates=["date"], index_col="date")["breadth"]
    skew = pd.read_csv(FACTORS_CSV, parse_dates=["date"], index_col="date")["skew"]

    idx = closes.index
    dd = forward_drawdown(closes, W)
    ret = forward_return(closes, W)
    vpct = expanding_percentile(vix.reindex(idx), WARMUP)
    midvix = ((vpct > 1 / 3) & (vpct < 2 / 3)).reindex(idx).fillna(False)

    tests = [("breadth->drawdown", breadth, dd, True),
             ("breadth->return", breadth, ret, True),
             ("skew->drawdown", skew, dd, False),
             ("skew->return", skew, ret, False)]

    print(f"BREADTH / SKEW vs forward {W}d DRAWDOWN & RETURN  (incremental to VIX)")
    print("signal tercile = low breadth / high skew (the 'stress' side)\n")
    print(f"{'test':<20}{'scope':<10}{'signal':>9}{'other':>9}{'t':>7}  verdict")
    any_edge = False
    for name, x, y, low in tests:
        xi = x.reindex(idx)
        for scope, mask in [("all", pd.Series(True, index=idx)), ("mid-VIX", midvix)]:
            sig, oth, t, n = _tercile_t(xi[mask], y[mask], low)
            edge = (not np.isnan(t)) and abs(t) >= T_GATE and scope == "mid-VIX"
            any_edge = any_edge or edge
            tag = "** adds beyond VIX" if edge else (
                "significant" if (not np.isnan(t) and abs(t) >= T_GATE) else "-")
            print(f"{name:<20}{scope:<10}{sig*100:>8.2f}%{oth*100:>8.2f}%{t:>7.1f}  {tag}")

    print(f"\nVERDICT: {'GO — an orthogonal factor forecasts drawdown/return beyond VIX' if any_edge else 'NO-GO — neither breadth nor skew adds drawdown/return signal beyond VIX'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
