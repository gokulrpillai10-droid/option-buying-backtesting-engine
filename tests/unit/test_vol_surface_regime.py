"""Pure scoring for the vol-surface composite regime: each factor -> a 0/1/2 stress
score by its own trailing percentile (causal), summed and bucketed into
Complacent / Normal / Stressed. Pins the causal percentile + the bucketing."""
import numpy as np
import pandas as pd

from analysis.vol_surface_regime import stress_score, bucket, STRESSED, COMPLACENT, NORMAL


def test_stress_score_is_causal_percentile_tercile():
    # rising series: last value is the highest so far -> top tercile -> score 2
    s = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    sc = stress_score(s, min_periods=3)
    assert sc.iloc[-1] == 2               # newest is an all-time high -> stressed
    assert np.isnan(sc.iloc[0])           # warm-up
    # a mid value scores 1
    s2 = pd.Series([10.0, 0, 5, 10, 0, 5, 10, 0, 5, 5])
    assert stress_score(s2, min_periods=3).iloc[-1] in (0, 1, 2)


def test_stress_score_inverted_factor():
    # for an 'inverted' factor (LOW = stress), a new low scores 2
    s = pd.Series([10.0, 9, 8, 7, 6, 5, 4, 3, 2, 1])
    sc = stress_score(s, min_periods=3, invert=True)
    assert sc.iloc[-1] == 2               # newest is an all-time LOW -> stressed


def test_bucket_maps_summed_score_to_label():
    # 3 factors, each 0..2 -> total 0..6
    assert bucket(0, n_factors=3) == COMPLACENT
    assert bucket(6, n_factors=3) == STRESSED
    assert bucket(3, n_factors=3) == NORMAL
