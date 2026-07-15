"""Ema5Itm: arming/disarm state machine, trigger->ITM order, spot exits."""
from datetime import datetime, time

import pandas as pd
import pytest

from engine.data.spot import SpotSeries
from engine.strategy.base import BarContext
from strategies.ema5_itm import Ema5Itm, itm_strike


def _spot(rows):
    df = pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]).assign(
        DateTime=lambda d: pd.to_datetime(d["DateTime"]))
    return SpotSeries(df)


def _ctx(spot, dt):
    return BarContext(now=dt, chain=None, spot=spot)


def _flat_then(rows_after_warmup):
    """6 flat warm-up 5m bars at 20000 (EMA converges to 20000), then customs."""
    t0 = pd.Timestamp("2024-01-02 09:15")
    rows = [(t0 + pd.Timedelta(minutes=5 * i), 20000, 20000, 20000, 20000)
            for i in range(6)]
    t = t0 + pd.Timedelta(minutes=30)
    for r in rows_after_warmup:
        rows.append((t, *r))
        t += pd.Timedelta(minutes=5)
    return _spot(rows), [r[0] for r in rows]


def run_bars(strategy, spot, times):
    strategy.on_day_start(_ctx(spot, times[0]))
    out = []
    for t in times:
        out.append(strategy.on_bar(_ctx(spot, t)))
    return out


def test_itm_strike_grid():
    assert itm_strike(20140, "CALL", 100) == 20000     # floor 20100 - 100
    assert itm_strike(20140, "PUT", 100) == 20250      # ceil 20150 + 100
    assert itm_strike(20150, "PUT", 100) == 20250      # exact grid


def test_put_arms_above_ema_and_triggers_on_low_break():
    # candle fully above EMA(~20000): arms at low 20030; next bar breaks it
    spot, times = _flat_then([(20040, 20060, 20030, 20050),   # arms (30,60)
                              (20040, 20045, 20020, 20025)])  # low<30 -> trigger
    s = Ema5Itm(side="PUT", itm_offset=100, rr=3)
    out = run_bars(s, spot, times)
    orders = out[-1]
    assert len(orders) == 1
    assert orders[0].side == "PUT"
    assert s.spot_sl == 20060
    assert s.spot_tgt == pytest.approx(20030 - 3 * 30)        # risk = 60-30


def test_ema_touch_while_moving_higher_disarms():
    # candle above EMA arms (low 20030); next candle MOVES HIGHER without
    # breaking the low but wicks the risen EMA (~20034.4 after its close)
    # -> disarm without re-arming; a third bar breaking the stale 20030
    # level must NOT trigger
    spot, times = _flat_then([(20040, 20060, 20030, 20050),
                              (20050, 20080, 20034, 20070),   # low>30, touches EMA
                              (20025, 20028, 20020, 20025)])  # breaks stale level
    s = Ema5Itm(side="PUT")
    out = run_bars(s, spot, times)
    assert out[-1] == [] and out[-2] == []


def test_break_of_armed_level_triggers_even_if_candle_later_touches_ema():
    # a candle that breaks the armed low IS the entry (stop order fills at
    # the level) — reaching the EMA afterwards doesn't retract the fill
    spot, times = _flat_then([(20040, 20060, 20030, 20050),
                              (20040, 20050, 19999, 20045)])  # breaks 20030
    s = Ema5Itm(side="PUT")
    out = run_bars(s, spot, times)
    assert len(out[-1]) == 1 and out[-1][0].side == "PUT"


def test_no_arming_before_warmup():
    t0 = pd.Timestamp("2024-01-02 09:15")
    rows = [(t0, 20040, 20060, 20030, 20050),                  # would arm if warm
            (t0 + pd.Timedelta(minutes=5), 20025, 20028, 20000, 20010)]
    spot = _spot(rows)
    s = Ema5Itm(side="PUT", min_warmup=5)
    out = run_bars(s, spot, [r[0] for r in rows])
    assert out == [[], []]


def test_call_mirror_arms_below_and_triggers_on_high_break():
    spot, times = _flat_then([(19960, 19970, 19940, 19950),   # fully below EMA
                              (19965, 19985, 19960, 19980)])  # high>19970 -> trigger
    s = Ema5Itm(side="CALL", itm_offset=100, rr=3)
    out = run_bars(s, spot, times)
    orders = out[-1]
    assert len(orders) == 1 and orders[0].side == "CALL"
    assert s.spot_sl == 19940
    assert s.spot_tgt == pytest.approx(19970 + 3 * 30)


def test_manage_spot_stop_target_and_eod():
    spot, times = _flat_then([(20040, 20060, 20030, 20050),
                              (20040, 20045, 20020, 20025)])
    s = Ema5Itm(side="PUT", rr=3)
    run_bars(s, spot, times)

    class B:                                                   # stub bars
        def __init__(self, h, l, c): self.high, self.low, self.close, self.open = h, l, c, c

    class Ctx:
        def __init__(self, b, t): self.spot_bar, self._t = b, t
        @property
        def now(self): return self._t

    t = datetime(2024, 1, 2, 11, 0)
    assert s.manage(Ctx(B(20030, 20010, 20020), t), None) is None
    ex = s.manage(Ctx(B(20065, 20040, 20050), t), None)        # high >= 20060
    assert ex.reason == "spot_sl"
    ex = s.manage(Ctx(B(20000, 19935, 19950), t), None)        # low <= 19940 tgt
    assert ex.reason == "spot_tgt"
    ex = s.manage(Ctx(B(20030, 20010, 20020), datetime(2024, 1, 2, 15, 10)), None)
    assert ex.reason == "eod"
