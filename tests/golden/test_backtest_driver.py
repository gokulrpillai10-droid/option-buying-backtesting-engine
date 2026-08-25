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
from engine.backtest import Backtest, Knobs


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


class LotMarket(FakeMarket):
    """FakeMarket that also exposes a per-expiry lot (the real MarketData contract),
    so the driver sizes by the EXPIRY it trades, not the calendar day."""
    def lot_size(self, expiry):
        return 25 if expiry == date(2024, 6, 6) else 999


def test_driver_sizes_by_traded_expiry_lot():
    d1 = date(2024, 6, 3)                                  # day sits in the 50-lot day-schedule
    spot = _spot_5min([d1])
    batches = [(date(2024, 6, 6), {d1: _day_chain(d1, 100, 120)})]
    res = Backtest(LotMarket(spot, batches), CostModel()).run(BuyFirstBarHold(), d1, d1)
    assert len(res.trades) == 1
    assert res.trades[0].qty == 25                         # per-expiry lot, not lot_size_for(day)


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


class BuyThenMarketExit(Strategy):
    def on_day_start(self, ctx):
        self._in, self._n = False, 0

    def on_bar(self, ctx):
        if not self._in:
            self._in = True
            return [Order(side="CALL", strike=20000, lots=1)]
        return []

    def manage(self, ctx, pos):
        from engine.strategy.exit import Exit
        return Exit.market("out")


def _slip_tape(entry_vol=5000, exit_vol=5000):
    d1 = date(2024, 1, 2)
    spot = _spot_5min([d1])
    bars = {15: dict(call_open=100.0, call_vol=entry_vol),                    # signal bar (volume gate)
            20: dict(call_open=100.0, call_vol=entry_vol),                    # flat entry bar
            25: dict(call_open=110.0, call_high=121.0, call_low=99.0,        # ranging exit bar
                     call_close=111.0, call_vol=exit_vol)}
    rows = [make_row(datetime(2024, 1, 2, 9, m), 20000, **kw) for m, kw in bars.items()]
    return d1, spot, Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))


def test_range_scaled_slippage_charges_fast_bars_more():
    # exit bar mid=(121+99)/2=110, range 22 -> extra slip = 0.05*22/110 = 0.01;
    # entry bar is flat (range 0) -> entry stays at the raw 100
    d1, spot, chain = _slip_tape()
    res = Backtest(FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})]), CostModel(),
                   Knobs(slippage_range_k=0.05)).run(BuyThenMarketExit(), d1, d1)
    assert res.trades[0].entry == pytest.approx(100.0)
    assert res.trades[0].exit == pytest.approx(110.0 * (1 - 0.01))


def test_thin_exit_penalty_multiplies_slip_when_volume_cant_absorb_qty():
    # exit-bar volume 40 < min_volume_mult(1.0) * qty(50, the 2024 lot) -> slip
    # doubles; a liquid exit bar keeps the base slip
    d1, spot, chain = _slip_tape(exit_vol=40)
    knobs = Knobs(slippage_pct=0.01, min_volume_mult=1.0, thin_exit_mult=2.0)
    res = Backtest(FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})]), CostModel(),
                   knobs).run(BuyThenMarketExit(), d1, d1)
    assert res.trades[0].exit == pytest.approx(110.0 * (1 - 0.02))

    d1, spot, chain = _slip_tape(exit_vol=5000)
    res = Backtest(FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})]), CostModel(),
                   knobs).run(BuyThenMarketExit(), d1, d1)
    assert res.trades[0].exit == pytest.approx(110.0 * (1 - 0.01))


class BuyThenSpotStop(Strategy):
    def on_day_start(self, ctx):
        self._in = False

    def on_bar(self, ctx):
        if not self._in:
            self._in = True
            return [Order(side="CALL", strike=20000, lots=1)]
        return []

    def manage(self, ctx, pos):
        from engine.strategy.exit import Exit
        return Exit.spot_at(19980, "fall", "spot_sl")


def test_spot_level_exit_resolves_on_1min_spot_with_1min_latency():
    # spot crosses 19980 on the 9:21 1-min bar (inside the 9:20 strategy bar);
    # the option fills at the NEXT 1-min open, 9:22 -> 97. The old
    # Exit.market('spot_sl') pattern would have waited for the 9:25 bar.
    d1 = date(2024, 1, 2)
    five = pd.DataFrame(
        [(datetime(2024, 1, 2, 9, 15), 20000.0, 20000.0, 20000.0, 20000.0),
         (datetime(2024, 1, 2, 9, 20), 20000.0, 20000.0, 19960.0, 19990.0)],
        columns=["DateTime", "Open", "High", "Low", "Close"])
    one = pd.DataFrame(
        [(datetime(2024, 1, 2, 9, 15), 20000.0, 20000.0, 20000.0, 20000.0),
         (datetime(2024, 1, 2, 9, 20), 20000.0, 20000.0, 19995.0, 19998.0),
         (datetime(2024, 1, 2, 9, 21), 19998.0, 19998.0, 19960.0, 19970.0),
         (datetime(2024, 1, 2, 9, 22), 19970.0, 19992.0, 19968.0, 19990.0)],
        columns=["DateTime", "Open", "High", "Low", "Close"])
    spot = SpotSeries(five, one_min=one)
    bars = {20: (100, 100, 100, 100),       # entry fill
            21: (98, 99, 95, 96),
            22: (97, 98, 94, 95),           # exit fill at this bar's open
            25: (96, 97, 95, 96)}
    rows = [make_row(datetime(2024, 1, 2, 9, m), 20000, call_open=o, call_high=h,
                     call_low=l, call_close=c) for m, (o, h, l, c) in bars.items()]
    chain = Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))
    res = Backtest(FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})]),
                   CostModel()).run(BuyThenSpotStop(), d1, d1)
    assert len(res.trades) == 1
    assert res.trades[0].reason == "spot_sl"
    assert res.trades[0].exit == pytest.approx(97.0)
    assert res.trades[0].exit_dt.minute == 22


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


