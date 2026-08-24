"""Feature-score seams for the regime detector.

Every module returns a per-day score in [-1, +1] where +1 favors option BUYING
(gamma / storm) and -1 favors option SELLING (theta / calm). Expected values are
hand-derived from the documented formula, not recomputed the way the code does.
"""
import numpy as np
import pandas as pd
import pytest

from engine.regime.features import (
    vol_state, iv_rv_ratio, vix_percentile, vol_dynamics, atm_skew,
)


def _days(n):
    return pd.date_range("2021-01-01", periods=n, freq="B")


# ---- vol_state: PRIMARY axis, maps regime_finder labels to a score ---------- #

def test_vol_state_storm_is_buy_calm_is_sell_else_zero():
    idx = _days(3)
    labels = pd.DataFrame(
        {"high_vol": [True, False, False], "stable_low_vol": [False, True, False]},
        index=idx,
    )
    out = vol_state(labels)
    assert list(out) == [1.0, -1.0, 0.0]          # storm→buy, calm→sell, neither→0


# ---- iv_rv_ratio: VRP refinement, high IV/RV -> sell, low -> buy ------------ #

def test_iv_rv_ratio_flat_price_zero_vol_guard():
    # a genuinely flat price series -> all returns exactly 0 -> realized vol 0 ->
    # zero-vol guard yields score 0.0 (no signal), never a divide-by-zero blow-up.
    closes = pd.Series(100.0, index=_days(30))
    out = iv_rv_ratio(pd.Series(20.0, index=closes.index), closes, window=21)
    assert out.iloc[-1] == 0.0


def test_iv_rv_ratio_low_iv_vs_high_rv_scores_buy():
    rng = np.random.default_rng(0)
    closes = pd.Series(100.0 + np.cumsum(rng.normal(0, 2.0, 300)), index=_days(300))
    rv_pct = closes.pct_change().rolling(21).std().iloc[-1] * np.sqrt(252) * 100
    vix = pd.Series(rv_pct * 0.5, index=closes.index)   # IV half of RV -> ratio 0.5 -> +0.5 buy
    out = iv_rv_ratio(vix, closes, window=21)
    assert out.iloc[-1] == 0.5                          # ratio 0.5 -> clip(1-0.5) = +0.5 (buy)


def test_iv_rv_ratio_rich_premium_saturates_sell():
    rng = np.random.default_rng(1)
    closes = pd.Series(100.0 + np.cumsum(rng.normal(0, 2.0, 300)), index=_days(300))
    rv_pct = closes.pct_change().rolling(21).std().iloc[-1] * np.sqrt(252) * 100
    vix = pd.Series(rv_pct * 3.0, index=closes.index)   # IV 3x RV -> ratio 3 -> clip(1-3) = -1 (sell)
    out = iv_rv_ratio(vix, closes, window=21)
    assert out.iloc[-1] == -1.0


# ---- vix_percentile: expanding, low pct -> buy (cheap), high -> sell -------- #

def test_vix_percentile_is_lookahead_safe_and_signed():
    # strictly increasing VIX: last day is the max -> pct=1.0 -> score = 1-2 = -1 (sell/rich)
    vix = pd.Series(np.arange(1, 301, dtype=float), index=_days(300))
    out = vix_percentile(vix, min_periods=252)
    assert out.iloc[-1] == -1.0
    assert np.isnan(out.iloc[100])                      # warm-up truncation (<252 obs)


# ---- vol_dynamics: expanding vol -> buy, contracting -> sell ---------------- #

def test_vol_dynamics_expanding_buys_contracting_sells():
    idx = _days(15)
    vix = pd.Series([10.0] * 11 + [12.0, 8.0, 10.0, 10.0], index=idx)  # 10-day lookback
    out = vol_dynamics(vix, lookback=10, scale=0.2)
    assert out.iloc[11] == 1.0     # 10->12 = +20% rel change / 0.2 -> clip +1 (expanding -> buy)
    assert out.iloc[12] == -1.0    # 10->8  = -20% -> clip -1 (contracting -> sell)


# ---- skew: chain snapshot, missing greek -> None passthrough ---------------- #

class _Q:
    def __init__(self, iv):
        self.iv = iv


class _Snap:
    def __init__(self, call_iv, put_iv):
        self._c, self._p = _Q(call_iv), _Q(put_iv)

    def call(self, strike):
        return self._c

    def put(self, strike):
        return self._p


def test_skew_put_richer_than_call_scores_buy_fear():
    snap = _Snap(call_iv=0.10, put_iv=0.11)              # puts 10% richer / scale 0.1 -> +1
    assert atm_skew(snap, ref_price=20000, scale=0.1) == pytest.approx(1.0)


def test_skew_missing_greek_returns_none():
    assert atm_skew(_Snap(call_iv=None, put_iv=0.11), ref_price=20000) is None
    assert atm_skew(_Snap(call_iv=0.10, put_iv=None), ref_price=20000) is None
