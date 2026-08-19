"""Composite classifier: fold the per-module feature scores (each in ``[-1,+1]``,
+1 = BUY/gamma) into one verdict.

    score = Σ wᵢ·moduleᵢ   over the modules that produced a score

Modules that return ``None``/``NaN`` (e.g. skew when a greek is missing, or breadth
while it is deferred) are dropped and the remaining weights are renormalized, so a
missing input never silently drags the score toward zero. The verdict is BUY /
SELL only when the composite clears ``thr`` **and** enough modules agree on its
sign (``confidence = module agreement``); otherwise NEUTRAL. A 4-of-5 persistence
filter (`persist`) then removes single-day flip-flops.
"""
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analysis.regime_finder import persistence

# Base weights (spec §67-74). breadth is deferred; when absent the others
# renormalize over themselves, so its 0.07 is simply redistributed pro-rata.
BASE_WEIGHTS = {
    "vol_state": 0.40,
    "vol_dynamics": 0.20,
    "iv_rv": 0.15,
    "vix_level": 0.10,
    "skew": 0.08,
    "breadth": 0.07,
}


@dataclass(frozen=True)
class Composite:
    score: float
    confidence: float
    label: str          # "BUY" | "SELL" | "NEUTRAL"


def _present(scores):
    """Drop modules with no usable score (None or NaN)."""
    out = {}
    for k, v in scores.items():
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        out[k] = float(v)
    return out


def composite(scores, weights=BASE_WEIGHTS, thr=0.15, min_agreement=0.6) -> Composite:
    """Weighted composite of module scores → BUY/SELL/NEUTRAL. ``confidence`` is the
    fraction of present modules whose sign matches the composite sign (sign
    consensus). NEUTRAL unless ``|score| >= thr`` and ``confidence >= min_agreement``."""
    present = _present(scores)
    if not present:
        return Composite(0.0, 0.0, "NEUTRAL")
    tot = sum(weights[k] for k in present)
    w = {k: weights[k] / tot for k in present}
    score = sum(w[k] * present[k] for k in present)

    sign = np.sign(score)
    if sign == 0:
        return Composite(0.0, 0.0, "NEUTRAL")
    agree = sum(1 for k in present if np.sign(present[k]) == sign)
    confidence = agree / len(present)

    if abs(score) >= thr and confidence >= min_agreement:
        label = "BUY" if sign > 0 else "SELL"
    else:
        label = "NEUTRAL"
    return Composite(float(score), float(confidence), label)


def persist(verdicts: pd.Series, need: int = 4, win: int = 5) -> pd.Series:
    """4-of-5 flip-flop guard on a BUY/SELL/NEUTRAL series: a BUY (SELL) stands only
    where BUY (SELL) occurred on ``>=need`` of the last ``win`` days; otherwise the
    day collapses to NEUTRAL. Reuses `regime_finder.persistence` on each side's mask."""
    buy = persistence(verdicts == "BUY", need, win)
    sell = persistence(verdicts == "SELL", need, win)
    out = pd.Series("NEUTRAL", index=verdicts.index)
    out[sell] = "SELL"
    out[buy] = "BUY"
    return out
