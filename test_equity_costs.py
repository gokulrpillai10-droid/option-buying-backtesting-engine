"""Tests for engine/equity/costs.py — EquityCostModel (Zerodha MIS rate card)."""
import pytest
from engine.equity.costs import EquityCostModel


@pytest.fixture
def model():
    return EquityCostModel()


def test_brokerage_below_cap_is_percentage(model):
    # Rs 10 000 order → 0.03% = Rs 3, well below the Rs 20 cap
    cost = model.total(buy_value=10_000, sell_value=10_000)
    expected_brokerage = 2 * 0.0003 * 10_000  # Rs 6
    assert cost == pytest.approx(
        expected_brokerage
        + model.stt_sell * 10_000
        + model.exchange * 20_000
        + model.sebi * 20_000
        + model.stamp_buy * 10_000
        + model.gst * (expected_brokerage + model.exchange * 20_000 + model.sebi * 20_000),
        rel=1e-9,
    )


def test_brokerage_caps_at_20_per_leg(model):
    # Rs 1 000 000 order → 0.03% = Rs 300, capped at Rs 20 per leg
    cost = model.total(buy_value=1_000_000, sell_value=1_000_000)
    capped_brokerage = 2 * 20.0
    assert cost == pytest.approx(
        capped_brokerage
        + model.stt_sell * 1_000_000
        + model.exchange * 2_000_000
        + model.sebi * 2_000_000
        + model.stamp_buy * 1_000_000
        + model.gst * (capped_brokerage + model.exchange * 2_000_000 + model.sebi * 2_000_000),
        rel=1e-9,
    )


def test_stt_is_sell_side_only_isolated():
    cm = EquityCostModel(
        brokerage_pct=0.0, brokerage_cap=0.0,
        exchange=0.0, sebi=0.0, stamp_buy=0.0, gst=0.0,
    )
    buy_value, sell_value = 90000.0, 50000.0
    result = cm.total(buy_value, sell_value)
    assert result == pytest.approx(0.00025 * sell_value, rel=1e-6)
    assert result != pytest.approx(0.00025 * buy_value)


def test_stamp_duty_is_buy_side_only_isolated():
    cm = EquityCostModel(
        brokerage_pct=0.0, brokerage_cap=0.0,
        stt_sell=0.0, exchange=0.0, sebi=0.0, gst=0.0,
    )
    buy_value, sell_value = 50000.0, 90000.0
    result = cm.total(buy_value, sell_value)
    assert result == pytest.approx(0.00003 * buy_value, rel=1e-6)
    assert result != pytest.approx(0.00003 * sell_value)


def test_gst_excludes_stt_and_stamp(model):
    # GST base = brokerage + exchange + sebi (NOT stt, NOT stamp)
    buy = sell = 50_000
    brokerage = 2 * min(model.brokerage_cap, model.brokerage_pct * buy)
    exch = model.exchange * (buy + sell)
    sebi = model.sebi * (buy + sell)
    expected_gst = model.gst * (brokerage + exch + sebi)

    cost = model.total(buy_value=buy, sell_value=sell)
    manual = (
        brokerage
        + model.stt_sell * sell
        + exch
        + sebi
        + model.stamp_buy * buy
        + expected_gst
    )
    assert cost == pytest.approx(manual, rel=1e-9)


def test_zero_value_trade_costs_nothing(model):
    assert model.total(buy_value=0, sell_value=0) == pytest.approx(0.0)


def test_cost_is_positive_for_any_nonzero_trade(model):
    assert model.total(buy_value=1_000, sell_value=1_000) > 0


def test_asymmetric_buy_sell_values(model):
    # buy != sell (e.g. partial fill or price moved) — should not raise
    cost = model.total(buy_value=80_000, sell_value=82_000)
    assert cost > 0


def test_default_rates_match_zerodha_mis_card():
    m = EquityCostModel()
    assert m.brokerage_pct == pytest.approx(0.0003)
    assert m.brokerage_cap == pytest.approx(20.0)
    assert m.stt_sell == pytest.approx(0.00025)
    assert m.exchange == pytest.approx(0.0000297)
    assert m.sebi == pytest.approx(0.000001)
    assert m.stamp_buy == pytest.approx(0.00003)
    assert m.gst == pytest.approx(0.18)


def test_custom_rates_are_respected():
    m = EquityCostModel(brokerage_pct=0.0, brokerage_cap=20.0, stt_sell=0.0,
                        exchange=0.0, sebi=0.0, stamp_buy=0.0, gst=0.0)
    assert m.total(buy_value=100_000, sell_value=100_000) == pytest.approx(0.0)
