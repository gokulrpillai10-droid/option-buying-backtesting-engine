"""Tests for BarContext -- the only view a strategy gets.

It is constructed with the full day's forward series but truncates to <= now on
construction, so a strategy physically cannot read future bars through it.
"""
from datetime import datetime, time

import pandas as pd
import pytest

from engine.data.chain import Chain
from engine.strategy.base import BarContext


def fwd_series():
    idx = [datetime(2024, 1, 1, 9, 15 + i) for i in range(6)]   # 09:15..09:20
    vals = [20000.0, 20010.0, 20025.0, 20015.0, 20040.0, 20050.0]
    return pd.Series(vals, index=pd.DatetimeIndex(idx))


def test_forward_is_the_current_minute(simple_chain_df):
    ctx = BarContext(now=datetime(2024, 1, 1, 9, 18),
                     chain=Chain(simple_chain_df), forward=fwd_series())
    assert ctx.forward == pytest.approx(20015.0)
    assert ctx.now == datetime(2024, 1, 1, 9, 18)


def test_future_bars_are_not_visible(simple_chain_df):
    # at 09:17 the high so far is 20025 (09:17), NOT the later 20050 (09:20)
    ctx = BarContext(now=datetime(2024, 1, 1, 9, 17),
                     chain=Chain(simple_chain_df), forward=fwd_series())
    hi, lo = ctx.opening_range(time(15, 29))
    assert hi == pytest.approx(20025.0)
    assert lo == pytest.approx(20000.0)


def test_opening_range_respects_end_time(simple_chain_df):
    ctx = BarContext(now=datetime(2024, 1, 1, 9, 20),
                     chain=Chain(simple_chain_df), forward=fwd_series())
    # opening range 09:15-09:16 only -> high 20010, low 20000
    hi, lo = ctx.opening_range(time(9, 16))
    assert hi == pytest.approx(20010.0)
    assert lo == pytest.approx(20000.0)


def test_atm_strike_is_nearest_traded_to_forward(simple_chain_df):
    # forward 20010 at 09:16 -> nearest of {19900,20000,20100} is 20000
    ctx = BarContext(now=datetime(2024, 1, 1, 9, 16),
                     chain=Chain(simple_chain_df), forward=fwd_series())
    assert ctx.atm_strike("CALL") == 20000


def test_atm_skips_untraded_strikes(simple_chain_df):
    df = simple_chain_df.copy()
    at916 = df["DateTime"] == datetime(2024, 1, 1, 9, 16)
    df.loc[at916 & (df["StrikePrice"] == 20000), "CallIsTradedStrike"] = False
    ctx = BarContext(now=datetime(2024, 1, 1, 9, 16),
                     chain=Chain(df), forward=fwd_series())
    # 20000 untraded -> next nearest traded is 20100 (|20100-20010|=90 < |19900-20010|=110)
    assert ctx.atm_strike("CALL") == 20100
