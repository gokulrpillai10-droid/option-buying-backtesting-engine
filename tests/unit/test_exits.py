"""Tests for per-minute exit resolution (stop / trailing / time-stop).

Returns the exit LEVEL and a reason; slippage and costs are applied later by
the execution model and position. Pure logic over a sequence of option bars,
each ``(high, low, close)``.
"""
import pytest

from engine.position.exits import resolve_exit


def test_hard_stop_when_low_breaches_stop_level():
    # entry 100, 30% stop -> level 70 ; no trailing
    bars = [(105, 95, 100), (80, 68, 72)]
    price, reason = resolve_exit(entry=100.0, bars=bars,
                                 stop_loss_pct=0.30, trailing_pct=None)
    assert price == pytest.approx(70.0)
    assert reason == "stop_loss"


def test_trailing_stop_binds_above_hard_stop():
    # peak reaches 150 -> trailing 25% level 112.5 ; bar 4 low 108 breaches it.
    # earlier lows stay above their (lower-peak) trailing levels so they don't fire
    bars = [(140, 120, 135), (136, 120, 134), (150, 130, 148), (150, 108, 110)]
    price, reason = resolve_exit(entry=100.0, bars=bars,
                                 stop_loss_pct=0.30, trailing_pct=0.25)
    assert price == pytest.approx(112.5)
    assert reason == "trailing_stop"


def test_time_stop_at_last_close_when_nothing_triggers():
    bars = [(105, 95, 100), (106, 99, 104)]
    price, reason = resolve_exit(entry=100.0, bars=bars,
                                 stop_loss_pct=0.30, trailing_pct=0.25)
    assert price == pytest.approx(104.0)   # last bar close
    assert reason == "time_stop"


def test_stop_takes_first_bar_that_breaches():
    # both bars breach the 70 stop; the FIRST one wins (chronological)
    bars = [(90, 65, 66), (80, 60, 61)]
    price, reason = resolve_exit(entry=100.0, bars=bars,
                                 stop_loss_pct=0.30, trailing_pct=None)
    assert price == pytest.approx(70.0)
    assert reason == "stop_loss"
