"""CPR-equity entries on synthetic paths + signal parity with the options CPR.

Parity is the port's key risk: the SAME spot path must produce IDENTICAL cross
timestamps and directions in strategies.cpr_breakout.CPRBreakout (options) and
CPRBreakoutEquity — proving the signal transferred faithfully.
"""
from datetime import date, datetime, time

import pytest

from engine.data.spot import SpotSeries, _resample_ohlc
from engine.equity.context import EquityBarContext
from engine.equity.position import EquityPosition
from engine.strategy.base import BarContext
from strategies.cpr_breakout import CPRBreakout
from strategies.cpr_breakout_equity import CPRBreakoutEquity
from tests.equity_helpers import frame, synth_day

D1, D2 = date(2024, 1, 1), date(2024, 1, 2)

# Day 1: H=1720, L=1690, C=1710 -> pivot 1706.667, bc 1705, tc 1708.333
# CPR band: top=1708.333, bottom=1705.0
DAY1 = {"10:00": (1700, 1720, 1700, 1715),
        "11:00": (1700, 1700, 1690, 1695),
        "15:29": (1710, 1710, 1710, 1710)}
TOP, BOTTOM = 1708.0 + 1.0 / 3.0, 1705.0


def series(day2_overrides):
    rows = synth_day(D1, 1700.0, overrides=DAY1) + synth_day(D2, 1700.0,
                                                             overrides=day2_overrides)
    return SpotSeries(_resample_ohlc(frame(rows), "5min", "left"))


def day2_bars(spot):
    return [t for t in spot.timestamps() if t.to_pydatetime().date() == D2]


def run_equity(spot, strat):
    """Drive the strategy over day 2; return [(bar_dt, direction)] of entries."""
    fires = []
    bars = day2_bars(spot)
    strat.on_day_start(EquityBarContext(now=bars[0].to_pydatetime(), spot=spot))
    for b in bars:
        orders = strat.on_bar(EquityBarContext(now=b.to_pydatetime(), spot=spot))
        for o in orders:
            fires.append((b.to_pydatetime(), o.direction))
    return fires


def run_options(spot, strat):
    """Same loop through the options CPRBreakout (no chain needed: max_iv=None)."""
    fires = []
    bars = day2_bars(spot)
    strat.on_day_start(BarContext(now=bars[0].to_pydatetime(), chain=None, spot=spot))
    for b in bars:
        orders = strat.on_bar(BarContext(now=b.to_pydatetime(), chain=None, spot=spot))
        for o in orders:
            fires.append((b.to_pydatetime(), o.side))
    return fires


# Cross overrides land on the LAST minute of the [10:30, 10:35) bin so they set
# the resampled 5-min bar's CLOSE (a mid-bin minute would let Close revert to
# the flat 1700 base and corrupt the intended candle shape).
UP_CROSS = {"10:34": (1706, 1712, 1706, 1711)}    # 10:30 bar: low<=top, close>top
DOWN_CROSS = {"10:34": (1707, 1707, 1701, 1703)}  # 10:30 bar: high>=bottom, close<bottom
GAP_ABOVE = {"10:30": (1712, 1715, 1711, 1714),   # whole 10:30 bar above top
             "10:31": (1712, 1715, 1711, 1714),   # (bin low 1711 > 1708.33):
             "10:32": (1714, 1715, 1712, 1714),   # no physical cross -> no entry
             "10:33": (1713, 1714, 1712, 1713),
             "10:34": (1713, 1714, 1712, 1714)}


def test_up_cross_goes_long():
    spot = series(UP_CROSS)
    fires = run_equity(spot, CPRBreakoutEquity())
    assert fires == [(datetime(2024, 1, 2, 10, 30), "long")]


def test_down_cross_goes_short():
    spot = series(DOWN_CROSS)
    fires = run_equity(spot, CPRBreakoutEquity())
    assert fires == [(datetime(2024, 1, 2, 10, 30), "short")]


