"""SpreadPosition: a two-leg vertical (long + short), combined P&L and costed close.

A bull call spread = buy ATM call + sell higher call. Combined unrealized P&L =
long leg (price-entry)*qty + short leg (entry-price)*qty. The short leg caps upside
and offsets the long leg's cost/decay -- the whole point of a debit spread.
"""
import pytest

from engine.execution.costs import CostModel
from engine.position.manager import SpreadLeg, SpreadPosition


def _bull_call():
    # debit = (150-50)*75 = 7500 ; 2 lots of 75 here = qty 75 for simplicity
    return SpreadPosition(legs=(
        SpreadLeg(action="buy", side="CALL", strike=20000, entry=150.0, qty=75),
        SpreadLeg(action="sell", side="CALL", strike=20300, entry=50.0, qty=75),
    ))


def test_debit_is_long_minus_short_premium():
    assert _bull_call().debit() == pytest.approx((150 - 50) * 75)


def test_unrealized_combines_both_legs():
    sp = _bull_call()
    # underlying up: long 150->250 (+100), short 50->120 (-70 to us) => net +30/unit
    assert sp.unrealized([250.0, 120.0]) == pytest.approx((100 - 70) * 75)
    # underlying down: long 150->60 (-90), short 50->10 (+40 to us) => net -50/unit
    assert sp.unrealized([60.0, 10.0]) == pytest.approx((-90 + 40) * 75)


def test_close_costed_combined_trade():
    sp = _bull_call()
    t = sp.close([250.0, 120.0], CostModel(), slip=0.0, reason="target")
    assert t.side == "SPREAD" and t.reason == "target" and t.qty == 75
    assert t.gross == pytest.approx((100 - 70) * 75)          # +2250 gross
    assert t.entry == pytest.approx(100.0)                    # net debit per unit
    assert t.net == pytest.approx(t.gross - t.cost)
    assert t.cost > 0                                         # 4 orders + STT on sells


def test_close_applies_adverse_slip_per_leg():
    sp = _bull_call()
    # long sells DOWN (1-slip), short buys-to-close UP (1+slip) -- both hurt us
    t = sp.close([250.0, 120.0], CostModel(), slip=0.01, reason="t")
    long_x = 250 * 0.99
    short_x = 120 * 1.01
    exp_gross = (long_x - 150) * 75 + (50 - short_x) * 75
    assert t.gross == pytest.approx(exp_gross)
