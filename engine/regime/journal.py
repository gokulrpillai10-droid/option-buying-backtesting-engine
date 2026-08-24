"""Outcome journal + bounded adaptive weighting.

Each day the detector logs its module scores. After a horizon H (~21 trading
days) the day is *resolved*: the straddle thermometer supplies an outcome sign
(+1 = BUY was right / gamma paid, -1 = SELL was right / theta paid) and each
module is graded by sign agreement. Weights are then nudged toward the modules
that have been right — under four guards that keep a thin, noisy journal from
hijacking the detector:

  1. min-sample gate  — no adaptation until >= `min_samples` (~40, ~1.5y) resolved.
  2. clamps           — every weight stays in [lo, hi] = [5%, 50%].
  3. EWMA shrinkage   — adapted weights are blended halfway back to base.
  4. revert-on-degrade— if the adapted weights score worse than base on the
                        journal itself, discard them and keep base.

Base weights are always the fallback; adaptation only ever *refines* them.
"""
import numpy as np
import pandas as pd

from engine.regime.classifier import BASE_WEIGHTS

HORIZON = 21          # trading days until a logged day is resolvable
NEUTRAL_HIT = 0.5     # hit-rate that neither rewards nor penalises a module (coin-flip)


def resolvable(journal: pd.DataFrame, now, horizon: int = HORIZON) -> pd.DataFrame:
    """Rows whose horizon has elapsed by ``now`` (as_of + horizon business days <= now)."""
    now = pd.Timestamp(now)
    matured = journal.index + pd.offsets.BDay(horizon)
    return journal[matured <= now]


def grade(journal: pd.DataFrame, outcomes: pd.Series, modules) -> pd.DataFrame:
    """Join resolved outcomes and mark, per module, whether its sign matched the
    outcome sign (a zero-signal day is never 'correct'). Returns resolved rows only."""
    res = journal.join(outcomes.rename("outcome"), how="inner").dropna(subset=["outcome"])
    osign = np.sign(res["outcome"])
    for m in modules:
        if m in res:
            res[m + "_ok"] = (np.sign(res[m]) == osign) & (res[m] != 0)
    return res


def hit_rates(resolved: pd.DataFrame, modules) -> dict:
    """Per-module fraction correct, counting only days the module actually signalled
    (score != 0). None where a module never signalled."""
    hr = {}
    for m in modules:
        if m + "_ok" not in resolved:
            continue
        signalled = resolved[m] != 0
        n = int(signalled.sum())
        hr[m] = float(resolved.loc[signalled, m + "_ok"].mean()) if n > 0 else None
    return hr


def _clamp_normalize(weights, lo, hi):
    """Project weights onto the simplex intersected with the box [lo, hi]
    (iterative water-filling): fix violators at their bound, redistribute the
    remaining mass over the free weights, repeat. Feasible when n*lo <= 1 <= n*hi."""
    keys = list(weights)
    free = dict(weights)
    fixed = {}
    while free:
        remaining = 1.0 - sum(fixed.values())
        s = sum(free.values())
        if s <= 0:
            for k in free:
                fixed[k] = remaining / len(free)
            break
        scaled = {k: v / s * remaining for k, v in free.items()}
        newly = {}
        for k, val in scaled.items():
            if val < lo - 1e-15:
                newly[k] = lo
            elif val > hi + 1e-15:
                newly[k] = hi
        if not newly:
            fixed.update(scaled)
            break
        for k, b in newly.items():
            fixed[k] = b
            free.pop(k)
    return {k: fixed[k] for k in keys}


def adapt_weights(base, hit_rates, n_resolved, min_samples=40,
                  lo=0.05, hi=0.5, shrink=0.5) -> dict:
    """Nudge base weights toward better-hitting modules, subject to the gate,
    shrinkage, and clamps. ``target ∝ base * (hit/NEUTRAL_HIT)`` (a coin-flip hit
    rate = no change), ``blended = shrink*base + (1-shrink)*target``, then projected
    into [lo, hi]."""
    if n_resolved < min_samples:
        return dict(base)
    raw = {}
    for k in base:
        h = hit_rates.get(k)
        factor = 1.0 if h is None else h / NEUTRAL_HIT
        raw[k] = base[k] * factor
    tot = sum(raw.values())
    if tot <= 0:
        return dict(base)
    target = {k: v / tot for k, v in raw.items()}
    blended = {k: shrink * base[k] + (1.0 - shrink) * target[k] for k in base}
    return _clamp_normalize(blended, lo, hi)


def composite_accuracy(resolved: pd.DataFrame, weights, modules) -> float:
    """Share of resolved days on which the weighted composite's sign matched the
    outcome sign (ties / zero-composite days excluded)."""
    if len(resolved) == 0:
        return 0.0
    score = sum(weights[m] * resolved[m] for m in modules if m in resolved)
    pred = np.sign(score)
    osign = np.sign(resolved["outcome"])
    valid = pred != 0
    if int(valid.sum()) == 0:
        return 0.0
    return float((pred[valid] == osign[valid]).mean())


def calibrate(journal: pd.DataFrame, outcomes: pd.Series, base=BASE_WEIGHTS,
              min_samples=40, **kw):
    """Return ``(weights, active)``. Applies the gate and revert-on-degrade: adapted
    weights are used only when there are >= min_samples resolved days AND they do not
    lower composite accuracy on the journal; otherwise base weights, ``active=False``."""
    modules = list(base)
    resolved = grade(journal, outcomes, modules)
    n = len(resolved)
    if n < min_samples:
        return dict(base), False
    adapted = adapt_weights(base, hit_rates(resolved, modules), n,
                            min_samples=min_samples, **kw)
    if composite_accuracy(resolved, adapted, modules) < composite_accuracy(resolved, base, modules):
        return dict(base), False
    return adapted, True
