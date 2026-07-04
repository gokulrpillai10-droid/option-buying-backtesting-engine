"""Tests for the execution model -- the accuracy core.

Guarantees: fills never happen at the signal bar (always t+1 open), slippage is
applied adversely, and every order either fills or is rejected with a reason
(never silently dropped).
"""
from datetime import datetime

import pytest

from engine.data.chain import Chain
from engine.execution.model import ExecutionModel, Order, Fill, Rejection


SIG = datetime(2024, 1, 1, 9, 15)   # opening bar in the fixture
LAST = datetime(2024, 1, 1, 9, 16)  # last bar in the fixture


def test_buy_fills_at_next_bar_open_with_adverse_slippage(simple_chain_df):
    em = ExecutionModel(Chain(simple_chain_df), slippage_pct=0.01)
    res = em.enter(Order(side="CALL", strike=20000, qty=65), SIG)
    assert isinstance(res, Fill)
    # 09:16 open of the 20000 call is 110.0 ; buy slippage pays UP 1%
    assert res.price == pytest.approx(110.0 * 1.01)
    assert res.strike == 20000 and res.side == "CALL" and res.qty == 65


def test_no_following_bar_is_rejected(simple_chain_df):
    em = ExecutionModel(Chain(simple_chain_df), slippage_pct=0.01)
    res = em.enter(Order(side="CALL", strike=20000, qty=65), LAST)
    assert isinstance(res, Rejection)
    assert res.reason == "no_next_bar"


def test_untraded_strike_is_rejected(simple_chain_df):
    df = simple_chain_df.copy()
    # mark the 20000 call untradeable at the signal bar
    mask = (df["DateTime"] == SIG) & (df["StrikePrice"] == 20000)
    df.loc[mask, "CallIsTradedStrike"] = False
    em = ExecutionModel(Chain(df), slippage_pct=0.01)
    res = em.enter(Order(side="CALL", strike=20000, qty=65), SIG)
    assert isinstance(res, Rejection)
    assert res.reason == "not_tradeable"


def test_insufficient_volume_is_rejected(simple_chain_df):
    df = simple_chain_df.copy()
    mask = (df["DateTime"] == SIG) & (df["StrikePrice"] == 20000)
    df.loc[mask, "CallVolume"] = 10
    em = ExecutionModel(Chain(df), slippage_pct=0.01, min_volume=100)
    res = em.enter(Order(side="CALL", strike=20000, qty=65), SIG)
    assert isinstance(res, Rejection)
    assert res.reason == "insufficient_liquidity"


def test_insufficient_oi_is_rejected(simple_chain_df):
    df = simple_chain_df.copy()
    mask = (df["DateTime"] == SIG) & (df["StrikePrice"] == 20000)
    df.loc[mask, "CallOI"] = 50
    em = ExecutionModel(Chain(df), slippage_pct=0.01, min_oi=1000)
    res = em.enter(Order(side="CALL", strike=20000, qty=65), SIG)
    assert isinstance(res, Rejection)
    assert res.reason == "insufficient_liquidity"


def test_exit_sells_at_given_price_with_adverse_slippage(simple_chain_df):
    em = ExecutionModel(Chain(simple_chain_df), slippage_pct=0.01)
    # exits are priced at a level the position manager hands in (stop/target/EOD),
    # not re-read from the chain; selling slips DOWN 1%
    px = em.exit_price(100.0)
    assert px == pytest.approx(99.0)
