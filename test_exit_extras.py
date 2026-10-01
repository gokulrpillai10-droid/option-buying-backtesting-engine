"""Extra tests for engine/strategy/exit.py covering what tests/unit/test_exit.py
does not: Exit.trail, Exit.spot_at, invalid-trigger guards, Bracket.consume,
TwoStage state machine, and resolve_level_exit boundary cases."""
import pytest
from engine.strategy.exit import Exit, Bracket, TwoStage, resolve_level_exit


# ---------------------------------------------------------------------------
# Exit factory guards
# ---------------------------------------------------------------------------

def test_exit_at_invalid_trigger_raises():
    with pytest.raises(ValueError, match="trigger must be"):
        Exit.at(100.0, "up")


def test_exit_spot_at_invalid_trigger_raises():
    with pytest.raises(ValueError, match="trigger must be"):
        Exit.spot_at(100.0, "down")


# ---------------------------------------------------------------------------
# Exit.trail constructor
# ---------------------------------------------------------------------------

def test_trail_sets_kind_and_gap():
    e = Exit.trail(gap=15.0, reason="ptrail")
    assert e.kind == "trail"
    assert e.gap == 15.0
    assert e.arm is None
    assert e.fraction == 1.0


def test_trail_with_arm():
    e = Exit.trail(gap=10.0, arm=150.0)
    assert e.arm == 150.0


def test_trail_fraction_is_forwarded():
    e = Exit.trail(gap=5.0, fraction=0.5)
    assert e.fraction == 0.5


# ---------------------------------------------------------------------------
# Exit.spot_at constructor
# ---------------------------------------------------------------------------

def test_spot_at_sets_kind_and_level():
    e = Exit.spot_at(level=23500.0, trigger="fall", reason="spot_sl")
    assert e.kind == "spot_level"
    assert e.level == 23500.0
    assert e.trigger == "fall"


def test_spot_at_rise_trigger():
    e = Exit.spot_at(level=24000.0, trigger="rise")
    assert e.trigger == "rise"


# ---------------------------------------------------------------------------
# Exit.market fraction
# ---------------------------------------------------------------------------

def test_market_exit_fraction():
    e = Exit.market(reason="scale", fraction=0.5)
    assert e.kind == "market"
    assert e.fraction == 0.5


# ---------------------------------------------------------------------------
# Bracket.consume
# ---------------------------------------------------------------------------

def test_consume_after_target_moves_target_to_inf():
    br = Exit.bracket(stop=70.0, target=130.0,
                      stop_reason="stop", target_reason="target")
    consumed = br.consume("target")
    assert consumed.target == float("inf")
    assert consumed.stop == 70.0            # stop unchanged


def test_consume_after_stop_moves_stop_to_neg_inf():
    br = Exit.bracket(stop=70.0, target=130.0,
                      stop_reason="stop", target_reason="target")
    consumed = br.consume("stop")
    assert consumed.stop == float("-inf")
    assert consumed.target == 130.0         # target unchanged


def test_consumed_target_never_hits_again():
    br = Exit.bracket(stop=70.0, target=130.0).consume("target")
    fill, reason, _ = br.resolve(open_=100, high=200, low=99)   # huge bar, only stop in range
    assert fill is None or reason == "stop"


def test_consumed_stop_never_hits_again():
    br = Exit.bracket(stop=70.0, target=130.0).consume("stop")
    fill, reason, _ = br.resolve(open_=100, high=101, low=1)    # huge down bar, stop consumed
    assert fill is None or reason == "target"


# ---------------------------------------------------------------------------
# resolve_level_exit boundary cases
# ---------------------------------------------------------------------------

def test_stop_touches_exactly_at_low():
    # low == level is a touch -> should hit
    fill, hit = resolve_level_exit(Exit.at(70.0, "fall"), open_=100, high=101, low=70.0)
    assert hit and fill == 70.0


def test_target_touches_exactly_at_high():
    # high == level is a touch -> should hit
    fill, hit = resolve_level_exit(Exit.at(130.0, "rise"), open_=100, high=130.0, low=99)
    assert hit and fill == 130.0


def test_stop_open_exactly_at_level_is_not_a_gap():
    # open == level: bar opened right at the stop; not gapped through, fills at level
    fill, hit = resolve_level_exit(Exit.at(70.0, "fall"), open_=70.0, high=72.0, low=68.0)
    assert hit
    # open == level means open is NOT strictly below level so fill should be level, not open
    assert fill == 70.0


def test_target_open_exactly_at_level_is_not_a_gap():
    fill, hit = resolve_level_exit(Exit.at(130.0, "rise"), open_=130.0, high=132.0, low=128.0)
    assert hit
    assert fill == 130.0


# ---------------------------------------------------------------------------
# TwoStage state machine
# ---------------------------------------------------------------------------

class _FakePos:
    def __init__(self, qty, entry):
        self.qty = qty
        self.entry = entry


def test_two_stage_stage1_returns_bracket():
    ts = TwoStage(target_pts=20.0, trail_gap=10.0, fraction=0.5, stop_pts=15.0)
    pos = _FakePos(qty=2, entry=100.0)
    result = ts.exit_for(pos)
    assert isinstance(result, Bracket)
    assert result.target == 120.0   # entry + target_pts
    assert result.stop == 85.0      # entry - stop_pts


def test_two_stage_no_stop_sets_neg_inf():
    ts = TwoStage(target_pts=20.0, trail_gap=10.0, fraction=0.5, stop_pts=None)
    pos = _FakePos(qty=2, entry=100.0)
    result = ts.exit_for(pos)
    assert result.stop == TwoStage.NO_STOP


def test_two_stage_after_scale_out_returns_trail():
    ts = TwoStage(target_pts=20.0, trail_gap=10.0, fraction=0.5)
    full_pos = _FakePos(qty=2, entry=100.0)
    ts.exit_for(full_pos)          # seeds _full = 2
    runner = _FakePos(qty=1, entry=100.0)
    result = ts.exit_for(runner)
    assert isinstance(result, Exit)
    assert result.kind == "trail"
    assert result.gap == 10.0


def test_two_stage_reset_clears_scale_detection():
    ts = TwoStage(target_pts=20.0, trail_gap=10.0, fraction=0.5)
    full_pos = _FakePos(qty=2, entry=100.0)
    ts.exit_for(full_pos)
    ts.reset()
    # after reset, a qty=1 pos is seen as a fresh full-size entry -> back to bracket
    new_pos = _FakePos(qty=1, entry=110.0)
    result = ts.exit_for(new_pos)
    assert isinstance(result, Bracket)


def test_two_stage_scaled_false_on_first_call():
    ts = TwoStage(target_pts=20.0, trail_gap=10.0, fraction=0.5)
    pos = _FakePos(qty=3, entry=100.0)
    assert not ts.scaled(pos)


def test_two_stage_scaled_true_after_qty_shrinks():
    ts = TwoStage(target_pts=20.0, trail_gap=10.0, fraction=0.5)
    full = _FakePos(qty=3, entry=100.0)
    ts.scaled(full)                # seeds _full = 3
    runner = _FakePos(qty=2, entry=100.0)
    assert ts.scaled(runner)
