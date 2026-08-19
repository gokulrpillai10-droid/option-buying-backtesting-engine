"""Classifier seams: weighted composite -> BUY/SELL/NEUTRAL with confidence =
module agreement, and the 4-of-5 persistence flip-flop guard. Weights are passed
explicitly so every expected score is hand-computed, not recomputed by the code.
"""
import numpy as np
import pandas as pd

from engine.regime.classifier import composite, persist


def test_unanimous_positive_is_buy_full_confidence():
    r = composite({"a": 1.0, "b": 0.5}, weights={"a": 0.5, "b": 0.5}, thr=0.15)
    assert r.label == "BUY"
    assert r.score == 0.75                    # 0.5*1.0 + 0.5*0.5
    assert r.confidence == 1.0                # both modules agree with the + sign


def test_unanimous_negative_is_sell():
    r = composite({"a": -1.0, "b": -0.4}, weights={"a": 0.5, "b": 0.5}, thr=0.15)
    assert r.label == "SELL"
    assert r.score == -0.7


def test_below_threshold_is_neutral_even_when_agreed():
    r = composite({"a": 0.1, "b": 0.1}, weights={"a": 0.5, "b": 0.5}, thr=0.15)
    assert r.label == "NEUTRAL"               # |0.1| < thr
    assert r.confidence == 1.0                # still fully agreed, just too weak


def test_low_agreement_forces_neutral_despite_magnitude():
    # score sign is negative and |score| > thr, but only 2 of 5 modules agree.
    scores = {"a": 0.1, "b": 0.1, "c": 0.1, "d": -1.0, "e": -1.0}
    w = {k: 0.2 for k in scores}
    r = composite(scores, weights=w, thr=0.15, min_agreement=0.6)
    assert round(r.score, 4) == -0.34         # (0.3 - 2.0)/5
    assert r.confidence == 0.4                # 2 of 5 modules on the sign
    assert r.label == "NEUTRAL"


def test_missing_modules_dropped_and_weights_renormalized():
    r = composite({"a": 1.0, "b": None, "c": float("nan")},
                  weights={"a": 0.5, "b": 0.3, "c": 0.2}, thr=0.15)
    assert r.label == "BUY"
    assert r.score == 1.0                     # only 'a' present -> weight renormalized to 1.0


def test_no_modules_present_is_neutral_zero_confidence():
    r = composite({"a": None}, weights={"a": 1.0})
    assert r.label == "NEUTRAL"
    assert r.confidence == 0.0


# ---- persistence: 4-of-5 flip-flop guard ----------------------------------- #

def test_persist_drops_lone_flip_keeps_a_run():
    idx = pd.date_range("2021-01-01", periods=7, freq="B")
    verdicts = pd.Series(
        ["NEUTRAL", "BUY", "NEUTRAL", "BUY", "BUY", "BUY", "BUY"], index=idx)
    out = persist(verdicts, need=4, win=5)
    assert out.iloc[1] == "NEUTRAL"           # a lone BUY can't persist
    assert out.iloc[6] == "BUY"               # 4-of-5 BUY by the last day -> holds
