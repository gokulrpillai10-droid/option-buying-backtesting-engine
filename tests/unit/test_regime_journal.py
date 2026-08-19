"""Outcome journal + adaptive weighting seams (spec §157, §170).

Ground truth is an outcome sign per resolved day (+1 = BUY was right / gamma paid,
-1 = SELL was right / theta paid), supplied by the straddle thermometer. The
journal grades each module by sign agreement, then nudges weights toward the
better modules under four guards: min-40 sample gate, [5%,50%] clamps, EWMA
shrinkage to base, and revert-on-degrade.
"""
import numpy as np
import pandas as pd

from engine.regime.classifier import BASE_WEIGHTS
from engine.regime.journal import (
    resolvable, grade, hit_rates, adapt_weights, composite_accuracy, calibrate,
)

MODS = list(BASE_WEIGHTS)


def _journal(rows):
    """rows: list of dicts with module scores; index = sequential business days."""
    idx = pd.date_range("2021-01-01", periods=len(rows), freq="B")
    return pd.DataFrame(rows, index=idx)


# ---- resolution after horizon H -------------------------------------------- #

def test_resolvable_only_matured_rows():
    j = _journal([{m: 0.0 for m in MODS} for _ in range(10)])
    now = j.index[5]                                  # 6th row's day
    out = resolvable(j, now=now, horizon=3)
    # a row matures 3 business days after its as_of; only rows whose as_of+3 <= now
    assert (out.index + pd.offsets.BDay(3) <= now).all()
    assert len(out) == 3                              # rows 0,1,2 matured by row 5


# ---- grading + hit rates ---------------------------------------------------- #

def test_grade_marks_module_correct_when_sign_matches_outcome():
    j = _journal([{ "vol_state": 1.0, "iv_rv": -1.0 },
                  { "vol_state": -1.0, "iv_rv": -1.0 }])
    outcomes = pd.Series([1.0, 1.0], index=j.index)   # BUY was right both days
    res = grade(j, outcomes, modules=["vol_state", "iv_rv"])
    assert list(res["vol_state_ok"]) == [True, False] # +/+ ok, -/+ wrong
    assert list(res["iv_rv_ok"]) == [False, False]

    hr = hit_rates(res, modules=["vol_state", "iv_rv"])
    assert hr["vol_state"] == 0.5
    assert hr["iv_rv"] == 0.0


# ---- adaptive weights: the four guards ------------------------------------- #

def test_min_sample_gate_below_40_returns_base_untouched():
    hr = {m: 1.0 if m == "iv_rv" else 0.0 for m in MODS}
    w = adapt_weights(BASE_WEIGHTS, hr, n_resolved=39)
    assert w == BASE_WEIGHTS                          # gate closed -> base


def test_no_information_all_half_returns_base():
    hr = {m: 0.5 for m in MODS}                       # coin-flip everywhere
    w = adapt_weights(BASE_WEIGHTS, hr, n_resolved=100)
    assert all(abs(w[m] - BASE_WEIGHTS[m]) < 1e-9 for m in MODS)


def test_clamps_keep_every_weight_in_bounds_and_sum_one():
    hr = {m: (1.0 if m == "iv_rv" else 0.0) for m in MODS}  # one hero, rest useless
    w = adapt_weights(BASE_WEIGHTS, hr, n_resolved=100, lo=0.05, hi=0.5)
    assert all(0.05 - 1e-9 <= v <= 0.5 + 1e-9 for v in w.values())
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_better_module_gains_weight_relative_to_worse():
    hr = {m: 0.5 for m in MODS}
    hr["iv_rv"] = 0.9                                 # clearly better than its base peers
    hr["skew"] = 0.1                                  # clearly worse
    w = adapt_weights(BASE_WEIGHTS, hr, n_resolved=100)
    assert w["iv_rv"] > BASE_WEIGHTS["iv_rv"]
    assert w["skew"] < BASE_WEIGHTS["skew"]


def test_shrinkage_moves_only_partway_to_target():
    hr = {m: 0.5 for m in MODS}
    hr["iv_rv"] = 1.0
    full = adapt_weights(BASE_WEIGHTS, hr, n_resolved=100, shrink=0.0)   # no shrink
    half = adapt_weights(BASE_WEIGHTS, hr, n_resolved=100, shrink=0.5)   # EWMA halfway
    # half sits strictly between base and the fully-adapted target
    assert BASE_WEIGHTS["iv_rv"] < half["iv_rv"] < full["iv_rv"]


# ---- revert-on-degrade ------------------------------------------------------ #

def test_calibrate_gates_below_min_samples():
    j = _journal([{m: 1.0 for m in MODS} for _ in range(20)])
    outcomes = pd.Series(1.0, index=j.index)
    w, active = calibrate(j, outcomes, base=BASE_WEIGHTS, min_samples=40)
    assert active is False
    assert w == BASE_WEIGHTS                          # too few resolved -> base


def test_calibrate_never_returns_weights_worse_than_base():
    # Build a journal where a single module perfectly predicts, but the OTHER
    # modules mislead: adapting toward per-module hit-rate hurts composite accuracy.
    rng = np.random.default_rng(3)
    n = 60
    idx = pd.date_range("2021-01-01", periods=n, freq="B")
    outcome = pd.Series(rng.choice([-1.0, 1.0], n), index=idx)
    rows = []
    for o in outcome:
        row = {m: -o for m in MODS}        # every module is WRONG (anti-correlated)
        row["vol_state"] = o               # except vol_state, always right
        rows.append(row)
    j = pd.DataFrame(rows, index=idx)
    w, active = calibrate(j, outcome, base=BASE_WEIGHTS, min_samples=40)
    # adapting would down-weight the (individually-wrong) modules, but base already
    # scores fine here; revert-on-degrade must never return something worse than base.
    base_acc = composite_accuracy(grade(j, outcome, MODS), BASE_WEIGHTS, MODS)
    used_acc = composite_accuracy(grade(j, outcome, MODS), w, MODS)
    assert used_acc >= base_acc - 1e-9
