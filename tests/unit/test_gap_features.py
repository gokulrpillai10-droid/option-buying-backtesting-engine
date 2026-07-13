from engine.equity.gap_features import (
    Thresholds, Decision, compute_gap, signed_drive, aligned,
    fade_side, go_side, decide_open, decide_0930)

THR = Thresholds()

def test_compute_gap_and_drive():
    assert abs(compute_gap(810.0, 800.0) - 0.0125) < 1e-10
    # drive is signed by gap direction: price up on an up-gap = positive
    assert signed_drive(815.0, 810.0, gap=0.0125) > 0
    # price up on a DOWN-gap = reverting = negative drive
    assert signed_drive(806.0, 800.0, gap=-0.01) < 0

def test_aligned():
    assert aligned(0.01, 0.005, THR) is True          # same sign, big enough
    assert aligned(0.01, 0.0005, THR) is False         # NIFTY gap under nifty_min
    assert aligned(0.01, -0.005, THR) is False         # opposite sign
    assert aligned(0.01, None, THR) is False           # missing NIFTY

def test_decide_open_no_gap_is_none():
    assert decide_open(0.001, None, THR) == Decision("none", None)

def test_decide_open_small_unaligned_fades_at_open():
    # gap up 0.25% (< big 0.4%), NIFTY not aligned -> fade short now
    assert decide_open(0.0025, None, THR) == Decision("fade", "short")
    # gap down mirror -> fade long
    assert decide_open(-0.0025, None, THR) == Decision("fade", "long")

def test_decide_open_big_or_aligned_defers():
    assert decide_open(0.01, None, THR).branch == "defer"        # big gap
    assert decide_open(0.002, 0.005, THR).branch == "defer"      # small but aligned

def test_decide_0930_reverting_fades():
    # deferred up-gap, price reverted by 09:30 (drive<=0) -> fade short
    assert decide_0930(0.01, None, drive=-0.004, thr=THR) == Decision("fade", "short")

def test_decide_0930_driving_and_big_goes():
    assert decide_0930(0.01, None, drive=0.006, thr=THR) == Decision("go", "long")

def test_decide_0930_ambiguous_is_no_trade():
    # positive drive but below drive_thr -> no trade
    assert decide_0930(0.01, None, drive=0.001, thr=THR) == Decision("none", None)
