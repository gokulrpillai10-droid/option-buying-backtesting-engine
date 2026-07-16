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


class BuyThenArmedTrail(Strategy):
    def __init__(self, arm):
        self._arm = arm

    def on_day_start(self, ctx):
        self._in = False

    def on_bar(self, ctx):
        if not self._in:
            self._in = True
            return [Order(side="CALL", strike=20000, lots=1)]
        return []

    def manage(self, ctx, pos):
        from engine.strategy.exit import Exit
        return Exit.trail(20, "ptrail", arm=self._arm)


def test_armed_trail_dormant_until_peak_reaches_arm_level():
    # same tape as the plain-trail test (peak 130, drop to 105 = 25 off the peak):
    # arm 140 is never reached -> the 20pt trail must stay dormant -> EOD exit;
    # arm 125 IS reached intrabar -> fires exactly like the unarmed trail at 110
    d1 = date(2024, 1, 2)
    spot = SpotSeries(pd.DataFrame(
        [(datetime(2024, 1, 1 + 1, 9, m), 100.0, 100.0, 100.0, 100.0) for m in (15, 20, 25, 30)],
        columns=["DateTime", "Open", "High", "Low", "Close"]))
    bars = {20: (100, 100, 100, 100),
            25: (110, 130, 120, 128),
            30: (118, 125, 105, 108),
            35: (108, 109, 107, 108)}
    rows = [make_row(datetime(2024, 1, 2, 9, m), 20000, call_open=o, call_high=h,
                     call_low=l, call_close=c) for m, (o, h, l, c) in bars.items()]
    chain = Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))
    market = lambda: FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})])

    res = Backtest(market(), CostModel()).run(BuyThenArmedTrail(arm=140), d1, d1)
    assert len(res.trades) == 1 and res.trades[0].reason == "eod"

    res = Backtest(market(), CostModel()).run(BuyThenArmedTrail(arm=125), d1, d1)
    assert len(res.trades) == 1
    assert res.trades[0].reason == "ptrail" and res.trades[0].exit == pytest.approx(110.0)


class BuyScaleOutThenTrail(Strategy):
    """Half out at a premium level, then trail the remainder (ema5_itm's scale_trail shape)."""
    def on_day_start(self, ctx):
        self._in, self._init_qty = False, None

    def on_bar(self, ctx):
        if not self._in:
            self._in = True
            return [Order(side="CALL", strike=20000, lots=1)]
        return []

    def manage(self, ctx, pos):
        from engine.strategy.exit import Exit
        if self._init_qty is None:
            self._init_qty = pos.qty
        if pos.qty < self._init_qty:
            return Exit.trail(20, "ptrail")
        return Exit.at(125, "rise", "scale", fraction=0.5)


def test_scale_out_half_then_trail_remainder():
    # bar 25 hits the 125 scale level (half out at 125); the trail takes over
    # NEXT bar and its peak references highs from activation on (not the
    # scale-out bar's 130): bar 30 peak 125 -> stop 105, low 105 -> rest at 105
    d1 = date(2024, 1, 2)
    spot = SpotSeries(pd.DataFrame(
        [(datetime(2024, 1, 1 + 1, 9, m), 100.0, 100.0, 100.0, 100.0) for m in (15, 20, 25, 30)],
        columns=["DateTime", "Open", "High", "Low", "Close"]))
    bars = {20: (100, 100, 100, 100),
            25: (110, 130, 120, 128),
            30: (118, 125, 105, 108),
            35: (108, 109, 107, 108)}
    rows = [make_row(datetime(2024, 1, 2, 9, m), 20000, call_open=o, call_high=h,
                     call_low=l, call_close=c) for m, (o, h, l, c) in bars.items()]
    chain = Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))
    res = Backtest(FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})]),
                   CostModel()).run(BuyScaleOutThenTrail(), d1, d1)
    assert len(res.trades) == 2
    full = lot_size_for(d1)
    assert res.trades[0].reason == "scale" and res.trades[0].exit == pytest.approx(125.0)
    assert res.trades[0].qty == pytest.approx(full / 2)
    assert res.trades[1].reason == "ptrail" and res.trades[1].exit == pytest.approx(105.0)
    assert res.trades[1].qty == pytest.approx(full / 2)


