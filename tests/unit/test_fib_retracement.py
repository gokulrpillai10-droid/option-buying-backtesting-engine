"""TDD: FibRetracement entry conditions on synthetic spot bars.

Scenario = an up-impulse in the 09:15-10:00 window, a retrace into the 50-61.8%
zone, then a reclaim of the 38.2% level (the bounce that should fire a CALL).
"""
from datetime import datetime

import pandas as pd

from tests.conftest import make_row, CHAIN_COLUMNS
from engine.data.chain import Chain
from engine.data.spot import SpotSeries
from engine.strategy.base import BarContext
from strategies.fib_retracement import FibRetracement

DAY = (2024, 1, 2)

# (hh, mm, open, high, low, close) — up-impulse 19900 -> 20100, then retrace/reclaim
_BARS = [
    (9, 15, 19900, 19910, 19900, 19905),   # session LOW (earliest)
    (9, 20, 19905, 19960, 19905, 19955),
    (9, 25, 19955, 20000, 19950, 19995),
    (9, 30, 19995, 20030, 19990, 20025),
    (9, 35, 20025, 20060, 20020, 20055),
    (9, 40, 20055, 20080, 20050, 20075),
    (9, 45, 20075, 20095, 20070, 20090),
    (9, 50, 20090, 20098, 20085, 20095),
    (9, 55, 20095, 20100, 20090, 20099),   # session HIGH (latest -> up-impulse)
    (10, 0, 20099, 20099, 19985, 19990),   # retrace: close 19990 in zone [19976.4, 20000]
    (10, 5, 19990, 20035, 19988, 20030),   # reclaim: 20030 > 38.2% level 20023.6 -> ENTER
]


def _spot():
    rows = [(datetime(*DAY, hh, mm), o, h, l, c) for (hh, mm, o, h, l, c) in _BARS]
    return SpotSeries(pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]))


def _chain():
    rows = [make_row(datetime(*DAY, hh, mm), 20000, call_open=120.0, put_open=120.0)
            for (hh, mm) in ((10, 0), (10, 5))]
    return Chain(pd.DataFrame(rows, columns=CHAIN_COLUMNS))


def _run():
    spot, chain = _spot(), _chain()
    s = FibRetracement()
    s.on_day_start(BarContext(now=datetime(*DAY, 9, 15), chain=chain, spot=spot))
    out = []
    for (hh, mm, *_rest) in _BARS:
        orders = s.on_bar(BarContext(now=datetime(*DAY, hh, mm), chain=chain, spot=spot))
        out.append(((hh, mm), orders))
    return out


def test_enters_call_on_the_reclaim_bar_only():
    entries = [(hm, o) for hm, o in _run() if o]
    assert len(entries) == 1                       # exactly one entry, on the reclaim
    (hm, orders) = entries[0]
    assert hm == (10, 5)
    assert orders[0].side == "CALL" and orders[0].strike == 20000 and orders[0].lots == 6


def test_no_entry_before_formation_window():
    for hm, o in _run():
        if hm[0] == 9:                             # nothing before 10:00
            assert o == []


def test_small_impulse_day_stands_aside():
    # a flat morning (range < 0.3% of spot) must produce no trade even with a wiggle
    flat = [(9, 15 + 5 * i, 20000, 20010, 19995, 20005) for i in range(9)]
    flat += [(10, 0, 20005, 20005, 19999, 20001), (10, 5, 20001, 20040, 20000, 20035)]
    rows = [(datetime(*DAY, hh, mm), o, h, l, c) for (hh, mm, o, h, l, c) in flat]
    spot = SpotSeries(pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]))
    chain = _chain()
    s = FibRetracement()
    s.on_day_start(BarContext(now=datetime(*DAY, 9, 15), chain=chain, spot=spot))
    got = [s.on_bar(BarContext(now=datetime(*DAY, hh, mm), chain=chain, spot=spot))
           for (hh, mm, *_r) in flat]
    assert all(o == [] for o in got)               # impulse < MIN_IMPULSE -> no trade
