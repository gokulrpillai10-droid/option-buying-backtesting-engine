"""Golden tests for the India F&O cost model.

Values are hand-computed from the published rate card so the model is
verifiable by inspection, not just by re-running the code.
"""
import pytest

from engine.execution.costs import CostModel


def test_default_costs_match_hand_computation():
    # buy 100 premium x 65 qty = 6500 ; exit 120 x 65 = 7800 ; round trip = 2 orders
    cm = CostModel()
    # brok 40 + stt 7.8 + exch 5.00929 + sebi 0.0143 + stamp 0.195 + gst 8.1042462
    assert cm.total(buy_value=6500.0, sell_value=7800.0, n_orders=2) == pytest.approx(61.1228362)


def test_zero_turnover_is_brokerage_plus_gst_only():
    cm = CostModel()
    # only brokerage (40) and GST on it (7.2) survive when there is no turnover
    assert cm.total(buy_value=0.0, sell_value=0.0, n_orders=2) == pytest.approx(47.2)


def test_stt_charged_on_sell_side_only():
    cm = CostModel()
    # swapping buy/sell turnover changes the total because STT & stamp are one-sided
    a = cm.total(buy_value=6500.0, sell_value=7800.0, n_orders=2)
    b = cm.total(buy_value=7800.0, sell_value=6500.0, n_orders=2)
    assert a != pytest.approx(b)


def test_more_orders_cost_more_brokerage():
    cm = CostModel()
    two = cm.total(buy_value=6500.0, sell_value=7800.0, n_orders=2)
    three = cm.total(buy_value=6500.0, sell_value=7800.0, n_orders=3)
    # one extra order = +20 brokerage + 18% GST on it = +23.6
    assert three - two == pytest.approx(23.6)


def test_rates_are_configurable():
    cm = CostModel(brokerage_per_order=0.0, gst=0.0)
    # no brokerage, no GST -> only turnover-based statutory charges remain
    # stt 7.8 + exch 5.00929 + sebi 0.0143 + stamp 0.195
    assert cm.total(buy_value=6500.0, sell_value=7800.0, n_orders=2) == pytest.approx(13.01859)


def test_exercise_stt_on_intrinsic_not_spot():
    """ITM exercise STT = 0.15% of intrinsic value (settlement), NOT spot × qty.
    Call strike 24000, spot 24200: intrinsic = 200 × 75 = 15000; STT = Rs 22.50."""
    cm = CostModel()
    intrinsic_value = 200 * 75   # (spot - strike) × qty
    assert cm.exercise_stt(intrinsic_value) == pytest.approx(22.50)


def test_exercise_stt_rate_is_configurable():
    cm = CostModel(exercise_stt_rate=0.001)
    assert cm.exercise_stt(15000) == pytest.approx(15.0)
