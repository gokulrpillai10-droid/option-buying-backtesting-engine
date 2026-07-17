"""Data-integrity checks: each detector on a tiny synthetic day frame."""
from datetime import datetime

import pandas as pd

from analysis.data_integrity import (THRESHOLDS, day_flags, ghost_prints,
                                     intrinsic_violations, ohlc_violations,
                                     verdict)
from tests.conftest import CHAIN_COLUMNS, make_row


def _frame(rows):
    return pd.DataFrame(rows, columns=CHAIN_COLUMNS)


def test_intrinsic_violation_detected_against_forward():
    t = datetime(2024, 1, 2, 9, 15)
    # forward 20000 (make_row default): the 19800 CALL is 200 ITM; closing at
    # 100 is an arb violation (tolerance ~26); closing at 195 is fine
    bad = _frame([make_row(t, 19800, call_close=100.0)])
    ok = _frame([make_row(t, 19800, call_close=195.0)])
    assert intrinsic_violations(bad) == 1
    assert intrinsic_violations(ok) == 0
    # non-traded strikes are ignored (stale placeholder rows)
    stale = _frame([make_row(t, 19800, call_close=100.0, call_traded=False)])
    assert intrinsic_violations(stale) == 0


def test_ohlc_violation_detected():
    t = datetime(2024, 1, 2, 9, 15)
    bad = _frame([make_row(t, 20000, call_open=100.0, call_high=90.0,
                           call_low=85.0, call_close=88.0)])   # high < open
    ok = _frame([make_row(t, 20000, call_open=100.0, call_high=101.0,
                          call_low=99.0, call_close=100.0)])
    assert ohlc_violations(bad) == 1
    assert ohlc_violations(ok) == 0


def test_ghost_print_detected():
    ts = [datetime(2024, 1, 2, 9, 15 + i) for i in range(3)]
    # 100 -> 30 (-70%, a print) -> 95 (instant full retrace)
    ghost = _frame([make_row(ts[0], 20000, call_close=100.0),
                    make_row(ts[1], 20000, call_close=30.0),
                    make_row(ts[2], 20000, call_close=95.0)])
    # 100 -> 130 -> 128: a real move that sticks
    real = _frame([make_row(ts[0], 20000, call_close=100.0),
                   make_row(ts[1], 20000, call_close=130.0),
                   make_row(ts[2], 20000, call_close=128.0)])
    assert ghost_prints(ghost) >= 1
    assert ghost_prints(real) == 0


def test_verdict_thresholds_and_coverage():
    clean = {"intrinsic": 0, "ohlc": 0, "ghost": 0, "minutes": 375}
    assert verdict(clean) == []
    assert "intrinsic" in verdict({**clean, "intrinsic": THRESHOLDS["intrinsic"]})
    assert "coverage" in verdict({**clean, "minutes": 100})
    # ghost is advisory: reported, but never a quarantine reason on its own
    assert verdict({**clean, "ghost": 500}) == []


def test_day_flags_shape():
    t = datetime(2024, 1, 2, 9, 15)
    flags = day_flags(_frame([make_row(t, 20000)]))
    assert set(flags) == {"intrinsic", "ohlc", "ghost", "minutes"}
    assert flags["minutes"] == 1