class BuyBracketScaleThenTrail(Strategy):
    """Full-size premium stop bracketed with a half target, then trail the rest
    (ema5_itm's scale_trail + prem_sl shape)."""
    def __init__(self, stop):
        self._stop = stop

    def on_day_start(self, ctx):
        self._in, self._init_qty = False, None

    def on_bar(self, ctx):
        if not self._in:
            self._in = True
            return [Order(side="CALL", strike=20000, lots=1)]
        return []

    def manage(self, ctx, pos):
        from engine.strategy.exit import Exit
        if self._init_qty is None:
            self._init_qty = pos.qty
        if pos.qty < self._init_qty:
            return Exit.trail(20, "ptrail")
        return Exit.bracket(self._stop, 125, stop_reason="psl",
                            target_reason="scale", target_fraction=0.5)


def test_bracket_scale_out_then_trail_remainder():
    d1 = date(2024, 1, 2)
    spot = SpotSeries(pd.DataFrame(
        [(datetime(2024, 1, 1 + 1, 9, m), 100.0, 100.0, 100.0, 100.0) for m in (15, 20, 25, 30)],
        columns=["DateTime", "Open", "High", "Low", "Close"]))
    bars = {20: (100, 100, 100, 100),
            25: (110, 130, 120, 128),
            30: (118, 125, 105, 108),
            35: (108, 109, 107, 108)}
    rows = [make_row(datetime(2024, 1, 2, 9, m), 20000, call_open=o, call_high=h,
                     call_low=l, call_close=c) for m, (o, h, l, c) in bars.items()]
    chain = Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))
    market = lambda: FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})])
    full = lot_size_for(d1)

    # stop 88 never touched -> half out at 125, trail books the rest at 105
    # (same-bar stop-first priority is covered in unit test_exit.py)
    res = Backtest(market(), CostModel()).run(BuyBracketScaleThenTrail(stop=88), d1, d1)
    assert [t.reason for t in res.trades] == ["scale", "ptrail"]
    assert res.trades[0].exit == pytest.approx(125.0)
    assert res.trades[1].exit == pytest.approx(105.0)
    assert all(t.qty == pytest.approx(full / 2) for t in res.trades)


def test_bracket_stop_keeps_resting_after_partial_in_same_window():
    # 1-min chain bars inside one 5-min strategy bar: the target books half at
    # 125 on the 9:21 bar, then the STOP must still be live for the window's
    # remaining bars -- 9:22 dives through 88 and takes the rest out at 88
    d1 = date(2024, 1, 2)
    spot = SpotSeries(pd.DataFrame(
        [(datetime(2024, 1, 2, 9, m), 100.0, 100.0, 100.0, 100.0) for m in (15, 20, 25)],
        columns=["DateTime", "Open", "High", "Low", "Close"]))
    bars = {20: (100, 100, 100, 100),       # entry fill
            21: (110, 130, 120, 128),       # target 125 books half
            22: (95, 96, 80, 85),           # stop 88 takes the remainder
            25: (85, 86, 84, 85)}
    rows = [make_row(datetime(2024, 1, 2, 9, m), 20000, call_open=o, call_high=h,
                     call_low=l, call_close=c) for m, (o, h, l, c) in bars.items()]
    chain = Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))
    res = Backtest(FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})]),
                   CostModel()).run(BuyBracketScaleThenTrail(stop=88), d1, d1)
    full = lot_size_for(d1)
    assert [t.reason for t in res.trades] == ["scale", "psl"]
    assert res.trades[0].exit == pytest.approx(125.0)
    assert res.trades[1].exit == pytest.approx(88.0)
    assert all(t.qty == pytest.approx(full / 2) for t in res.trades)
    assert res.trades[0].exit_dt.minute == 21 and res.trades[1].exit_dt.minute == 22


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
