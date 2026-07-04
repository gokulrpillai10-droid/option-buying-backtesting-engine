"""CPRBreakout: CPR math, cross-detection entry, deep-ITM strike, exit stages."""
from datetime import datetime, time

import pandas as pd
import pytest

from engine.data.spot import SpotSeries
from engine.data.chain import Chain
from engine.strategy.base import BarContext
from engine.position.manager import Position
from strategies.cpr_breakout import CPRBreakout, _strike
from tests.conftest import make_row, CHAIN_COLUMNS


def _spot():
    # prior day H=20200 L=20000 C=20150 -> Pivot 20116.67, BC 20100, TC 20133.33
    #   => top 20133.33, bottom 20100
    rows = [
        ("2024-01-01 09:15", 20100, 20200, 20000, 20050),   # prior day: sets H/L
        ("2024-01-01 15:25", 20120, 20160, 20110, 20150),   # prior day last close 20150
        ("2024-01-02 09:15", 20120, 20125, 20118, 20122),   # inside band, no cross
        ("2024-01-02 09:20", 20120, 20140, 20115, 20140),   # low<=top & close>top -> CE cross
    ]
    df = pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]).assign(
        DateTime=lambda d: pd.to_datetime(d["DateTime"]))
    return SpotSeries(df)


def _ctx(spot, dt):
    return BarContext(now=dt, chain=None, spot=spot)


def test_cpr_levels_from_prior_day():
    s = CPRBreakout()
    s.on_day_start(_ctx(_spot(), datetime(2024, 1, 2, 9, 15)))
    assert s.active
    assert s.top == pytest.approx(20133.333, abs=0.01)
    assert s.bottom == pytest.approx(20100.0)


def test_no_entry_when_candle_stays_inside_band():
    s, spot = CPRBreakout(), _spot()
    s.on_day_start(_ctx(spot, datetime(2024, 1, 2, 9, 15)))
    assert s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 15))) == []   # 20118-20125 inside


def test_ce_entry_on_upcross_buys_deep_itm_call():
    s, spot = CPRBreakout(), _spot()
    s.on_day_start(_ctx(spot, datetime(2024, 1, 2, 9, 15)))
    s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 15)))
    orders = s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 20)))        # crosses up through top
    assert len(orders) == 1
    o = orders[0]
    assert o.side == "CALL" and o.lots == 6
    assert o.strike == _strike(20140, "CALL") == 19950                # floor(20140/50)*50 - 150
    assert s.entered and s.side == "CALL"


def test_one_breakout_per_day():
    s, spot = CPRBreakout(), _spot()
    s.on_day_start(_ctx(spot, datetime(2024, 1, 2, 9, 15)))
    s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 20)))                 # first cross -> entered
    assert s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 20))) == []    # no second entry


def test_default_is_deployable_config_book4_target50_iv012():
    # defaults = walk-forward config: +50 book 4/6, trail 2 by 12, far-edge stop, IV<=0.12
    s, spot = CPRBreakout(), _spot()
    assert s.max_iv == 0.12 and s.stop_mode == "far" and s.STOP_PREM is None
    s.on_day_start(_ctx(spot, datetime(2024, 1, 2, 9, 15)))           # chain=None -> IV unknown -> trades
    s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 20)))                 # enter CE (far-edge stop=bottom)
    pos = Position("CALL", 19950, entry=300.0, qty=6 * 75)
    ex = s.manage(_ctx(spot, datetime(2024, 1, 2, 9, 20)), pos)       # close 20140 > bottom -> no cpr stop
    assert ex.kind == "bracket"
    assert ex.target == pytest.approx(350.0) and ex.target_fraction == pytest.approx(4 / 6)  # +50 book 4/6
    assert ex.stop < 0                                                # loss-cap off -> unreachable stop
    reduced = Position("CALL", 19950, entry=300.0, qty=2 * 75)        # carried 2 -> trail
    assert s.manage(_ctx(spot, datetime(2024, 1, 2, 9, 20)), reduced).kind == "trail"


