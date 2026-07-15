"""GapDriveShortEquity decision/exit paths on synthetic sessions (5min tf).

Day1 = flat 1000 warm-up (prev_close). Day2 opens gapped down at 990 (-1.0%);
the 09:25 bar close is the 09:30 checkpoint. Drive threshold 0.25% of the
open (990) needs checkpoint close <= 987.53.
"""
from datetime import date

import pytest

from engine.equity.backtest import EquityBacktest, EquityKnobs
from engine.equity.costs import EquityCostModel
from strategies.gap_drive_short_equity import GapDriveShortEquity
from tests.equity_helpers import frame, synth_day

D1, D2 = date(2026, 3, 2), date(2026, 3, 3)


def run(day2_base, overrides, **kw):
    df = frame(synth_day(D1, 1000.0) + synth_day(D2, day2_base, overrides))
    bt = EquityBacktest(df, EquityCostModel(),
                        EquityKnobs(timeframe="5min", slippage_pct=0.0))
    return bt.run(GapDriveShortEquity(**kw)).trades


def pin(h0, m0, px, until=(15, 29)):
    return {f"{h:02d}:{m:02d}": (px, px, px, px)
            for h in range(9, 16) for m in range(60)
            if (h, m) >= (h0, m0) and (h, m) <= until and (h, m) >= (9, 15)}


def test_driving_gap_down_shorts_and_rides_to_eod():
    # gap -1.0%; from 09:25 pinned at 985 (drive -0.51% < -0.25%); 12:00 -> 975
    ov = {**pin(9, 25, 985.0), **pin(12, 0, 975.0)}
    trades = run(990.0, ov)
    assert len(trades) == 1
    t = trades[0]
    assert t.side == "SHORT" and t.reason == "eod"
    assert t.entry == pytest.approx(985.0)      # 09:30 bar open fill
    assert t.exit == pytest.approx(975.0)


def test_reclaimed_gap_is_skipped():
    ov = pin(9, 20, 995.0)                       # above the 990 open by 09:25
    assert run(990.0, ov) == []


def test_weak_drive_is_skipped():
    ov = pin(9, 25, 989.0)                       # -0.10% drive < 0.25% thr
    assert run(990.0, ov) == []


def test_gap_too_small_or_too_big_is_skipped():
    assert run(997.0, pin(9, 25, 990.0)) == []   # -0.3% gap < 0.5% min
    assert run(980.0, pin(9, 25, 970.0)) == []   # -2.0% gap > 1.5% max


def test_gap_up_is_skipped():
    assert run(1010.0, pin(9, 25, 1000.0)) == []


def test_or_stop_on_recovery_through_or_high():
    # OR high = 990 (open); drive to 985, then 11:00 spikes to 992
    ov = {**pin(9, 25, 985.0), "11:00": (992, 992, 992, 992),
          **pin(11, 1, 985.0)}
    trades = run(990.0, ov)
    assert len(trades) == 1
    t = trades[0]
    assert t.reason == "or_stop"
    assert t.exit == pytest.approx(992.0)        # gap-through: worse open
    assert t.gross < 0


def test_fixed_qty_sizing():
    ov = {**pin(9, 25, 985.0), **pin(12, 0, 975.0)}
    trades = run(990.0, ov, qty=500)
    assert trades[0].qty == 500
    assert trades[0].gross == pytest.approx((985.0 - 975.0) * 500)
