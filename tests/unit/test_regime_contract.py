"""Contract seam: `classify(as_of, ...)` -> RegimeBias, and the look-ahead LEAK
TEST (spec §149-151, outside-voice #7): today's bias must use NO data timestamped
>= today's open. We prove it by poisoning the as_of row and every later row with
an absurd spike and asserting the bias is byte-identical to the clean run.
"""
import numpy as np
import pandas as pd

from engine.regime.contract import classify, RegimeBias


def _series(n=340, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    closes = pd.Series(15000.0 + np.cumsum(rng.normal(0, 80.0, n)), index=idx)
    vix = pd.Series(14.0 + np.abs(rng.normal(0, 3.0, n)), index=idx)
    return closes, vix


def test_classify_returns_a_regime_bias():
    closes, vix = _series()
    as_of = closes.index[-1]
    bias = classify(as_of, closes=closes, vix=vix)
    assert isinstance(bias, RegimeBias)
    assert bias.as_of == as_of
    assert bias.label in {"BUY", "SELL", "NEUTRAL"}
    assert set(bias.modules) >= {"vol_state", "vol_dynamics", "iv_rv", "vix_level"}


def test_classify_is_lookahead_safe_ignores_as_of_and_future_rows():
    closes, vix = _series()
    as_of = closes.index[-30]                       # leave 30 future rows to poison

    clean = classify(as_of, closes=closes, vix=vix)

    poison_closes = closes.copy()
    poison_vix = vix.copy()
    future = poison_closes.index >= as_of           # as_of itself + everything after
    poison_closes[future] = 999999.0                # absurd spike that WOULD flip any module
    poison_vix[future] = 500.0

    poisoned = classify(as_of, closes=poison_closes, vix=poison_vix)

    assert poisoned.label == clean.label
    assert poisoned.score == clean.score
    assert poisoned.confidence == clean.confidence
    assert poisoned.modules == clean.modules        # no module saw a >= as_of value


def test_classify_skips_skew_when_no_snapshot():
    closes, vix = _series()
    bias = classify(closes.index[-1], closes=closes, vix=vix)
    assert bias.modules["skew"] is None             # no chain snapshot -> skew absent
    assert bias.modules["breadth"] is None          # deferred
