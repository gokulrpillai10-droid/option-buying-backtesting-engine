from datetime import date, datetime, time

from engine.data.spot import SpotSeries, _resample_ohlc
from engine.equity.context import EquityBarContext
from engine.equity.position import EquityPosition
from strategies.gap_router_equity import GapRouterEquity
from tests.equity_helpers import frame, synth_day

D1, D2 = date(2024, 1, 1), date(2024, 1, 2)


class FakeNifty:
    def __init__(self, value=None):
        self.value = value
    def gap_for(self, d):
        return self.value


def series(day2_open, day2_overrides=None):
    # Day 1 flat at 800 -> prev_close 800. Day 2 opens at day2_open.
    d2 = {"09:15": (day2_open, day2_open, day2_open, day2_open)}
    if day2_overrides:
        d2.update(day2_overrides)
    rows = synth_day(D1, 800.0) + synth_day(D2, day2_open, overrides=d2)
    return SpotSeries(_resample_ohlc(frame(rows), "1min", "left"))


def day2_bars(spot):
    return [t for t in spot.timestamps() if t.to_pydatetime().date() == D2]


def run(spot, strat):
    fires = []
    bars = day2_bars(spot)
    strat.on_day_start(EquityBarContext(now=bars[0].to_pydatetime(), spot=spot))
    for b in bars:
        for o in strat.on_bar(EquityBarContext(now=b.to_pydatetime(), spot=spot)):
            fires.append((b.to_pydatetime(), o.direction))
    return fires


def test_small_unaligned_gap_up_fades_short_at_open():
    # open 802 = +0.25% gap, NIFTY missing -> fade short on the first bar
    spot = series(802.0)
    fires = run(spot, GapRouterEquity(FakeNifty(None)))
    assert fires == [(datetime(2024, 1, 2, 9, 15), "short")]


def test_big_aligned_driving_gap_goes_long_at_0930():
    # open 810 (+1.25% big), NIFTY aligned +1%, price drives to 815 by 09:30
    spot = series(810.0, {"09:30": (815.0, 815.0, 815.0, 815.0)})
    fires = run(spot, GapRouterEquity(FakeNifty(0.01)))
    assert fires == [(datetime(2024, 1, 2, 9, 30), "long")]


def test_big_gap_reverting_fades_short_at_0930():
    # open 810 up-gap but reverts to 806 by 09:30 (drive<0) -> fade short
    spot = series(810.0, {"09:30": (806.0, 806.0, 806.0, 806.0)})
    fires = run(spot, GapRouterEquity(FakeNifty(0.01)))
    assert fires == [(datetime(2024, 1, 2, 9, 30), "short")]


def test_ambiguous_defer_no_trade():
    # open 810 big, NIFTY not aligned, weak +drive (811) below drive_thr -> none
    spot = series(810.0, {"09:30": (811.0, 811.0, 811.0, 811.0)})
    assert run(spot, GapRouterEquity(FakeNifty(None))) == []


def test_no_gap_no_trade():
    spot = series(800.4)                       # +0.05% < gap_thr
    assert run(spot, GapRouterEquity(FakeNifty(None))) == []


def test_one_trade_per_day_guard():
    # even if on_bar is called every bar, only the first fade order is emitted
    spot = series(802.0)
    strat = GapRouterEquity(FakeNifty(None))
    assert len(run(spot, strat)) == 1


def test_manage_fade_short_target_is_prior_close():
    spot = series(802.0)
    strat = GapRouterEquity(FakeNifty(None))
    run(spot, strat)                            # fade short at open
    pos = EquityPosition(direction="short", entry=802.0, qty=100)
    # before 09:30: no OR stop, resting target at prior close (802 fade -> 800)
    ex = strat.manage(EquityBarContext(now=datetime(2024, 1, 2, 9, 20), spot=spot), pos)
    assert ex.kind == "level" and ex.trigger == "fall" and ex.level == 800.0


def test_manage_square_off():
    spot = series(802.0)
    strat = GapRouterEquity(FakeNifty(None))
    run(spot, strat)
    pos = EquityPosition(direction="short", entry=802.0, qty=100)
    ex = strat.manage(EquityBarContext(now=datetime(2024, 1, 2, 15, 15), spot=spot), pos)
    assert ex.kind == "market" and ex.reason == "eod"
