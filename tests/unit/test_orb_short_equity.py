"""OrbShortEquity decision/exit paths on synthetic sessions (5min timeframe).

Day layout: day1 is a flat warm-up session (supplies prev_close); day2 is the
scenario day at base 1000 with an 09:15-09:30 opening range of 995-1005
(low/high set on the 09:16 minute)."""
from datetime import date, time

import pytest

from engine.equity.backtest import EquityBacktest, EquityKnobs
from engine.equity.costs import EquityCostModel
from strategies.orb_short_equity import OrbShortEquity
from tests.equity_helpers import frame, synth_day

D1, D2 = date(2026, 3, 2), date(2026, 3, 3)


def run(overrides, day2_open=1000.0, **strat_kw):
    ov = {"09:15": (day2_open, day2_open, day2_open, day2_open),
          "09:16": (1000, 1005, 995, 1000), **overrides}
    df = frame(synth_day(D1, 1000.0) + synth_day(D2, 1000.0, ov))
    bt = EquityBacktest(df, EquityCostModel(),
                        EquityKnobs(timeframe="5min", slippage_pct=0.0))
    r = bt.run(OrbShortEquity(**strat_kw))
    return r.trades


def drop_from(start_min, px):
    """Overrides pinning every minute from ``start_min`` to EOD at ``px``."""
    out = {}
    for h in range(9, 16):
        for m in range(60):
            if (h, m) >= start_min and (h, m) <= (15, 29) and (h, m) >= (9, 15):
                out[f"{h:02d}:{m:02d}"] = (px, px, px, px)
    return out


def test_break_below_or_shorts_and_rides_to_eod():
    # price pinned at 990 from 10:00: signal = 10:00 5m close below OR-low
    # 995, entry fills at the 10:05 open (990), squared off at the 990 close
    trades = run(drop_from((10, 0), 990.0))
    assert len(trades) == 1
    t = trades[0]
    assert t.side == "SHORT"
    assert t.reason == "eod"
    assert t.entry == pytest.approx(990.0)     # next-5m-open fill, 0 slippage
    assert t.exit == pytest.approx(990.0)
    assert t.gross == pytest.approx(0.0)


def test_break_and_trend_down_pays():
    ov = {**drop_from((10, 0), 990.0), **drop_from((12, 0), 970.0)}
    trades = run(ov)
    assert len(trades) == 1
    t = trades[0]
    assert t.side == "SHORT" and t.reason == "eod"
    assert t.entry == pytest.approx(990.0)
    assert t.exit == pytest.approx(970.0)
    assert t.gross == pytest.approx((990.0 - 970.0) * t.qty)


def test_or_stop_exits_at_or_high():
    ov = {**drop_from((10, 0), 990.0), "11:00": (1006, 1006, 1006, 1006)}
    trades = run(ov)
    assert len(trades) == 1
    t = trades[0]
    assert t.reason == "or_stop"
    # gap-through: bar opens beyond the 1005 level -> fill at the worse open
    assert t.exit == pytest.approx(1006.0)
    assert t.gross < 0


def test_one_trade_per_day_no_reentry_after_stop():
    ov = {**drop_from((10, 0), 990.0), "11:00": (1006, 1006, 1006, 1006),
          **drop_from((12, 0), 980.0)}     # second breakdown after the stop
    trades = run(ov)
    assert len(trades) == 1                 # stopped once, never re-enters


def test_gap_up_day_is_skipped():
    trades = run(drop_from((10, 0), 990.0), day2_open=1002.0)  # +0.2% gap
    assert trades == []


def test_break_after_cutoff_is_skipped():
    trades = run(drop_from((11, 0), 990.0))  # first 5m close below OR at 11:00
    assert trades == []


def test_regime_skip_stands_aside():
    trades = run(drop_from((10, 0), 990.0),
                 regime_skip=lambda d: d == D2)
    assert trades == []


def test_fixed_qty_overrides_capital_sizing():
    ov = {**drop_from((10, 0), 990.0), **drop_from((12, 0), 970.0)}
    trades = run(ov, qty=500)
    assert len(trades) == 1
    t = trades[0]
    assert t.qty == 500                          # not floor(100k / 990)
    assert t.gross == pytest.approx((990.0 - 970.0) * 500)


def test_first_day_without_prev_close_is_skipped():
    df = frame(synth_day(D1, 1000.0, {"09:16": (1000, 1005, 995, 1000),
                                      **drop_from((10, 0), 990.0)}))
    bt = EquityBacktest(df, EquityCostModel(),
                        EquityKnobs(timeframe="5min", slippage_pct=0.0))
    assert bt.run(OrbShortEquity()).trades == []
