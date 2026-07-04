"""Golden: the multi-day Backtest driver, simplest path.

Drives a strategy across 2 days: enter on the first 5-min bar, hold, EOD square-off.
Pins the fill convention (signal on bar B -> fill at B+timeframe), date-keyed lot
size, and costs. manage()/Exit.at resolution + carry-forward are later increments.
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
from engine.instruments import lot_size_for
from engine.backtest import Backtest


def _spot_5min(days):
    rows = []
    for d in days:
        for hm in ((9, 15), (9, 20), (9, 25)):
            rows.append((datetime(d.year, d.month, d.day, *hm), 100.0, 100.0, 100.0, 100.0))
    return SpotSeries(pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]))


def _day_chain(d, entry_open, eod_close):
    # 1-min option bars at the fill minutes: 09:20 (entry), 09:25, 09:30 (EOD)
    rows = [
        make_row(datetime(d.year, d.month, d.day, 9, 20), 20000,
                 call_open=entry_open, call_close=entry_open + 5),
        make_row(datetime(d.year, d.month, d.day, 9, 25), 20000,
                 call_open=entry_open + 10, call_close=entry_open + 12),
        make_row(datetime(d.year, d.month, d.day, 9, 30), 20000,
                 call_open=eod_close - 2, call_close=eod_close),
    ]
    return Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))


class FakeMarket:
    def __init__(self, spot, batches):
        self._spot, self._batches = spot, batches

    def spot(self):
        return self._spot

    def stream(self, start, end):
        return iter(self._batches)


class BuyFirstBarHold(Strategy):
    def on_day_start(self, ctx):
        self._done = False

    def on_bar(self, ctx):
        if not self._done:
            self._done = True
            return [Order(side="CALL", strike=20000, lots=1)]
        return []
    # manage inherits default -> None (hold to EOD)


def test_two_day_enter_hold_eod_with_costs_and_lotsize():
    d1, d2 = date(2024, 1, 2), date(2024, 1, 3)
    spot = _spot_5min([d1, d2])
    batches = [(date(2024, 1, 11),
                {d1: _day_chain(d1, 100, 120), d2: _day_chain(d2, 200, 230)})]
    bt = Backtest(FakeMarket(spot, batches), CostModel())   # slippage default 0

    res = bt.run(BuyFirstBarHold(), d1, d2)
    assert len(res.trades) == 2
    t1, t2 = res.trades

    qty = lot_size_for(d1)          # 75 in 2024
    assert t1.qty == qty and t1.entry == 100.0 and t1.exit == 120.0
    assert t1.gross == pytest.approx((120 - 100) * qty)
    assert t1.net == pytest.approx(t1.gross - t1.cost)
    assert t2.entry == 200.0 and t2.exit == 230.0
    assert t2.gross == pytest.approx((230 - 200) * qty)


class BuyThenScaleOut(Strategy):
    def on_day_start(self, ctx):
        self._in = False
        self._n = 0

    def on_bar(self, ctx):
        if not self._in:
            self._in = True
            return [Order(side="CALL", strike=20000, lots=1)]
        return []

    def manage(self, ctx, pos):
        from engine.strategy.exit import Exit
        self._n += 1
        if self._n == 1:
            return Exit.market("book", fraction=0.5)   # book half
        return Exit.market("rest")                      # exit remainder


def test_partial_scale_out_books_half_then_rest():
    d1 = date(2024, 1, 2)
    spot = _spot_5min([d1])
    batches = [(date(2024, 1, 11), {d1: _day_chain(d1, 100, 120)})]
    res = Backtest(FakeMarket(spot, batches), CostModel()).run(BuyThenScaleOut(), d1, d1)
    assert len(res.trades) == 2
    full = lot_size_for(d1)                              # 75
    assert res.trades[0].reason == "book" and res.trades[0].qty == pytest.approx(full / 2)
    assert res.trades[1].reason == "rest" and res.trades[1].qty == pytest.approx(full / 2)


class BuyThenPremiumTrail(Strategy):
    def on_day_start(self, ctx):
        self._in = False

    def on_bar(self, ctx):
        if not self._in:
            self._in = True
            return [Order(side="CALL", strike=20000, lots=1)]
        return []

    def manage(self, ctx, pos):
        from engine.strategy.exit import Exit
        return Exit.trail(20, "ptrail")          # trail the option premium by 20


def test_premium_trail_exits_20_off_the_peak():
    d1 = date(2024, 1, 2)
    spot = SpotSeries(pd.DataFrame(
        [(datetime(2024, 1, 1 + 1, 9, m), 100.0, 100.0, 100.0, 100.0) for m in (15, 20, 25, 30)],
        columns=["DateTime", "Open", "High", "Low", "Close"]))
    bars = {20: (100, 100, 100, 100),       # entry fill (peak=100)
            25: (110, 130, 120, 128),       # premium peaks at 130
            30: (118, 125, 105, 108),       # low 105 <= peak130-20=110 -> exit at 110
            35: (108, 109, 107, 108)}
    rows = [make_row(datetime(2024, 1, 2, 9, m), 20000, call_open=o, call_high=h,
                     call_low=l, call_close=c) for m, (o, h, l, c) in bars.items()]
    chain = Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))
    res = Backtest(FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})]),
                   CostModel()).run(BuyThenPremiumTrail(), d1, d1)
    assert len(res.trades) == 1
    assert res.trades[0].reason == "ptrail" and res.trades[0].exit == pytest.approx(110.0)


class BuyThenLevelTarget(Strategy):
    def on_day_start(self, ctx):
        self._in = False
        self._full = None

    def on_bar(self, ctx):
        if not self._in:
            self._in = True
            return [Order(side="CALL", strike=20000, lots=1)]
        return []

    def manage(self, ctx, pos):
        from engine.strategy.exit import Exit
        if self._full is None:
            self._full = pos.qty
        if pos.qty == self._full:                       # stage 1: book 1/3 at premium +25
            return Exit.at(125, "rise", "book25", fraction=1 / 3)
        return Exit.market("rest")                       # stage 2: dump remainder


def test_level_target_books_fraction_intrabar_then_remainder():
    d1 = date(2024, 1, 2)
    spot = SpotSeries(pd.DataFrame(
        [(datetime(2024, 1, 2, 9, m), 100.0, 100.0, 100.0, 100.0) for m in (15, 20, 25, 30)],
        columns=["DateTime", "Open", "High", "Low", "Close"]))
    bars = {20: (100, 100, 100, 100),       # entry fill at 100
            25: (110, 130, 108, 120),       # high 130 >= 125 target -> book 1/3 at 125
            30: (120, 122, 118, 119)}        # remainder dumped next bar
    rows = [make_row(datetime(2024, 1, 2, 9, m), 20000, call_open=o, call_high=h,
                     call_low=l, call_close=c) for m, (o, h, l, c) in bars.items()]
    chain = Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))
    res = Backtest(FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})]),
                   CostModel()).run(BuyThenLevelTarget(), d1, d1)
    full = lot_size_for(d1)
    assert len(res.trades) == 2
    assert res.trades[0].reason == "book25" and res.trades[0].exit == pytest.approx(125.0)
    assert res.trades[0].qty == pytest.approx(full / 3)
    assert res.trades[1].qty == pytest.approx(full - full / 3)   # remainder


def _vol_day_chain(d, sig_vol):
    """Signal minute 09:15 carries the volume the floor inspects; 09:20 is the
    fill/EOD bar (always liquid so it never gates the exit)."""
    rows = [
        make_row(datetime(d.year, d.month, d.day, 9, 15), 20000,
                 call_open=100, call_vol=sig_vol),
        make_row(datetime(d.year, d.month, d.day, 9, 20), 20000,
                 call_open=100, call_close=120, call_vol=10**9),
    ]
    return Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))


def _vol_spot(d):
    return SpotSeries(pd.DataFrame(
        [(datetime(d.year, d.month, d.day, 9, m), 100.0, 100.0, 100.0, 100.0) for m in (15, 20)],
        columns=["DateTime", "Open", "High", "Low", "Close"]))


def test_volume_floor_blocks_thin_entry():
    # min_volume_mult=10, qty=75 -> require signal-bar volume >= 750. With 100, skip.
    from engine.backtest import Knobs
    d1 = date(2024, 1, 2)
    bt = Backtest(FakeMarket(_vol_spot(d1), [(date(2024, 1, 11), {d1: _vol_day_chain(d1, 100)})]),
                  CostModel(), Knobs(min_volume_mult=10))
    res = bt.run(BuyFirstBarHold(), d1, d1)
    assert len(res.trades) == 0       # thin signal-bar volume -> entry rejected, no re-entry


def test_volume_floor_allows_liquid_entry():
    # Same floor, but signal-bar volume 1000 >= 750 -> entry proceeds.
    from engine.backtest import Knobs
    d1 = date(2024, 1, 2)
    bt = Backtest(FakeMarket(_vol_spot(d1), [(date(2024, 1, 11), {d1: _vol_day_chain(d1, 1000)})]),
                  CostModel(), Knobs(min_volume_mult=10))
    res = bt.run(BuyFirstBarHold(), d1, d1)
    assert len(res.trades) == 1
    assert res.trades[0].entry == 100.0 and res.trades[0].exit == 120.0


def test_volume_floor_off_by_default():
    # Default knobs (min_volume_mult=0) -> no gating even on thin volume (backward compat).
    d1 = date(2024, 1, 2)
    bt = Backtest(FakeMarket(_vol_spot(d1), [(date(2024, 1, 11), {d1: _vol_day_chain(d1, 1)})]),
                  CostModel())
    res = bt.run(BuyFirstBarHold(), d1, d1)
    assert len(res.trades) == 1


def test_fill_price_close_knob_matches_g12_convention():
    # G12 priced fills at the option CLOSE at signal+5min, not the open (P1-2).
    from engine.backtest import Knobs
    d1 = date(2024, 1, 2)
    spot = _spot_5min([d1])
    batches = [(date(2024, 1, 11), {d1: _day_chain(d1, 100, 120)})]
    bt = Backtest(FakeMarket(spot, batches), CostModel(), Knobs(fill_price="close"))
    res = bt.run(BuyFirstBarHold(), d1, d1)
    # entry = CallClose at 09:20 = entry_open + 5 = 105 (not the 100 open)
    assert res.trades[0].entry == 105.0