def test_gap_beyond_band_does_not_enter():
    # 10:30 bar sits entirely above the top edge (low > top): the cross
    # requirement (low <= top) must reject it, and the flat rest of the day
    # never crosses -> no entries at all.
    spot = series(GAP_ABOVE)
    assert run_equity(spot, CPRBreakoutEquity()) == []


def test_no_entry_at_or_after_cutoff():
    late = {"14:49": (1706, 1712, 1706, 1711)}    # falls in the 14:45 bar (bin-last)
    spot = series(late)
    assert run_equity(spot, CPRBreakoutEquity(entry_cutoff=time(14, 45))) == []


def test_one_entry_per_day():
    two = {"10:34": (1706, 1712, 1706, 1711),     # first cross (10:30 bar close)
           "10:36": (1704, 1704, 1704, 1704),     # dips back below
           "10:44": (1706, 1712, 1706, 1711)}     # genuine second cross (10:40 bar) ignored
    spot = series(two)
    assert len(run_equity(spot, CPRBreakoutEquity())) == 1


@pytest.mark.parametrize("overrides,eq_dir,opt_side", [
    (UP_CROSS, "long", "CALL"),
    (DOWN_CROSS, "short", "PUT"),
])
def test_signal_parity_with_options_cpr(overrides, eq_dir, opt_side):
    spot = series(overrides)
    eq = run_equity(spot, CPRBreakoutEquity())
    opt = run_options(spot, CPRBreakout(max_iv=None, max_cpr_ratio=None))
    assert len(eq) == len(opt) == 1
    assert eq[0][0] == opt[0][0]                  # same bar timestamp
    assert (eq[0][1], opt[0][1]) == (eq_dir, opt_side)


def test_manage_cpr_far_stop_and_target_levels():
    spot = series(UP_CROSS)
    strat = CPRBreakoutEquity(target_pts=10.0)
    run_equity(spot, strat)                        # enter long; stop = bottom (far)
    pos = EquityPosition(direction="long", entry=1712.0, qty=58)

    # flat 1700 bar: above the far stop (bottom=1705)? NO — 1700 < 1705 -> stop.
    ctx = EquityBarContext(now=datetime(2024, 1, 2, 11, 0), spot=spot)
    ex = strat.manage(ctx, pos)
    assert ex.kind == "market" and ex.reason == "cpr_stop"

    # bar at 10:30 closes 1711 > bottom -> no stop; target resting at entry+10.
    ctx2 = EquityBarContext(now=datetime(2024, 1, 2, 10, 30), spot=spot)
    ex2 = strat.manage(ctx2, pos)
    assert ex2.kind == "level" and ex2.trigger == "rise"
    assert ex2.level == pytest.approx(1722.0)

    # short mirror: target is a "fall" level below entry.
    spot_dn = series(DOWN_CROSS)
    strat_dn = CPRBreakoutEquity(target_pts=10.0)
    run_equity(spot_dn, strat_dn)
    pos_dn = EquityPosition(direction="short", entry=1702.0, qty=58)
    ex3 = strat_dn.manage(EquityBarContext(now=datetime(2024, 1, 2, 10, 30),
                                           spot=spot_dn), pos_dn)
    assert ex3.kind == "level" and ex3.trigger == "fall"
    assert ex3.level == pytest.approx(1692.0)

    # square time -> EOD market exit regardless.
    ex4 = strat.manage(EquityBarContext(now=datetime(2024, 1, 2, 15, 15),
                                        spot=spot), pos)
    assert ex4.kind == "market" and ex4.reason == "eod"


def test_target_none_means_hold():
    spot = series(UP_CROSS)
    strat = CPRBreakoutEquity(target_pts=None)
    run_equity(spot, strat)
    pos = EquityPosition(direction="long", entry=1712.0, qty=58)
    ctx = EquityBarContext(now=datetime(2024, 1, 2, 10, 30), spot=spot)
    assert strat.manage(ctx, pos) is None          # no stop hit, no target -> hold
