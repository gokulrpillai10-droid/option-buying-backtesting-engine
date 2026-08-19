"""Driver seams for the detector calendar: row-wise composite -> verdicts and the
count/episode helpers. module_frame/classify look-ahead safety is pinned by the
contract leak test; here we lock the calendar mapping on hand-made module rows.
"""
import pandas as pd

from analysis.regime_buysell_run import calendar, _counts, _episode_stats


def test_calendar_maps_module_rows_to_buy_sell_neutral():
    idx = pd.date_range("2021-01-01", periods=3, freq="B")
    mods = pd.DataFrame({
        "vol_state":    [1.0, -1.0, 0.05],
        "vol_dynamics": [1.0, -1.0, 0.05],
        "iv_rv":        [1.0, -1.0, 0.0],
        "vix_level":    [1.0, -1.0, 0.0],
    }, index=idx)
    cal = calendar(mods, thr=0.15)
    assert list(cal) == ["BUY", "SELL", "NEUTRAL"]   # all+, all-, too-weak


def test_counts_and_episode_stats():
    idx = pd.date_range("2021-01-01", periods=6, freq="B")
    cal = pd.Series(["BUY", "BUY", "NEUTRAL", "SELL", "SELL", "SELL"], index=idx)
    assert _counts(cal) == {"BUY": 2, "SELL": 3, "NEUTRAL": 1}
    assert _episode_stats(cal, "SELL") == (1, 3)      # one 3-day SELL run
    assert _episode_stats(cal, "BUY") == (1, 2)
