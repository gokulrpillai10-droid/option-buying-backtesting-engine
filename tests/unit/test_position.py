"""Tests for an open position closing into a fully-costed Trade."""
import pytest

from engine.execution.costs import CostModel
from engine.position.manager import Position, Trade


def test_close_computes_gross_cost_and_net():
    pos = Position(side="CALL", strike=20000, entry=100.0, qty=65)
    trade = pos.close(exit=120.0, cost_model=CostModel())
    assert isinstance(trade, Trade)
    assert trade.gross == pytest.approx(1300.0)          # (120-100) * 65
    assert trade.cost == pytest.approx(61.1228362)        # buy 6500 / sell 7800 / 2 orders
    assert trade.net == pytest.approx(1300.0 - 61.1228362)


def test_losing_trade_has_negative_net():
    pos = Position(side="CALL", strike=20000, entry=100.0, qty=65)
    trade = pos.close(exit=80.0, cost_model=CostModel())
    assert trade.gross == pytest.approx(-1300.0)
    assert trade.net < trade.gross                        # costs make it worse


def test_put_pnl_uses_same_long_premium_formula():
    # buying a put still profits when its premium rises; no direction flip
    pos = Position(side="PUT", strike=20000, entry=100.0, qty=65)
    trade = pos.close(exit=120.0, cost_model=CostModel())
    assert trade.gross == pytest.approx(1300.0)
    assert trade.side == "PUT"


def test_partial_close_uses_given_qty():
    pos = Position(side="CALL", strike=20000, entry=100.0, qty=130)
    trade = pos.close(exit=120.0, cost_model=CostModel(), qty=65)   # book half
    assert trade.qty == 65
    assert trade.gross == pytest.approx((120 - 100) * 65)


def test_trade_carries_entry_exit_and_identity():
    pos = Position(side="CALL", strike=20000, entry=100.0, qty=65)
    trade = pos.close(exit=120.0, cost_model=CostModel())
    assert trade.strike == 20000
    assert trade.qty == 65
    assert trade.entry == pytest.approx(100.0)
    assert trade.exit == pytest.approx(120.0)
