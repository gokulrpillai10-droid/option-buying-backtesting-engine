"""Tests for the naked-short guard in Backtest._open_spread.

The guard fires (ValueError) when any option side's net qty goes negative —
i.e. more contracts sold than bought on that side. It fires BEFORE any chain
access, so raises-tests can pass chain=None without touching real data.

For passes-guard tests we need a minimal chain stub so _open_spread can
continue past the guard; an empty snapshot (no strikes) makes _opt_price
return None and _open_spread return None (all-or-none) — that's fine, we
only care that no ValueError was raised.
"""
from datetime import date

import pytest

from engine.backtest import Backtest, Knobs
from engine.execution.costs import CostModel
from engine.execution.model import Order


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

class _EmptySnap:
    def strikes(self):
        return set()


class _EmptyChain:
    """Chain that has no priced strikes — makes _opt_price return None."""
    def snapshot_at(self, dt):
        return _EmptySnap()


class _FakeMarket:
    """Market stub; no lot_size() so Backtest falls back to lot_size_for(day)."""
    pass


def _bt():
    return Backtest(_FakeMarket(), CostModel(), Knobs())


_CHAIN = _EmptyChain()
_DT = None          # never reached on raises-tests
_EXPIRY = None
_DAY = None
_SLIP = 0.0


# ---------------------------------------------------------------------------
# Guard fires: naked short raises ValueError
# ---------------------------------------------------------------------------

def test_naked_call_sell_raises():
    orders = [Order(side="CALL", strike=24000, qty=75, action="sell")]
    with pytest.raises(ValueError, match="naked short"):
        _bt()._open_spread(orders, None, _DT, _EXPIRY, _DAY, _SLIP)


def test_naked_put_sell_raises():
    orders = [Order(side="PUT", strike=23500, qty=75, action="sell")]
    with pytest.raises(ValueError, match="naked short"):
        _bt()._open_spread(orders, None, _DT, _EXPIRY, _DAY, _SLIP)


def test_oversold_call_raises():
    # buy 1 lot, sell 2 lots → net CALL = -75
    orders = [
        Order(side="CALL", strike=24000, qty=75, action="buy"),
        Order(side="CALL", strike=24500, qty=150, action="sell"),
    ]
    with pytest.raises(ValueError, match="naked short"):
        _bt()._open_spread(orders, None, _DT, _EXPIRY, _DAY, _SLIP)


def test_covered_call_but_naked_put_raises():
    # CALL side is balanced; PUT side is naked → guard still fires
    orders = [
        Order(side="CALL", strike=24000, qty=75, action="buy"),
        Order(side="CALL", strike=24500, qty=75, action="sell"),   # covered
        Order(side="PUT",  strike=23500, qty=75, action="sell"),   # naked
    ]
    with pytest.raises(ValueError, match="naked short"):
        _bt()._open_spread(orders, None, _DT, _EXPIRY, _DAY, _SLIP)


def test_error_message_contains_net():
    orders = [Order(side="CALL", strike=24000, qty=75, action="sell")]
    with pytest.raises(ValueError, match="net"):
        _bt()._open_spread(orders, None, _DT, _EXPIRY, _DAY, _SLIP)


# ---------------------------------------------------------------------------
# Guard passes: covered / balanced spreads do not raise
# ---------------------------------------------------------------------------

def test_buy_only_passes_guard():
    orders = [Order(side="CALL", strike=24000, qty=75, action="buy")]
    # returns None (no price data) but must NOT raise
    result = _bt()._open_spread(orders, _CHAIN, _DT, _EXPIRY, _DAY, _SLIP)
    assert result is None


def test_call_bull_spread_equal_qty_passes_guard():
    # buy ATM call + sell OTM call (1:1) → net CALL = 0 → covered
    orders = [
        Order(side="CALL", strike=24000, qty=75, action="buy"),
        Order(side="CALL", strike=24500, qty=75, action="sell"),
    ]
    result = _bt()._open_spread(orders, _CHAIN, _DT, _EXPIRY, _DAY, _SLIP)
    assert result is None


def test_more_bought_than_sold_passes_guard():
    # buy 2 lots, sell 1 → net CALL = +75 → fine
    orders = [
        Order(side="CALL", strike=24000, qty=150, action="buy"),
        Order(side="CALL", strike=24500, qty=75, action="sell"),
    ]
    result = _bt()._open_spread(orders, _CHAIN, _DT, _EXPIRY, _DAY, _SLIP)
    assert result is None


def test_independent_call_and_put_sides_both_covered():
    # iron-condor-like: buy + sell on each side independently balanced
    orders = [
        Order(side="CALL", strike=24500, qty=75, action="buy"),
        Order(side="CALL", strike=25000, qty=75, action="sell"),
        Order(side="PUT",  strike=23500, qty=75, action="buy"),
        Order(side="PUT",  strike=23000, qty=75, action="sell"),
    ]
    result = _bt()._open_spread(orders, _CHAIN, _DT, _EXPIRY, _DAY, _SLIP)
    assert result is None


# ---------------------------------------------------------------------------
# Qty resolution
# ---------------------------------------------------------------------------

def test_guard_uses_explicit_qty_not_lots():
    # explicit qty=75 on the sell, no buy → net=-75, should raise regardless of lots
    orders = [Order(side="CALL", strike=24000, qty=75, lots=1, action="sell")]
    with pytest.raises(ValueError, match="naked short"):
        _bt()._open_spread(orders, None, _DT, _EXPIRY, _DAY, _SLIP)


def test_guard_uses_lots_when_qty_is_none():
    # qty=None → engine resolves via lot_size_for(day); still naked → must raise
    day = date(2024, 6, 1)   # lot=25 on this date
    orders = [Order(side="PUT", strike=23000, qty=None, lots=1, action="sell")]
    with pytest.raises(ValueError, match="naked short"):
        _bt()._open_spread(orders, None, None, day, day, _SLIP)
