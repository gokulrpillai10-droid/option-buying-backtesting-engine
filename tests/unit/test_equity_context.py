"""EquityBarContext: spot-forwarding accessors + look-ahead safety."""
from datetime import date, datetime, time

import pytest

from engine.data.spot import SpotSeries, _resample_ohlc
from engine.equity.context import EquityBarContext
from tests.equity_helpers import frame, synth_day


@pytest.fixture
def spot_5m():
    """Two sessions, 5-min bars. Day 1 carries a known H/L/C for CPR-style
    prev-day reads; day 2 is flat 1700 with one marked bar."""
    rows = (synth_day(date(2024, 1, 1), 1700.0,
                      overrides={"10:00": (1700, 1720, 1700, 1715),   # day HIGH 1720
                                 "11:00": (1700, 1700, 1690, 1695),   # day LOW 1690
                                 "15:29": (1710, 1710, 1710, 1710)})  # day CLOSE 1710
            + synth_day(date(2024, 1, 2), 1700.0,
                        overrides={"09:17": (1700, 1712, 1698, 1705)}))
    return SpotSeries(_resample_ohlc(frame(rows), "5min", "left"))


def test_now_spot_and_bar(spot_5m):
    now = datetime(2024, 1, 2, 9, 15)
    ctx = EquityBarContext(now=now, spot=spot_5m)
    assert ctx.now == now
    b = ctx.spot_bar                     # 5-min bar 09:15 contains the 09:17 override
    assert b.dt == now
    assert b.high == 1712.0 and b.low == 1698.0
    assert ctx.spot == b.close


def test_prev_day_hlc_and_prev_close(spot_5m):
    ctx = EquityBarContext(now=datetime(2024, 1, 2, 9, 15), spot=spot_5m)
    assert ctx.prev_day_hlc() == (1720.0, 1690.0, 1710.0)
    assert ctx.prev_close() == 1710.0


def test_recent_and_history_no_lookahead(spot_5m):
    now = datetime(2024, 1, 2, 9, 25)
    ctx = EquityBarContext(now=now, spot=spot_5m)
    r = ctx.recent(3)
    assert len(r) == 3 and r[-1].dt == now
    assert all(b.dt <= now for b in ctx.spot_history())


def test_opening_range(spot_5m):
    ctx = EquityBarContext(now=datetime(2024, 1, 2, 10, 0), spot=spot_5m)
    hi, lo = ctx.opening_range(time(9, 30))
    assert hi == 1712.0 and lo == 1698.0          # from the 09:15 bar only

def test_opening_range_never_past_now(spot_5m):
    # now is 09:20 but the requested window extends to 10:00 -> clamps at now
    ctx = EquityBarContext(now=datetime(2024, 1, 2, 9, 20), spot=spot_5m)
    hi, lo = ctx.opening_range(time(10, 0))
    assert hi == 1712.0 and lo == 1698.0
