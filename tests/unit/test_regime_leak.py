"""Dedicated look-ahead LEAK TEST (spec §149-151, outside-voice #7).

Every detector input carries an explicit as-of stamp, and today's bias must use
NO data timestamped >= today's open. This module asserts that per input:
  * spot OHLC  — poisoning rows >= as_of leaves the bias unchanged
  * VIX EOD    — same
  * chain skew — a snapshot stamped >= as_of is REJECTED (enforced, not trusted);
                 a prior-close snapshot is accepted
(breadth is deferred/None, so it cannot leak.)
"""
import numpy as np
import pandas as pd
import pytest

from engine.regime.contract import classify
from engine.data.chain import OptionChainSnapshot


def _series(n=340, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    closes = pd.Series(15000.0 + np.cumsum(rng.normal(0, 80.0, n)), index=idx)
    vix = pd.Series(14.0 + np.abs(rng.normal(0, 3.0, n)), index=idx)
    return closes, vix


class _Q:
    def __init__(self, iv):
        self.iv = iv


class _Snap:
    """Minimal chain snapshot exposing an as-of stamp + call/put IV (for skew)."""
    def __init__(self, as_of, call_iv=0.11, put_iv=0.12):
        self.as_of = as_of
        self._c, self._p = _Q(call_iv), _Q(put_iv)

    def call(self, strike):
        return self._c

    def put(self, strike):
        return self._p


def test_spot_rows_at_or_after_as_of_do_not_move_the_bias():
    closes, vix = _series()
    as_of = closes.index[-30]
    clean = classify(as_of, closes=closes, vix=vix)
    poison = closes.copy()
    poison[poison.index >= as_of] = 999999.0
    assert classify(as_of, closes=poison, vix=vix).modules == clean.modules


def test_vix_rows_at_or_after_as_of_do_not_move_the_bias():
    closes, vix = _series()
    as_of = closes.index[-30]
    clean = classify(as_of, closes=closes, vix=vix)
    poison = vix.copy()
    poison[poison.index >= as_of] = 500.0
    assert classify(as_of, closes=closes, vix=poison).modules == clean.modules


def test_skew_snapshot_stamped_at_or_after_as_of_is_rejected():
    closes, vix = _series()
    as_of = closes.index[-1]
    with pytest.raises(ValueError, match="look-ahead"):
        classify(as_of, closes=closes, vix=vix,
                 snapshot=_Snap(as_of=as_of), ref_price=15000)   # stamped AT as_of
    future = as_of + pd.offsets.BDay(1)
    with pytest.raises(ValueError, match="look-ahead"):
        classify(as_of, closes=closes, vix=vix,
                 snapshot=_Snap(as_of=future), ref_price=15000)  # stamped AFTER


def test_prior_close_skew_snapshot_is_accepted():
    closes, vix = _series()
    as_of = closes.index[-1]
    prior = closes.index[-2]
    bias = classify(as_of, closes=closes, vix=vix,
                    snapshot=_Snap(as_of=prior, call_iv=0.10, put_iv=0.12), ref_price=15000)
    assert bias.modules["skew"] is not None                      # prior-close snapshot used


def test_option_chain_snapshot_exposes_its_as_of_stamp():
    dt = pd.Timestamp("2024-01-05 15:29:00")
    snap = OptionChainSnapshot(dt, pd.DataFrame({"DateTime": [dt], "StrikePrice": [20000]}))
    assert snap.as_of == dt
