"""Pure grading logic for the live regime forward-validation journal: was the
vol-state / direction call right once the horizon matured? Mixed/neutral days
make no call (None), so they don't pollute the scorecard."""
from analysis.regime_forward_journal import grade_vol, grade_dir


def test_grade_vol_stormy_right_when_future_vol_high():
    assert grade_vol(stormy=True, calm=False, fwd_vol=0.012, thr=0.008) is True
    assert grade_vol(stormy=True, calm=False, fwd_vol=0.005, thr=0.008) is False


def test_grade_vol_calm_right_when_future_vol_low():
    assert grade_vol(stormy=False, calm=True, fwd_vol=0.004, thr=0.008) is True
    assert grade_vol(stormy=False, calm=True, fwd_vol=0.011, thr=0.008) is False


def test_grade_vol_mixed_makes_no_call():
    assert grade_vol(stormy=False, calm=False, fwd_vol=0.01, thr=0.008) is None


def test_grade_dir_bull_bear_neutral():
    assert grade_dir(bull=True, bear=False, fwd_ret=0.02) is True
    assert grade_dir(bull=True, bear=False, fwd_ret=-0.02) is False
    assert grade_dir(bull=False, bear=True, fwd_ret=-0.02) is True
    assert grade_dir(bull=False, bear=True, fwd_ret=0.02) is False
    assert grade_dir(bull=False, bear=False, fwd_ret=0.02) is None   # neutral -> no call
