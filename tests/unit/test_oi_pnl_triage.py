"""Pure analysis helpers of the OI predictive-power triage (corr + tercile spread).
The data-walk driver is exercised by running the script; these pin the stats."""
import numpy as np
import pandas as pd

from analysis.oi_pnl_triage import corr, tercile_spread, welch_t


def test_welch_t_separates_and_is_nan_safe():
    assert welch_t([10, 11, 12, 13], [1, 2, 3, 4]) > 3      # clearly separated means
    assert abs(welch_t([1, 2, 3, 4], [1, 2, 3, 4])) < 1e-9  # identical -> ~0
    assert np.isnan(welch_t([1.0], [2.0, 3.0]))             # too few in a group


def test_corr_perfect_and_degenerate():
    f = pd.Series([1.0, 2, 3, 4, 5])
    assert round(corr(f, f), 6) == 1.0
    assert np.isnan(corr(pd.Series([2.0, 2, 2, 2, 2]), f))   # zero-variance feature


def test_tercile_spread_separates_top_from_bottom():
    f = pd.Series(np.arange(9, dtype=float))
    p = pd.Series(np.arange(9, dtype=float))
    top, bot, spread = tercile_spread(f, p)
    assert top > bot and spread > 0            # pnl rises with the feature
    assert np.isnan(tercile_spread(f.iloc[:3], p.iloc[:3])[2])  # too few points


def test_tercile_spread_nan_safe():
    f = pd.Series([np.nan] * 9)
    assert np.isnan(tercile_spread(f, pd.Series(np.arange(9, dtype=float)))[2])
