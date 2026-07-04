"""Exit value type + level-exit resolution with honest gap-through.

manage() returns an Exit. Exit.market() fills at the next 1-min open (handled by the
runner). Exit.at(level, trigger) is a resting order resolved against the option bar:
  - 'fall' (stop, sell when price drops to level): fills at level if touched intrabar;
    if the bar OPENS already below level (gap-down), fills at the worse open.
  - 'rise' (target, sell when price rises to level): fills at level if touched intrabar;
    if the bar OPENS above level (gap-up), fills at the better open (realistic resting fill).
Intrabar touch -> level; gap -> the bar open. 1-min granularity caps fidelity.
"""
from engine.strategy.exit import Exit, resolve_level_exit


def test_bracket_target_hit():
    br = Exit.bracket(stop=70, target=130)
    fill, reason, frac = br.resolve(open_=100, high=132, low=99)
    assert fill == 130.0 and reason == "target" and frac == 1.0


def test_bracket_stop_hit():
    br = Exit.bracket(stop=70, target=130)
    fill, reason, frac = br.resolve(open_=100, high=101, low=68)
    assert fill == 70.0 and reason == "stop"


def test_bracket_stop_takes_priority_when_both_in_bar():
    br = Exit.bracket(stop=70, target=130)
    fill, reason, frac = br.resolve(open_=100, high=140, low=60)   # both touched -> stop first
    assert reason == "stop"


def test_bracket_no_hit():
    br = Exit.bracket(stop=70, target=130)
    fill, reason, frac = br.resolve(open_=100, high=120, low=90)
    assert fill is None and reason is None


def test_bracket_per_side_fractions():
    # stop closes the whole position; target books only a fraction (scale-out at target)
    br = Exit.bracket(stop=70, target=130, stop_fraction=1.0, target_fraction=0.5)
    _, reason, frac = br.resolve(open_=100, high=132, low=99)      # target hit
    assert reason == "target" and frac == 0.5
    _, reason, frac = br.resolve(open_=100, high=101, low=68)      # stop hit
    assert reason == "stop" and frac == 1.0


def test_exit_constructors():
    m = Exit.market("eod")
    assert m.kind == "market" and m.reason == "eod"
    a = Exit.at(70.0, "fall", "stop")
    assert a.kind == "level" and a.level == 70.0 and a.trigger == "fall" and a.reason == "stop"


def test_stop_intrabar_fills_at_level():
    fill, hit = resolve_level_exit(Exit.at(70, "fall"), open_=100, high=101, low=68)
    assert hit and fill == 70.0


def test_stop_gap_down_fills_at_open():
    fill, hit = resolve_level_exit(Exit.at(70, "fall"), open_=65, high=66, low=60)
    assert hit and fill == 65.0          # opened past the stop -> worse open


def test_stop_not_hit():
    fill, hit = resolve_level_exit(Exit.at(70, "fall"), open_=100, high=102, low=95)
    assert not hit and fill is None


def test_target_intrabar_fills_at_level():
    fill, hit = resolve_level_exit(Exit.at(130, "rise"), open_=100, high=132, low=99)
    assert hit and fill == 130.0


def test_target_gap_up_fills_at_open():
    fill, hit = resolve_level_exit(Exit.at(130, "rise"), open_=140, high=145, low=139)
    assert hit and fill == 140.0         # gapped above target -> better open


def test_target_not_hit():
    fill, hit = resolve_level_exit(Exit.at(130, "rise"), open_=100, high=120, low=99)
    assert not hit and fill is None
