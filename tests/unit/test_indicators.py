"""Oscillator primitives: RSI (Wilder), slow Stochastic %K, Williams %R, EMA.

Anchors each formula against a hand-computed or invariant-based expectation so a
future refactor can't silently drift the signal thresholds the Reversal-Blast
study keys on (RSI<30, %K<20, %R<-80).
"""
import numpy as np
import pandas as pd
import pytest

from engine.indicators import ema, rsi, stoch_k, williams_r


def test_ema_matches_pandas_ewm():
    s = pd.Series([1.0, 2, 3, 4, 5])
    assert ema(s, 3).tolist() == pytest.approx(
        s.ewm(span=3, adjust=False).mean().tolist())


def test_rsi_all_gains_is_100():
    up = pd.Series(np.arange(1, 40, dtype=float))     # strictly rising
    assert rsi(up, 14).iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_0():
    down = pd.Series(np.arange(40, 1, -1, dtype=float))  # strictly falling
    assert rsi(down, 14).iloc[-1] == pytest.approx(0.0)


def test_rsi_flat_series_is_neutral():
    flat = pd.Series([50.0] * 30)
    # no gain, no loss -> neutral 50 (guarded 0/0)
    assert rsi(flat, 14).iloc[-1] == pytest.approx(50.0)


def test_rsi_in_bounds():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + rng.standard_normal(200).cumsum())
    r = rsi(s, 14).dropna()
    assert r.between(0, 100).all()


def test_stoch_k_at_low_is_zero_at_high_is_100():
    # close sits at the rolling low -> raw %K 0; at the rolling high -> 100.
    high = pd.Series([10.0] * 20)
    low = pd.Series([0.0] * 20)
    at_low = stoch_k(high, low, pd.Series([0.0] * 20), period=14, smooth_k=1)
    at_high = stoch_k(high, low, pd.Series([10.0] * 20), period=14, smooth_k=1)
    assert at_low.iloc[-1] == pytest.approx(0.0)
    assert at_high.iloc[-1] == pytest.approx(100.0)


def test_stoch_k_flat_range_neutral():
    flat = pd.Series([5.0] * 20)
    k = stoch_k(flat, flat, flat, period=14, smooth_k=1)
    assert k.iloc[-1] == pytest.approx(50.0)


def test_williams_r_bounds_and_poles():
    high = pd.Series([10.0] * 20)
    low = pd.Series([0.0] * 20)
    at_high = williams_r(high, low, pd.Series([10.0] * 20), period=14)
    at_low = williams_r(high, low, pd.Series([0.0] * 20), period=14)
    assert at_high.iloc[-1] == pytest.approx(0.0)     # top of range
    assert at_low.iloc[-1] == pytest.approx(-100.0)   # bottom of range


def test_williams_r_is_stoch_minus_100():
    # %R = %K_raw - 100 (both off the same HH/LL window) — cross-check identity.
    rng = np.random.default_rng(1)
    c = pd.Series(100 + rng.standard_normal(60).cumsum())
    h = c + 1.0
    lo = c - 1.0
    k_raw = stoch_k(h, lo, c, period=14, smooth_k=1)
    wr = williams_r(h, lo, c, period=14)
    both = pd.concat([k_raw, wr], axis=1).dropna()
    assert (both.iloc[:, 0] - both.iloc[:, 1]).round(6).eq(100.0).all()
