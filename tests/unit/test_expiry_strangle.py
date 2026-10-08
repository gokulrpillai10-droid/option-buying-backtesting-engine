"""Pure helpers for the near-expiry strangle backtest: premium-ceiling leg
selection (largest premium <= ceiling, >= floor) and the first-leg-to-Nx exit
that closes both legs. Expected values are hand-derived."""
from datetime import datetime

from analysis.expiry_strangle_run import pick_leg_ceiling, find_exit_firstleg


def test_pick_leg_ceiling_takes_largest_premium_at_or_below_ceiling():
    # ceiling 5: eligible traded premiums <= 5 are {4.5, 2.0, 1.1}; largest is 4.5.
    quotes = [
        (24800, 8.0, True),     # above ceiling -> excluded
        (24850, 4.5, True),     # largest <= 5  <-- winner
        (24900, 2.0, True),
        (24950, 1.1, True),
        (25000, 4.9, False),    # <= 5 but NOT traded -> excluded
    ]
    assert pick_leg_ceiling(quotes, ceiling=5.0, floor=1.0) == (24850, 4.5)


def test_pick_leg_ceiling_skips_day_when_best_is_below_floor():
    # every traded premium <= ceiling is under the Rs1 floor -> None (skip the day)
    quotes = [(25000, 0.6, True), (25050, 0.3, True), (24000, 40.0, True)]  # 40 > ceiling
    assert pick_leg_ceiling(quotes, ceiling=5.0, floor=1.0) is None


def test_pick_leg_ceiling_none_when_nothing_at_or_below_ceiling():
    assert pick_leg_ceiling([(24000, 40.0, True)], ceiling=5.0, floor=1.0) is None


def test_intrabar_high_triggers_and_winner_fills_at_target():
    # minutes: (dt, ce_high, pe_high, ce_close, pe_close). targets: CE 20, PE 10.
    ce_entry, pe_entry, mult = 2.0, 1.0, 10.0
    m = [
        (datetime(2025, 5, 22, 14, 40), 5.0, 2.0, 4.0, 1.5),    # neither high at target
        (datetime(2025, 5, 22, 14, 45), 8.0, 12.0, 7.0, 6.0),   # PE high 12 >= 10 -> hit
        (datetime(2025, 5, 22, 14, 50), 30.0, 1.0, 25.0, 0.5),
    ]
    # PE fills at its target 10.0 (not its high 12 or close 6); CE at its close 7.0
    assert find_exit_firstleg(m, ce_entry, pe_entry, mult) == (
        datetime(2025, 5, 22, 14, 45), 7.0, 10.0, "PE")


def test_high_touch_that_closes_below_target_still_counts():
    # CE high 21 reaches target 20 even though it closes at 9 (close-based would miss it)
    m = [(datetime(2025, 5, 22, 14, 40), 21.0, 3.0, 9.0, 2.0)]
    assert find_exit_firstleg(m, 2.0, 1.0, 10.0) == (
        datetime(2025, 5, 22, 14, 40), 20.0, 2.0, "CE")     # CE at target 20, PE at close


def test_no_leg_reaches_target_exits_eod_at_last_close():
    m = [(datetime(2025, 5, 22, 14, 40), 3.0, 2.0, 2.5, 1.5),
         (datetime(2025, 5, 22, 15, 25), 0.1, 0.1, 0.1, 0.05)]
    assert find_exit_firstleg(m, 2.0, 1.0, 10.0) == (
        datetime(2025, 5, 22, 15, 25), 0.1, 0.05, "eod")


def test_empty_window_is_none():
    assert find_exit_firstleg([], 2.0, 1.0, 10.0) is None
