"""Unit tests for the regime labeler (analysis/regime_finder.py).

Synthetic-series design: vol labels are z-scores RELATIVE to a 90-day
baseline, so quiet-then-wild triggers high_vol. Trend labels (bull/bear)
are an ABSOLUTE gate: price vs its own 200-day MA + MA slope. To force a
trend label, a series must actually cross its rolling MA200 and hold
there long enough for the MA to turn — a short flat-then-move segment is
not enough; the move must be long/steep enough to pull the MA itself.
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
    # first valid row now needs ma_win (200) observations (dominates lookback+z_win);
    # rolling(200) is first valid at index 199
    assert labels.index[0] >= closes.index[199]
    assert labels.index[0] <= closes.index[215]  # not over-truncated
    assert labels.notna().all().all()


def test_flat_series_no_crash_no_labels():
    closes = _series(np.full(400, 100.0))  # literally constant: rolling stds are 0
    labels = label_regimes(closes)
    assert not labels[["bull", "bear", "high_vol", "low_vol"]].any().any()
    assert labels.dtypes.eq(bool).all()


def test_rising_ramp_labels_bull():
    # 400 pts, steadily rising ~0.05%/day + tiny noise -> price pulls away
    # above its own rising MA200 well before the series ends.
    n = 400
    rng = np.random.default_rng(11)
    rets = 0.0005 + rng.normal(0.0, 0.0005, n)
    closes = _series(100.0 * np.cumprod(1.0 + rets))
    labels = label_regimes(closes)
    move = labels.iloc[-60:]  # well after MA200 has turned up
    assert move["bull"].sum() > 0
    assert move["stable_bull"].sum() > 0
    assert move["stable_bear"].sum() == 0


def test_sustained_decline_labels_bear():
    # rise for 250 days (builds a rising MA200), then decline for 200 days
    # -- long/steep enough for price to cross under the MA and for the
    # MA itself to roll over and start falling.
    rng = np.random.default_rng(13)
    up = 0.0006 + rng.normal(0.0, 0.0005, 250)
    down = -0.0012 + rng.normal(0.0, 0.001, 200)
    rets = np.concatenate([up, down])
    closes = _series(100.0 * np.cumprod(1.0 + rets))
    labels = label_regimes(closes)
    move = labels.iloc[-60:]  # deep into the decline segment
    assert move["bear"].sum() > 0
    assert move["stable_bear"].sum() > 0
    assert move["stable_bull"].sum() == 0


def test_flat_ma_below_price_is_neutral_not_bear():
    # An exactly-flat MA is neither rising nor falling -> price below it must be
    # NEUTRAL, not bear. Guards against bear = close<ma & ~rising (which would
    # fold the flat band into bear; "not rising" != "falling").
    # A perfectly alternating 12/8 series has a 2-day MA that is exactly 10 on
    # every step, so the MA never rises or falls; the "8" rows sit below it.
    closes = _series([12.0 if i % 2 == 0 else 8.0 for i in range(140)])
    labels = label_regimes(closes, ma_win=2, slope_win=1)
    ma = closes.rolling(2).mean()
    below = (closes < ma).loc[labels.index]
    assert below.any()               # the "8" rows really are below the flat MA
    assert labels["bear"].sum() == 0  # yet nothing is bear (MA never falls)
    assert labels["bull"].sum() == 0  # symmetric: nothing bull either (never rises)


def test_crash_labels_high_vol():
    closes = make_closes(drift=-0.008, vol=0.02)
    labels = label_regimes(closes)
    move = labels.iloc[-60:]
    assert move["high_vol"].sum() > 0


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


def test_combined_state_high_vol_without_stable_direction_is_neutral():
    # high_vol raw but no stable bull/bear -> not a HighVol_* state; and the
    # LowVol arms require stable_low_vol, which a high_vol day doesn't have ->
    # falls through to Neutral. Exercises the `free`-guard fallthrough branch.
    labels = pd.DataFrame(
        {
            "high_vol": [True, False],
            "stable_low_vol": [False, False],
            "stable_bull": [False, False],
            "stable_bear": [False, False],
        },
        index=IDX(2),
    )
    assert list(combined_state(labels)) == ["Neutral", "Neutral"]


def test_lagged_shifts_one_day():
    labels = label_regimes(make_closes(drift=-0.008, vol=0.02))
    lag = lagged(labels)
    assert not lag.iloc[0].any()  # day one: nothing known yet
    assert (lag["bear"].iloc[1:].values == labels["bear"].iloc[:-1].values).all()
    assert lag.dtypes.eq(bool).all()
