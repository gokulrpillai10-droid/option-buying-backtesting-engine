"""Pure vol-surface factors for the second regime detector (options-structure lens).

Where the price detector reads price + VIX level, this reads the SHAPE of the vol
surface: how rich vol is (VRP), how skewed (put/call), and — assembled by the daily
builder — how the term structure and vol-of-vol are moving. Snapshot-in / scalar-out,
on the duck-typed chain snapshot (``strikes()`` + ``call(k).iv/.delta`` /
``put(k).iv/.delta``), so identical functions run on Data-v3 and a live Kite chain.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd


def _iv_at(snapshot, k) -> float:
    iv = snapshot.call(k).iv                 # IV is shared per strike (Data-v3 IV col)
    return float(iv) if iv is not None else math.nan


def _delta(snapshot, k, side: str) -> Optional[float]:
    d = (snapshot.call(k) if side == "call" else snapshot.put(k)).delta
    return None if d is None else float(d)


def atm_iv(snapshot, forward: float) -> float:
    """IV at the strike nearest the forward (the ~at-the-money implied vol)."""
    ks = snapshot.strikes()
    if not ks:
        return math.nan
    k = min(ks, key=lambda s: abs(s - forward))
    return _iv_at(snapshot, k)


def strike_nearest_delta(snapshot, target: float, side: str) -> Optional[int]:
    """Strike whose ``side`` delta is closest to ``target`` (e.g. 0.25 call, -0.25
    put) -- the wing used for a risk-reversal skew."""
    cand = [(k, _delta(snapshot, k, side)) for k in snapshot.strikes()]
    cand = [(k, d) for k, d in cand if d is not None]
    if not cand:
        return None
    return min(cand, key=lambda kd: abs(kd[1] - target))[0]


def skew(snapshot, delta: float = 0.25) -> float:
    """Risk-reversal skew = IV(``delta`` put wing) - IV(``delta`` call wing).
    Positive = puts bid over calls = downside fear / hedging demand."""
    pk = strike_nearest_delta(snapshot, -delta, "put")
    ck = strike_nearest_delta(snapshot, delta, "call")
    if pk is None or ck is None:
        return math.nan
    return _iv_at(snapshot, pk) - _iv_at(snapshot, ck)


def vrp(atm_iv_val: float, realized_vol: float) -> float:
    """Variance-risk-premium proxy = implied / realized vol (same annualization).
    >1 = options priced richer than the market is moving (complacent). NaN when
    realized vol is 0 (undefined) -- never inf."""
    if realized_vol and realized_vol > 0:
        return atm_iv_val / realized_vol
    return math.nan


def annualized_realized_vol(closes: pd.Series, window: int = 20) -> pd.Series:
    """Trailing ``window``-day realized vol, annualized (x sqrt(252)) to match the
    annualized IV units -- the denominator of VRP."""
    return closes.pct_change().rolling(window).std() * np.sqrt(252)
