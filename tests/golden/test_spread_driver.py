"""Golden: the Backtest driver opens, manages, and closes a 2-leg vertical spread.

A strategy returns TWO orders (buy + sell) -> the driver opens a SpreadPosition, prices
both legs each bar, and on a combined-P&L exit closes both legs into one SPREAD Trade.
"""
from datetime import date, datetime

import pandas as pd
import pytest

from tests.conftest import make_row, CHAIN_COLUMNS
from engine.data.chain import Chain
from engine.data.spot import SpotSeries
from engine.execution.costs import CostModel
from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit
from engine.instruments import lot_size_for
from engine.backtest import Backtest


def _spot(d):
    rows = [(datetime(d.year, d.month, d.day, h, m), 100.0, 100.0, 100.0, 100.0)
            for h, m in ((9, 15), (9, 20), (9, 25))]
    return SpotSeries(pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]))


def _chain(d):
    rows = []
    # (minute, long-call price, short-call price)
    for (h, m), lp, sp in ((( 9, 20), 150, 50), ((9, 25), 250, 120), ((9, 30), 260, 130)):
        rows.append(make_row(datetime(d.year, d.month, d.day, h, m), 20000,
                             call_open=lp, call_close=lp))
        rows.append(make_row(datetime(d.year, d.month, d.day, h, m), 20300,
                             call_open=sp, call_close=sp))
    return Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))


class FakeMarket:
    def __init__(self, spot, batches): self._spot, self._batches = spot, batches
    def spot(self): return self._spot
    def stream(self, start, end): return iter(self._batches)


class BuyCallSpread(Strategy):
    def on_day_start(self, ctx): self._in = False
    def on_bar(self, ctx):
        if not self._in:
            self._in = True
            return [Order(action="buy", side="CALL", strike=20000, lots=1),
                    Order(action="sell", side="CALL", strike=20300, lots=1)]
        return []
    def manage(self, ctx, pos):
        snap = ctx.snapshot()
        prices = [snap.call(lg.strike).close for lg in pos.legs]
        return Exit.market("target") if pos.unrealized(prices) >= 2000 else None


def test_spread_enter_manage_close_one_combined_trade():
    d = date(2024, 1, 2)
    res = Backtest(FakeMarket(_spot(d), [(date(2024, 1, 11), {d: _chain(d)})]),
                   CostModel()).run(BuyCallSpread(), d, d)
    assert len(res.trades) == 1
    t = res.trades[0]
    q = lot_size_for(d)                                  # 75
    assert t.side == "SPREAD" and t.reason == "target" and t.qty == q
    # exit at 09:30: long 150->260 (+110), short 50->130 (-80) => +30/unit
    assert t.gross == pytest.approx((110 - 80) * q)      # 2250
    assert t.net == pytest.approx(t.gross - t.cost) and t.cost > 0
