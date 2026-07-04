"""EquityPosition.close in all four quadrants (long/short x profit/loss)."""
import pytest

from engine.equity.costs import EquityCostModel
from engine.equity.model import EquityOrder
from engine.equity.position import EquityPosition

M = EquityCostModel()


def test_order_carries_direction_and_capital():
    o = EquityOrder(direction="short", capital=100_000.0)
    assert o.direction == "short" and o.capital == 100_000.0


def test_long_profit():
    p = EquityPosition(direction="long", entry=1750.0, qty=57)
    t = p.close(1760.0, M, reason="target")
    assert t.side == "LONG" and t.strike == 0 and t.qty == 57
    assert t.entry == 1750.0 and t.exit == 1760.0
    assert t.gross == pytest.approx((1760.0 - 1750.0) * 57)          # +570
    expected_cost = M.total(buy_value=1750.0 * 57, sell_value=1760.0 * 57)
    assert t.cost == pytest.approx(expected_cost)
    assert t.net == pytest.approx(t.gross - t.cost)
    assert t.reason == "target"


def test_long_loss():
    p = EquityPosition(direction="long", entry=1750.0, qty=57)
    t = p.close(1740.0, M)
    assert t.gross == pytest.approx(-570.0)


def test_short_profit():
    # Short: sell at entry, buy back at exit; buy_value uses the EXIT leg.
    p = EquityPosition(direction="short", entry=1750.0, qty=57)
    t = p.close(1740.0, M, reason="target")
    assert t.side == "SHORT"
    assert t.gross == pytest.approx((1750.0 - 1740.0) * 57)          # +570
    expected_cost = M.total(buy_value=1740.0 * 57, sell_value=1750.0 * 57)
    assert t.cost == pytest.approx(expected_cost)
    assert t.net == pytest.approx(t.gross - t.cost)


def test_short_loss():
    p = EquityPosition(direction="short", entry=1750.0, qty=57)
    t = p.close(1760.0, M)
    assert t.gross == pytest.approx(-570.0)
