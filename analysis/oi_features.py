"""Pure daily open-interest features for the NIFTY regime OI triage.

Snapshot-in / feature-out. Operates on the duck-typed chain snapshot the straddle
helpers use -- ``snapshot.strikes()`` plus ``snapshot.call(k).oi`` /
``snapshot.put(k).oi`` -- so the SAME functions run on a Data-v3 EOD snapshot
(settled OI, backtest) or a live Kite snapshot. No I/O, no look-ahead, fully tested.

Features:
  * ``pcr``            -- put/call OI ratio (positioning tilt), optionally banded.
  * ``max_pain``       -- strike that minimizes total option intrinsic payout.
  * ``oi_walls``       -- (max-call-OI strike = resistance, max-put-OI = support).
  * ``classify_buildup`` -- 4-quadrant read from OI change x price direction.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Tuple


def _oi(snapshot, k, side: str) -> float:
    """OI for one strike/side; an absent strike (real snapshots raise KeyError on
    ``.loc[strike]``) contributes 0 -- so a caller-supplied strike band that runs
    past the listed chain never crashes."""
    try:
        q = snapshot.call(k) if side == "call" else snapshot.put(k)
    except KeyError:
        return 0.0
    return float(getattr(q, "oi", 0) or 0)


def pcr(snapshot, strikes: Optional[Iterable[int]] = None) -> float:
    """Put OI / Call OI over ``strikes`` (default: all strikes in the snapshot).

    Returns NaN when call OI is zero (undefined ratio) -- never inf."""
    ks = list(strikes) if strikes is not None else snapshot.strikes()
    call_oi = sum(_oi(snapshot, k, "call") for k in ks)
    put_oi = sum(_oi(snapshot, k, "put") for k in ks)
    return (put_oi / call_oi) if call_oi > 0 else math.nan


def max_pain(snapshot) -> Optional[int]:
    """Strike minimizing total option intrinsic payout at expiry -- the price that
    hurts the most option BUYERS (writers' pin). ``None`` on an empty chain.

    Payout(P) = sum_K callOI(K)*max(0, P-K) + putOI(K)*max(0, K-P). Ties -> lowest
    strike (deterministic)."""
    ks = snapshot.strikes()
    if not ks:
        return None
    best, best_pay = None, None
    for p in ks:
        pay = 0.0
        for k in ks:
            if p > k:
                pay += _oi(snapshot, k, "call") * (p - k)
            elif k > p:
                pay += _oi(snapshot, k, "put") * (k - p)
        if best_pay is None or pay < best_pay:
            best, best_pay = p, pay
    return best


def oi_walls(snapshot) -> Tuple[Optional[int], Optional[int]]:
    """(call wall = max-call-OI strike = resistance, put wall = max-put-OI = support).
    A side with no OI anywhere -> ``None`` for that wall."""
    ks = snapshot.strikes()
    call_wall = max(ks, key=lambda k: _oi(snapshot, k, "call"), default=None)
    put_wall = max(ks, key=lambda k: _oi(snapshot, k, "put"), default=None)
    if call_wall is not None and _oi(snapshot, call_wall, "call") == 0:
        call_wall = None
    if put_wall is not None and _oi(snapshot, put_wall, "put") == 0:
        put_wall = None
    return call_wall, put_wall


def classify_buildup(d_call_oi: float, d_put_oi: float, price_ret: float) -> str:
    """4-quadrant OI-buildup read from net OI change x price direction:

        price up  + OI up   -> long_buildup      price up  + OI down -> short_covering
        price dn  + OI up   -> short_buildup     price dn  + OI down -> long_unwinding

    ``flat`` when either the net OI change or the price move is zero."""
    total = d_call_oi + d_put_oi
    if price_ret == 0 or total == 0:
        return "flat"
    if price_ret > 0:
        return "long_buildup" if total > 0 else "short_covering"
    return "short_buildup" if total > 0 else "long_unwinding"
