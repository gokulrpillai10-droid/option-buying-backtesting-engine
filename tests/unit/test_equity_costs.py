"""EquityCostModel vs a hand-calculated Zerodha MIS-equity round trip."""
import pytest

from engine.equity.costs import EquityCostModel


def test_full_round_trip_hand_calc():
    # Buy 57 sh @ 1750 = 99,750 ; sell 57 sh @ 1760 = 100,320
    m = EquityCostModel()
    buy, sell = 99_750.0, 100_320.0
    brokerage = min(20.0, 0.0003 * buy) + min(20.0, 0.0003 * sell)   # 20 + 20 = 40
    stt = 0.00025 * sell                                             # 25.08
    exch = 0.0000297 * (buy + sell)                                  # 5.9420790
    sebi = 0.000001 * (buy + sell)                                   # 0.20007
    stamp = 0.00003 * buy                                            # 2.9925
    gst = 0.18 * (brokerage + exch + sebi)                           # 8.30558682
    expected = brokerage + stt + exch + sebi + stamp + gst           # 82.52023582
    assert m.total(buy, sell) == pytest.approx(expected)
    assert m.total(buy, sell) == pytest.approx(82.52023582)


def test_small_trade_brokerage_below_cap():
    # 1 share: 0.03% of value < Rs20, so percentage brokerage applies per order.
    m = EquityCostModel()
    buy, sell = 1000.0, 1010.0
    brokerage = 0.0003 * buy + 0.0003 * sell                         # 0.603
    stt = 0.00025 * sell
    exch = 0.0000297 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.00003 * buy
    gst = 0.18 * (brokerage + exch + sebi)
    assert m.total(buy, sell) == pytest.approx(
        brokerage + stt + exch + sebi + stamp + gst)
    assert m.total(buy, sell) == pytest.approx(1.06685426)


def test_stt_is_sell_side_only():
    m = EquityCostModel()
    # Same turnover split differently: more sell value => strictly more STT.
    assert m.total(50_000.0, 150_000.0) > m.total(150_000.0, 50_000.0)
