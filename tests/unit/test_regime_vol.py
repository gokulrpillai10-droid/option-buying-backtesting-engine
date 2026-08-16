"""Sharpened volatility axis for the regime detector: blend price-wobble with
India VIX (level + direction). These pin the look-ahead-safe VIX percentile and
the calm/stormy blend rule (stormy if EITHER says danger; calm only if BOTH agree).
"""
import numpy as np
import pandas as pd

from analysis.regime_vol import (
    expanding_percentile, vix_state, blend_vol, fear_rising,
)


def test_expanding_percentile_is_trailing_and_look_ahead_safe():
    s = pd.Series([30.0, 10.0, 20.0, 40.0, 5.0])
    p = expanding_percentile(s, min_periods=1)
    # each value ranked only within history up to and including itself
    assert p.iloc[0] == 1.0            # [30] -> 30 is the max so far
    assert p.iloc[1] == 0.5            # [30,10] -> 10 <= 1 of 2
    assert round(p.iloc[2], 3) == 0.667  # [30,10,20] -> 20 <= 2 of 3
    assert p.iloc[3] == 1.0            # [.,40] -> 40 is a new high
    assert p.iloc[4] == 0.2            # [.,5]  -> 5 <= 1 of 5


def test_expanding_percentile_warmup_is_nan_before_min_periods():
    s = pd.Series(np.arange(1.0, 11.0))
    p = expanding_percentile(s, min_periods=5)
    assert p.iloc[:4].isna().all()     # not enough history yet
    assert not np.isnan(p.iloc[4])     # 5th obs -> first real percentile


def test_vix_state_from_percentile():
    assert vix_state(0.90) == "high"   # top tercile -> fear
    assert vix_state(0.10) == "low"    # bottom tercile -> calm
    assert vix_state(0.50) is None     # middle -> no strong signal
    assert vix_state(float("nan")) is None


def test_blend_stormy_if_either_flags_danger():
    assert blend_vol(price_high=True, price_low=False, vix_high=False, vix_low=True) == "stormy"
    assert blend_vol(price_high=False, price_low=True, vix_high=True, vix_low=False) == "stormy"


def test_blend_calm_only_if_both_agree_quiet():
    assert blend_vol(price_high=False, price_low=True, vix_high=False, vix_low=True) == "calm"
    # price says calm but VIX does NOT -> not calm (conservative)
    assert blend_vol(price_high=False, price_low=True, vix_high=False, vix_low=False) is None
    # neither -> transitional
    assert blend_vol(price_high=False, price_low=False, vix_high=False, vix_low=False) is None


def test_fear_rising_compares_vix_to_its_recent_self():
    vix = pd.Series([12, 12, 12, 18, 20], dtype=float)   # jumps up
    fr = fear_rising(vix, lookback=3)
    assert bool(fr.iloc[3]) is True     # 18 > 12 (3 bars ago)
    assert bool(fr.iloc[4]) is True     # 20 > 12
    calm = pd.Series([20, 20, 20, 14, 12], dtype=float)  # cools off
    assert bool(fear_rising(calm, lookback=3).iloc[4]) is False
