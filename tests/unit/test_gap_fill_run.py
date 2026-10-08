"""Pure reporting helpers for the gap-fill run driver (spec §118-123): |gap|
sub-bucketing and per-cell metrics. Expected values are hand-derived literals.
"""
import numpy as np
import pandas as pd

from strategies import gap_fill_params as P
from analysis.gap_fill_fade_run import bucket_of, cell_stats


def test_bucket_of_partitions_gap_including_diagnostic_edges():
    assert bucket_of(P.FLOOR / 2) == "<FLOOR"          # below the traded band
    assert bucket_of(-(P.FLOOR / 2)) == "<FLOOR"        # |gap| based (sign-agnostic)
    assert bucket_of(P.FLOOR + 0.0001) == "F-0.15%"
    assert bucket_of(0.0020) == "0.15-0.30%"
    assert bucket_of(0.0040) == "0.30%-CAP"
    assert bucket_of(P.CAP + 0.001) == ">CAP"           # above the traded band
    assert bucket_of(None) == "?"


def test_cell_stats_metrics_from_a_known_frame():
    df = pd.DataFrame({
        "net":    [100.0, -50.0, 100.0],   # 2 wins, 1 loss
        "entry":  [40.0, 60.0, 50.0],      # avg debit 50
        "reason": ["book", "pstop", "trail"],
    })
    c = cell_stats(df)
    assert c["n"] == 3
    assert c["net"] == 150.0
    assert c["pf"] == 200.0 / 50.0         # gross win 200 / gross loss 50 = 4.0
    assert c["win"] == 2 / 3
    assert c["avg_debit"] == 50.0
    assert c["avg_net"] == 50.0
    assert c["exits"] == {"book": 1, "pstop": 1, "trail": 1}
    # t-stat = mean / (sd/sqrt n), computed independently here
    net = np.array([100.0, -50.0, 100.0])
    expect_t = net.mean() / (net.std(ddof=1) / np.sqrt(3))
    assert c["t"] == expect_t


def test_cell_stats_empty_is_none():
    assert cell_stats(pd.DataFrame({"net": [], "entry": [], "reason": []})) is None
