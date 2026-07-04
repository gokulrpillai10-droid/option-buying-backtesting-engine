"""ATRROCBuy: ATR/ROC indicators, high-vol gate, ROC-direction OTM entry."""
from datetime import datetime

import pandas as pd
import pytest

from engine.data.spot import SpotSeries
from engine.strategy.base import BarContext
from strategies.atr_roc_buy import ATRROCBuy, atr, roc


def _series(closes, rng):
    """Build a 5-min SpotSeries: each bar high=close+rng/2, low=close-rng/2."""
    rows = []
    t = datetime(2024, 1, 2, 9, 15)
    for k, c in enumerate(closes):
        dt = t + pd.Timedelta(minutes=5 * k)
        rows.append((dt, c, c + rng / 2, c - rng / 2, c))
    return SpotSeries(pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]))


def test_atr_and_roc_math():
    bars = _series([100, 110, 120, 130], rng=20).window(datetime(2024, 1, 2, 9, 30), 4)
    # TRs: each bar range 20, plus gaps of 10 -> TR ~ max(20, |h-pc|...) = 25 here
    assert atr(bars, 3) == pytest.approx(
        (max(20, abs(110 + 10 - 100), abs(110 - 10 - 100))
         + max(20, abs(120 + 10 - 110), abs(120 - 10 - 110))
         + max(20, abs(130 + 10 - 120), abs(130 - 10 - 120))) / 3)
    assert roc(bars, 3) == pytest.approx((130 - 100) / 100 * 100)   # +30%


def test_no_entry_when_atr_below_threshold():
    s = ATRROCBuy(atr_period=14, roc_period=12, atr_min=50)
    spot = _series([20000 + i for i in range(20)], rng=10)          # tiny ranges -> low ATR
    s.on_day_start(BarContext(now=datetime(2024, 1, 2, 9, 15), chain=None, spot=spot))
    dt = spot.timestamps()[-1].to_pydatetime()
    assert s.on_bar(BarContext(now=dt, chain=None, spot=spot)) == []


def test_high_atr_positive_roc_buys_otm_call():
    s = ATRROCBuy(atr_period=14, roc_period=12, atr_min=50, lots=1)
    closes = [20000 + 10 * i for i in range(20)]                    # rising -> ROC > 0
    spot = _series(closes, rng=70)                                  # big ranges -> ATR > 50
    s.on_day_start(BarContext(now=datetime(2024, 1, 2, 9, 15), chain=None, spot=spot))
    dt = spot.timestamps()[-1].to_pydatetime()
    orders = s.on_bar(BarContext(now=dt, chain=None, spot=spot))
    assert len(orders) == 1 and orders[0].side == "CALL"
    last_close = closes[-1]                                         # 20190 -> atm 20200 -> OTM 20250
    assert orders[0].strike == int(round(last_close / 50.0)) * 50 + 50


def test_high_atr_negative_roc_buys_otm_put():
    s = ATRROCBuy(atr_period=14, roc_period=12, atr_min=50, lots=1)
    closes = [20000 - 10 * i for i in range(20)]                    # falling -> ROC < 0
    spot = _series(closes, rng=70)
    s.on_day_start(BarContext(now=datetime(2024, 1, 2, 9, 15), chain=None, spot=spot))
    dt = spot.timestamps()[-1].to_pydatetime()
    orders = s.on_bar(BarContext(now=dt, chain=None, spot=spot))
    assert len(orders) == 1 and orders[0].side == "PUT"
    assert orders[0].strike == int(round(closes[-1] / 50.0)) * 50 - 50
