"""Golden end-to-end: the ACCURATE COMBINATION.

Signals come from the real spot series; fills come from the Data-v3 chain. The
runner iterates spot bars, the strategy decides on ctx.spot, and the execution
model fills the chosen strike at the option's t+1 open -- spot says WHEN, the
chain says AT WHAT PRICE.
"""
from datetime import datetime, time

import pandas as pd
import pytest

from tests.conftest import make_row, CHAIN_COLUMNS

from engine.data.chain import Chain
from engine.data.spot import SpotSeries
from engine.execution.costs import CostModel
from engine.execution.model import ExecutionModel, Order
from engine.strategy.base import Strategy
from engine.runner import Runner


def day_df():
    bars = {15: (98, 99, 97, 98), 16: (100, 105, 99, 104), 17: (104, 120, 103, 118),
            18: (118, 125, 115, 122), 19: (122, 128, 119, 126), 20: (126, 132, 124, 130)}
    rows = []
    for m, (o, h, l, c) in bars.items():
        rows.append(make_row(datetime(2024, 1, 1, 9, m), 20000, forward=20000.0,
                             call_open=o, call_high=h, call_low=l, call_close=c))
    return pd.DataFrame(rows, columns=CHAIN_COLUMNS)


def spot_series():
    rows = [(datetime(2024, 1, 1, 9, m), 20000.0, 20000.0, 20000.0, 20000.0) for m in range(15, 21)]
    return SpotSeries(pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]))


class BuyCallOnFirstSpotBar(Strategy):
    def on_bar(self, ctx):
        if ctx.now.time() == time(9, 15) and ctx.spot >= 20000.0:   # decision uses SPOT
            return [Order(side="CALL", strike=ctx.atm_strike("CALL"), qty=65)]
        return []


def test_spot_signal_fills_from_chain():
    chain = Chain(day_df())
    em = ExecutionModel(chain, slippage_pct=0.0)
    runner = Runner(chain=chain, spot=spot_series(), execution=em, cost_model=CostModel(),
                    square_off=time(15, 15), stop_loss_pct=0.30, trailing_pct=None)
    res = runner.run(BuyCallOnFirstSpotBar())
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.strike == 20000 and t.entry == pytest.approx(100.0)   # 09:16 chain open
    assert t.exit == pytest.approx(130.0) and t.reason == "time_stop"