def test_intrabar_band_trail_pessimistic_vs_optimistic():
    # bar 25 spans BOTH a new peak (130) and a would-be trail stop: the mixed/
    # optimistic reading folds the high first (stop 110, low 105 -> exit 110);
    # the pessimistic reading checks the low against the PRIOR peak 100 first
    # (stop 80 -> survives), folds 130 in after, and exits next bar at its
    # open 108 (open < stop 110 -> worse open). The spread IS the error band.
    d1 = date(2024, 1, 2)
    spot = SpotSeries(pd.DataFrame(
        [(datetime(2024, 1, 2, 9, m), 100.0, 100.0, 100.0, 100.0) for m in (15, 20, 25, 30)],
        columns=["DateTime", "Open", "High", "Low", "Close"]))
    bars = {20: (100, 100, 100, 100),
            25: (110, 130, 105, 128),       # high 130 and low 105 in ONE bar
            30: (108, 112, 106, 108),
            35: (108, 109, 107, 108)}
    rows = [make_row(datetime(2024, 1, 2, 9, m), 20000, call_open=o, call_high=h,
                     call_low=l, call_close=c) for m, (o, h, l, c) in bars.items()]
    chain = Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))
    market = lambda: FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})])

    res = Backtest(market(), CostModel(),
                   Knobs(intrabar="optimistic")).run(BuyThenPremiumTrail(), d1, d1)
    assert res.trades[0].reason == "ptrail" and res.trades[0].exit == pytest.approx(110.0)

    res = Backtest(market(), CostModel(),
                   Knobs(intrabar="pessimistic")).run(BuyThenPremiumTrail(), d1, d1)
    assert res.trades[0].reason == "ptrail" and res.trades[0].exit == pytest.approx(108.0)
    assert res.trades[0].exit_dt.minute == 30       # survives bar 25, dies next bar


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


class SellNakedCall(Strategy):
    def on_day_start(self, ctx):
        self._in = False

    def on_bar(self, ctx):
        if not self._in:
            self._in = True
            return [Order(action="sell", side="CALL", strike=20000, lots=1)]
        return []


def test_naked_short_legs_are_refused():
    # net-short exposure isn't modeled (margin/assignment) -> loud error,
    # not silently wrong numbers; covered verticals stay allowed (orb_spread)
    d1 = date(2024, 1, 2)
    spot = _spot_5min([d1])
    batches = [(date(2024, 1, 11), {d1: _day_chain(d1, 100, 120)})]
    with pytest.raises(ValueError, match="naked short"):
        Backtest(FakeMarket(spot, batches), CostModel()).run(SellNakedCall(), d1, d1)


class BuyThenTwoStagePolicy(Strategy):
    """manage() returns the TwoStage policy itself -> engine-owned in-window handoff."""
    def on_day_start(self, ctx):
        from engine.strategy.exit import TwoStage
        self._in = False
        self._pol = TwoStage(target_pts=25, trail_gap=20, fraction=0.5, stop_pts=12)

    def on_bar(self, ctx):
        if not self._in:
            self._in = True
            return [Order(side="CALL", strike=20000, lots=1)]
        return []

    def manage(self, ctx, pos):
        return self._pol


def test_two_stage_policy_hands_off_to_trail_in_the_same_window():
    # entry 100 -> target 125 books half on the 9:21 bar; the ENGINE-owned
    # policy starts trailing the remainder on the very next 1-min bar with the
    # peak seeded at the scale-out fill: 9:22 peak 125 -> stop 105, low 104
    # breaches -> rest out at 105 in the SAME strategy bar. (The legacy
    # exit_for() path would have waited for the next 5-min bar.)
    d1 = date(2024, 1, 2)
    spot = SpotSeries(pd.DataFrame(
        [(datetime(2024, 1, 2, 9, m), 100.0, 100.0, 100.0, 100.0) for m in (15, 20, 25)],
        columns=["DateTime", "Open", "High", "Low", "Close"]))
    bars = {20: (100, 100, 100, 100),
            21: (110, 130, 120, 128),       # target 125 books half
            22: (118, 125, 104, 108),       # trail: stop 125-20=105, low 104 -> out at 105
            25: (108, 109, 107, 108)}
    rows = [make_row(datetime(2024, 1, 2, 9, m), 20000, call_open=o, call_high=h,
                     call_low=l, call_close=c) for m, (o, h, l, c) in bars.items()]
    chain = Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))
    res = Backtest(FakeMarket(spot, [(date(2024, 1, 11), {d1: chain})]),
                   CostModel()).run(BuyThenTwoStagePolicy(), d1, d1)
    full = lot_size_for(d1)
    assert [t.reason for t in res.trades] == ["book", "ptrail"]
    assert res.trades[0].exit == pytest.approx(125.0)
    assert res.trades[1].exit == pytest.approx(105.0)
    assert res.trades[0].exit_dt.minute == 21 and res.trades[1].exit_dt.minute == 22
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
