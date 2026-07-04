"""ORBSpread: opening-range breakout traded with vertical debit spreads."""
from datetime import datetime

import pandas as pd
import pytest

from engine.data.spot import SpotSeries
from engine.data.chain import Chain
from engine.strategy.base import BarContext
from engine.position.manager import SpreadPosition, SpreadLeg
from strategies.orb_spread import ORBSpread
from tests.conftest import make_row, CHAIN_COLUMNS


def _spot():
    rows = [
        ("2024-01-02 09:15", 20050, 20100, 20000, 20050),   # OR: high 20100, low 20000
        ("2024-01-02 09:40", 20060, 20090, 20030, 20070),   # inside the range
        ("2024-01-02 09:45", 20120, 20160, 20110, 20150),   # 5-min close 20150 > OR-high -> bull
    ]
    return SpotSeries(pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"])
                      .assign(DateTime=lambda d: pd.to_datetime(d["DateTime"])))


def _ctx(spot, dt, chain=None):
    return BarContext(now=dt, chain=chain, spot=spot)


def test_builds_opening_range_then_no_entry_inside_window():
    s, spot = ORBSpread(width=300, lots=3), _spot()
    s.on_day_start(_ctx(spot, datetime(2024, 1, 2, 9, 15)))
    assert s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 15))) == []   # building OR
    assert s.or_high == 20100 and s.or_low == 20000


def test_bull_breakout_deploys_bull_call_spread():
    s, spot = ORBSpread(width=300, lots=3), _spot()
    s.on_day_start(_ctx(spot, datetime(2024, 1, 2, 9, 15)))
    s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 15)))                # build OR
    s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 40)))                # inside
    orders = s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 45)))       # close 20150 > 20100
    assert len(orders) == 2
    buy, sell = orders
    assert buy.action == "buy" and buy.side == "CALL" and buy.strike == 20150   # ATM
    assert sell.action == "sell" and sell.side == "CALL" and sell.strike == 20450  # ATM+300
    assert buy.lots == 3 and sell.lots == 3
    assert s.entered and s.on_bar(_ctx(spot, datetime(2024, 1, 2, 9, 50))) == []  # one trade/day


def test_manage_exits_on_combined_pnl_target_and_stop():
    s = ORBSpread(width=300, lots=3, target=9000, stop=3000)
    pos = SpreadPosition(legs=(
        SpreadLeg("buy", "CALL", 20150, entry=150.0, qty=225),
        SpreadLeg("sell", "CALL", 20450, entry=50.0, qty=225),
    ))
    # price the legs so combined P&L >= +9000 -> target. long +X, short flat:
    # need unrealized >= 9000 over 225 qty -> +40/unit. long 150->200 (+50), short 50->55 (-5) = +45/u
    rows = [make_row(datetime(2024, 1, 2, 11, 0), 20150, call_open=200, call_close=200),
            make_row(datetime(2024, 1, 2, 11, 0), 20450, call_open=55, call_close=55)]
    chain = Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))
    ex = s.manage(_ctx(None, datetime(2024, 1, 2, 11, 0), chain), pos)
    assert ex.kind == "market" and ex.reason == "target"

    rows2 = [make_row(datetime(2024, 1, 2, 11, 0), 20150, call_open=130, call_close=130),
             make_row(datetime(2024, 1, 2, 11, 0), 20450, call_open=48, call_close=48)]
    ex2 = s.manage(_ctx(None, datetime(2024, 1, 2, 11, 0),
                        Chain(pd.DataFrame(rows2, columns=CHAIN_COLUMNS))), pos)
    # long 150->130 (-20), short 50->48 (+2) = -18/u * 225 = -4050 <= -3000 -> stop
    assert ex2.kind == "market" and ex2.reason == "stop"


def test_eod_squares_off():
    s = ORBSpread()
    pos = SpreadPosition(legs=(SpreadLeg("buy", "CALL", 20150, 150.0, 225),
                               SpreadLeg("sell", "CALL", 20450, 50.0, 225)))
    ex = s.manage(_ctx(None, datetime(2024, 1, 2, 15, 25)), pos)
    assert ex.kind == "market" and ex.reason == "eod"
