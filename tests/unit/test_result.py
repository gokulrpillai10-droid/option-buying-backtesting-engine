"""Result basic metrics — net / win% / PF / t-stat / tail / by-year.

Centralizes the stats the ~8 analysis scripts re-implemented inline (CQ-3 DRY).
"""
from datetime import date

import pytest

from engine.backtest import Result
from engine.position.manager import Trade


def _t(net, entry=100.0, qty=65, d=None):
    return Trade(side="CALL", strike=20000, qty=qty, entry=entry,
                 exit=entry + net / qty, gross=net, cost=0.0, net=net, date=d)


def _result():
    return Result(trades=[
        _t(100, d=date(2024, 1, 1)),
        _t(-50, d=date(2024, 1, 2)),
        _t(200, d=date(2025, 1, 1)),
    ])


def test_net_and_win_rate():
    r = _result()
    assert r.net() == pytest.approx(250.0)
    assert r.win_rate() == pytest.approx(2 / 3)


def test_profit_factor():
    # wins 100+200=300, losses 50 -> PF 6
    assert _result().profit_factor() == pytest.approx(6.0)


def test_tail_excl_top_winners():
    r = _result()
    assert r.tail_excl(1) == pytest.approx(50.0)     # drop +200
    assert r.tail_excl(2) == pytest.approx(-50.0)     # drop +200, +100


def test_by_year():
    assert _result().by_year() == {2024: pytest.approx(50.0), 2025: pytest.approx(200.0)}


def test_t_stat_is_finite_and_positive_here():
    t = _result().t_stat()
    assert t == t and t > 0          # not NaN, net-positive sample
