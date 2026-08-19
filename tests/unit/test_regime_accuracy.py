"""Pure helpers for the regime accuracy test: forward outcomes + a confusion
matrix + Welch t. These pin the look-ahead direction (targets look FORWARD; the
label is lagged by the driver) and the classification arithmetic."""
import numpy as np
import pandas as pd

from analysis.regime_accuracy import (
    forward_return, forward_realized_vol, confusion, welch_t, horizon_persistence,
    forward_drawdown,
)


def test_forward_drawdown_is_worst_next_window_dip():
    c = pd.Series([100.0, 110.0, 90.0, 95.0])
    dd = forward_drawdown(c, window=2)
    assert round(dd.iloc[0], 4) == -0.10       # min(110,90)=90 -> 90/100-1
    assert np.isnan(dd.iloc[-1])               # no future left


def test_horizon_persistence_conditions_on_holding_today():
    m = pd.Series([True, True, True, False, False])
    # holds at idx 0,1,2; one day ahead it holds at 0,1 (idx2->False) -> 2/3
    assert round(horizon_persistence(m, 1), 3) == round(2 / 3, 3)
    assert np.isnan(horizon_persistence(pd.Series([False, False]), 1))  # never holds -> nan, no crash


def test_forward_return_looks_ahead_window_days():
    c = pd.Series([100.0, 110.0, 121.0, 133.1])
    fr = forward_return(c, window=1)
    assert round(fr.iloc[0], 4) == 0.1        # 110/100 - 1
    assert round(fr.iloc[1], 4) == 0.1        # 121/110 - 1
    assert np.isnan(fr.iloc[-1])              # no future left


def test_forward_realized_vol_zero_on_constant_growth_and_trails_nan():
    c = pd.Series([100.0 * (1.01 ** i) for i in range(10)])   # constant daily return
    frv = forward_realized_vol(c, window=3)
    assert abs(frv.iloc[0]) < 1e-9            # constant returns -> zero forward vol
    assert frv.iloc[-3:].isna().all()         # last `window` days have no full future


def test_confusion_perfect_and_mixed():
    pred = pd.Series([True, True, False, False])
    good = confusion(pred, pd.Series([True, True, False, False]))
    assert good["accuracy"] == 1.0 and good["precision"] == 1.0 and good["recall"] == 1.0
    mixed = confusion(pd.Series([True, True, False, True]),
                      pd.Series([True, False, False, False]))
    # tp=1, fp=2, tn=1, fn=0 -> acc=2/4, precision=1/3, recall=1/1
    assert mixed["accuracy"] == 0.5
    assert round(mixed["precision"], 3) == 0.333
    assert mixed["recall"] == 1.0


def test_welch_t_separates_and_nan_safe():
    assert welch_t([10, 11, 12, 13], [1, 2, 3, 4]) > 3
    assert np.isnan(welch_t([1.0], [2.0, 3.0]))
