"""Unit tests for the regime labeler (analysis/regime_finder.py).

Synthetic-series design: the labels are z-scores RELATIVE to a 90-day
baseline, so a steady trend scores z~0. To force a label, a series must
CHANGE character: flat-then-rally -> bull, flat-then-crash -> bear,
quiet-then-wild -> high_vol.
"""
import numpy as np
import pandas as pd
import pytest

from analysis.regime_finder import persistence, label_regimes, episodes, combined_state, lagged

IDX = lambda n: pd.date_range("2019-01-01", periods=n, freq="B")


def _series(vals):
    return pd.Series(vals, index=IDX(len(vals)))


def make_closes(n_flat=250, n_move=80, drift=0.0, vol=0.002, seed=7, start=100.0):
    rng = np.random.default_rng(seed)
    flat = rng.normal(0.0, 0.002, n_flat)
    move = rng.normal(drift, vol, n_move)
    rets = np.concatenate([flat, move])
    return _series(start * np.cumprod(1.0 + rets))


def test_persistence_four_of_five_passes():
    raw = _series([True, True, True, True, False, True]).astype(bool)
    out = persistence(raw, need=4, win=5)
    assert bool(out.iloc[5])  # T,T,T,F,T window has 4 Trues and today is True


def test_persistence_three_of_five_fails():
    raw = _series([True, True, False, False, True, True]).astype(bool)
    out = persistence(raw, need=4, win=5)
    assert not bool(out.iloc[5])  # only 3 of last 5


def test_persistence_requires_today_true():
    raw = _series([True, True, True, True, True, False]).astype(bool)
    out = persistence(raw, need=4, win=5)
    assert not bool(out.iloc[5])  # 4-of-5 satisfied but today is False


def test_warmup_truncated():
    closes = make_closes()
    labels = label_regimes(closes)
    # first valid row needs lookback (20) + z_win (90) observations
    assert labels.index[0] >= closes.index[109]
    assert labels.index[0] <= closes.index[130]  # not over-truncated
    assert labels.notna().all().all()


def test_flat_series_no_crash_no_labels():
    closes = _series(np.full(400, 100.0))  # literally constant: rolling stds are 0
    labels = label_regimes(closes)
    assert not labels[["bull", "bear", "high_vol", "low_vol"]].any().any()
    assert labels.dtypes.eq(bool).all()


def test_rally_after_flat_labels_bull():
    closes = make_closes(drift=0.006, vol=0.004)
    labels = label_regimes(closes)
    move = labels.iloc[-60:]  # inside the rally segment
    assert move["bull"].sum() > 0
    assert move["stable_bull"].sum() > 0
    assert move["stable_bear"].sum() == 0


def test_crash_after_flat_labels_bear_and_high_vol():
    closes = make_closes(drift=-0.008, vol=0.02)
    labels = label_regimes(closes)
    move = labels.iloc[-60:]
    assert move["bear"].sum() > 0
    assert move["stable_bear"].sum() > 0
    assert move["high_vol"].sum() > 0
    assert move["stable_bull"].sum() == 0


def test_vol_spike_alone_labels_high_vol():
    closes = make_closes(drift=0.0, vol=0.025)
    labels = label_regimes(closes)
    assert labels.iloc[-60:]["high_vol"].sum() > 0


def test_episodes_finds_contiguous_runs():
    mask = _series([False, True, True, False, True]).astype(bool)
    eps = episodes(mask)
    assert len(eps) == 2
    (s1, e1, d1), (s2, e2, d2) = eps
    assert (s1, e1, d1) == (mask.index[1], mask.index[2], 2)
    assert (s2, e2, d2) == (mask.index[4], mask.index[4], 1)


def test_episodes_empty_mask():
    assert episodes(_series([False, False]).astype(bool)) == []


def test_combined_state_priority_high_vol_wins():
    labels = pd.DataFrame(
        {
            "high_vol": [True, False, False, False],
            "stable_low_vol": [True, True, True, False],
            "stable_bull": [True, True, False, False],
            "stable_bear": [False, False, True, False],
        },
        index=IDX(4),
    )
    s = combined_state(labels)
    assert list(s) == ["HighVol_Bull", "LowVol_Bull", "LowVol_Bear", "Neutral"]


def test_lagged_shifts_one_day():
    labels = label_regimes(make_closes(drift=-0.008, vol=0.02))
    lag = lagged(labels)
    assert not lag.iloc[0].any()  # day one: nothing known yet
    assert (lag["bear"].iloc[1:].values == labels["bear"].iloc[:-1].values).all()
    assert lag.dtypes.eq(bool).all()
