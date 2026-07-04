"""Golden end-to-end test: data -> strategy -> execution -> position.

A hand-constructed day with one ATM call strike. The strategy signals once at
09:15; the runner must fill at the 09:16 open (t+1), hold to a time-stop at the
last bar's close, and book a fully-costed trade whose numbers are computed by
hand here.
"""
from datetime import datetime, time

import pandas as pd
import pytest

from tests.conftest import make_row, CHAIN_COLUMNS

from engine.data.chain import Chain
from engine.execution.costs import CostModel
from engine.execution.model import ExecutionModel, Order
from engine.strategy.base import Strategy
from engine.runner import Runner


def day_df():
    """One strike (20000), forward flat at 20000, six minutes 09:15..09:20.
    Call O/H/L/C chosen so nothing breaches the 30% stop -> time-stop at 130."""
    bars = {
        # minute: (open, high, low, close)
        15: (98, 99, 97, 98),
        16: (100, 105, 99, 104),    # entry fill = this open (100)
        17: (104, 120, 103, 118),
        18: (118, 125, 115, 122),
        19: (122, 128, 119, 126),
        20: (126, 132, 124, 130),   # last bar -> time-stop close
    }
    rows = []
    for m, (o, h, l, c) in bars.items():
        dt = datetime(2024, 1, 1, 9, m)
        rows.append(make_row(dt, 20000, forward=20000.0,
                             call_open=o, call_high=h, call_low=l, call_close=c))
    return pd.DataFrame(rows, columns=CHAIN_COLUMNS)


def forward_series():
    idx = [datetime(2024, 1, 1, 9, m) for m in range(15, 21)]
    return pd.Series([20000.0] * 6, index=pd.DatetimeIndex(idx))


class SignalAtOpen(Strategy):
    """Buy one ATM call lot at 09:15, never again."""
    def on_bar(self, ctx):
        if ctx.now.time() == time(9, 15):
            return [Order(side="CALL", strike=ctx.atm_strike("CALL"), qty=65)]
        return []


def make_runner(df):
    chain = Chain(df)
    em = ExecutionModel(chain, slippage_pct=0.0)
    return Runner(chain=chain, forward=forward_series(), execution=em,
                  cost_model=CostModel(), square_off=time(15, 15),
                  stop_loss_pct=0.30, trailing_pct=None)


def test_one_costed_trade_filled_at_next_open_and_time_stopped():
    res = make_runner(day_df()).run(SignalAtOpen())
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.side == "CALL" and t.strike == 20000 and t.qty == 65
    assert t.entry == pytest.approx(100.0)      # 09:16 open, no slippage
    assert t.exit == pytest.approx(130.0)       # time-stop at last close
    assert t.reason == "time_stop"
    assert t.gross == pytest.approx(1950.0)     # (130-100)*65
    # net uses the independently golden-tested cost model (buy 6500 / sell 8450)
    assert t.net == pytest.approx(1950.0 - CostModel().total(6500.0, 8450.0, 2))


def test_no_trade_and_a_rejection_when_signal_has_no_next_bar():
    class SignalAtLastBar(Strategy):
        def on_bar(self, ctx):
            if ctx.now.time() == time(9, 20):   # last bar -> nothing to fill into
                return [Order(side="CALL", strike=ctx.atm_strike("CALL"), qty=65)]
            return []

    res = make_runner(day_df()).run(SignalAtLastBar())
    assert res.trades == []
    assert any(r.reason == "no_next_bar" for r in res.rejections)
