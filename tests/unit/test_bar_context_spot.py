"""BarContext exposes spot (signals) while keeping the chain (fills/strikes).

This is the 'accurate combination' wiring: a strategy reads ctx.spot / ctx.spot_bar
/ ctx.spot_history for triggers (real index), and ctx.atm_strike / ctx.snapshot for
fills (Data-v3 chain). Spot access is look-ahead-safe (only <= now).
"""
from datetime import datetime

import pandas as pd
import pytest

from engine.data.chain import Chain
from engine.data.spot import SpotSeries
from engine.strategy.base import BarContext


def spot_df():
    rows = [
        ("2024-01-01 09:15", 20000.0, 20030.0, 19990.0, 20005.0),
        ("2024-01-01 09:16", 20005.0, 20040.0, 20000.0, 20010.0),
        ("2024-01-01 09:17", 20010.0, 20050.0, 20005.0, 20020.0),
    ]
    return pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]).assign(
        DateTime=lambda d: pd.to_datetime(d["DateTime"]))


def ctx_at(now, simple_chain_df):
    return BarContext(now=now, chain=Chain(simple_chain_df), spot=SpotSeries(spot_df()))


def test_spot_is_current_bar_close(simple_chain_df):
    ctx = ctx_at(datetime(2024, 1, 1, 9, 16), simple_chain_df)
    assert ctx.spot == 20010.0


def test_spot_bar_is_current_ohlc(simple_chain_df):
    ctx = ctx_at(datetime(2024, 1, 1, 9, 16), simple_chain_df)
    assert ctx.spot_bar.high == 20040.0 and ctx.spot_bar.low == 20000.0


def test_spot_history_excludes_future(simple_chain_df):
    ctx = ctx_at(datetime(2024, 1, 1, 9, 16), simple_chain_df)
    assert [b.close for b in ctx.spot_history()] == [20005.0, 20010.0]


def test_atm_strike_uses_spot_not_forward(simple_chain_df):
    # spot 20010 -> nearest traded chain strike is 20000 (fills come from the chain)
    ctx = ctx_at(datetime(2024, 1, 1, 9, 16), simple_chain_df)
    assert ctx.atm_strike("CALL") == 20000