def test_premium_loss_cap_sets_bracket_stop():
    s, spot = CPRBreakout(stop_prem=40), _spot()
    s.on_day_start(_ctx(spot, datetime(2024, 1, 2, 9, 15)))
    s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 20)))
    pos = Position("CALL", 19950, entry=300.0, qty=6 * 75)
    ex = s.manage(_ctx(spot, datetime(2024, 1, 2, 9, 20)), pos)
    assert ex.stop == pytest.approx(260.0) and ex.stop_fraction == 1.0   # entry-40, full stop


def test_stop_mode_none_disables_cpr_close_stop():
    s, spot = CPRBreakout(stop_mode="none", stop_prem=15), _spot()
    s.on_day_start(_ctx(spot, datetime(2024, 1, 2, 9, 15)))
    s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 20)))                 # enter CE
    spot2 = SpotSeries(pd.DataFrame(
        [("2024-01-02 10:00", 19000, 19000, 19000, 19000)],          # close far below bottom
        columns=["DateTime", "Open", "High", "Low", "Close"]).assign(
        DateTime=lambda d: pd.to_datetime(d["DateTime"])))
    pos = Position("CALL", 19950, entry=300.0, qty=6 * 75)
    ex = s.manage(_ctx(spot2, datetime(2024, 1, 2, 10, 0)), pos)
    assert ex.kind == "bracket" and ex.stop == pytest.approx(285.0)   # only the -15 premium stop


def test_far_edge_cpr_stop_fires_on_close_below_bottom():
    s, spot = CPRBreakout(stop_mode="far"), _spot()
    s.on_day_start(_ctx(spot, datetime(2024, 1, 2, 9, 15)))
    s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 20)))                 # enter CE, stop_close=bottom 20100
    spot2 = SpotSeries(pd.DataFrame(
        [("2024-01-02 10:00", 20090, 20090, 20090, 20090)],          # close 20090 < bottom
        columns=["DateTime", "Open", "High", "Low", "Close"]).assign(
        DateTime=lambda d: pd.to_datetime(d["DateTime"])))
    pos = Position("CALL", 19950, entry=300.0, qty=6 * 75)
    ex = s.manage(_ctx(spot2, datetime(2024, 1, 2, 10, 0)), pos)
    assert ex.kind == "market" and ex.reason == "cpr_stop"


def _chain_with_iv(iv):
    row = make_row(datetime(2024, 1, 2, 9, 15), 20100, iv=iv, forward=20122)
    return Chain(pd.DataFrame([row], columns=CHAIN_COLUMNS))


def test_max_iv_filter_skips_high_vol_days():
    spot = _spot()
    ctx = BarContext(now=datetime(2024, 1, 2, 9, 15), chain=_chain_with_iv(0.25), spot=spot)
    s_skip = CPRBreakout(max_iv=0.20)            # day-open IV 0.25 > 0.20 -> skip
    s_skip.on_day_start(ctx)
    assert s_skip.active is False
    s_keep = CPRBreakout(max_iv=0.30)            # 0.25 <= 0.30 -> trade
    s_keep.on_day_start(ctx)
    assert s_keep.active is True


def test_narrow_cpr_filter_skips_wide_days():
    # prior day range H-L = 20200-20000 = 200; CPR width = 33.33 -> ratio ~0.167
    spot = _spot()
    s_skip = CPRBreakout(max_cpr_ratio=0.10)                          # 0.167 > 0.10 -> skip
    s_skip.on_day_start(_ctx(spot, datetime(2024, 1, 2, 9, 15)))
    assert s_skip.active is False
    s_keep = CPRBreakout(max_cpr_ratio=0.30)                          # 0.167 <= 0.30 -> trade
    s_keep.on_day_start(_ctx(spot, datetime(2024, 1, 2, 9, 15)))
    assert s_keep.active is True
