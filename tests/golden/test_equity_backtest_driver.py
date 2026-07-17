"""Golden end-to-end: EquityBacktest on hand-built synthetic days.

Every fill and P&L is hand-verified: next-open entry with slip, intrabar
level target on 1-min bars (incl. gap-through), market exit, EOD square-off,
capital sizing, and the two day-gate skip rules.
"""
from datetime import date, time

import pytest

from engine.equity.backtest import EquityBacktest, EquityKnobs
from engine.equity.costs import EquityCostModel
from engine.equity.model import EquityOrder
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit
from tests.equity_helpers import frame, synth_day

M = EquityCostModel()
SLIP = 0.0005
D1, D2 = date(2024, 1, 1), date(2024, 1, 2)


class OneShot(Strategy):
    """Enter at a fixed signal bar ON ONE DAY ONLY (default D2 — otherwise the
    flat setup day D1 would also trade); exits are scripted via ``manage_fn``."""
    def __init__(self, direction, signal_time, manage_fn=lambda ctx, pos: None,
                 capital=100_000.0, day=D2):
        self.direction, self.signal_time = direction, signal_time
        self.manage_fn, self.capital, self.day = manage_fn, capital, day

    def on_day_start(self, ctx):
        self.done = False

    def on_bar(self, ctx):
        if (not self.done and ctx.now.date() == self.day
                and ctx.now.time() == self.signal_time):
            self.done = True
            return [EquityOrder(direction=self.direction, capital=self.capital)]
        return []

    def manage(self, ctx, pos):
        return self.manage_fn(ctx, pos)


def two_flat_days(day2_overrides):
    """Day 1 establishes a prior session; day 2 carries the scenario."""
    return frame(synth_day(D1, 1700.0) + synth_day(D2, 1700.0,
                                                   overrides=day2_overrides))


def test_long_market_exit_hand_verified():
    # Fill minute 09:25 opens 1702; market exit signalled at 10:00 fills at
    # the 10:05 bar open 1712.
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702),
                        "10:05": (1712, 1712, 1712, 1712)})
    strat = OneShot("long", time(9, 20),
                    manage_fn=lambda ctx, pos:
                        Exit.market("out") if ctx.now.time() >= time(10, 0) else None)
    bt = EquityBacktest(df, M)
    r = bt.run(strat)
    assert len(r.trades) == 1
    t = r.trades[0]
    entry = 1702 * (1 + SLIP)                    # 1702.851
    exit_ = 1712 * (1 - SLIP)                    # 1711.144
    qty = int(100_000 // entry)                  # 58
    assert t.side == "LONG" and t.qty == qty == 58
    assert t.entry == pytest.approx(entry) and t.exit == pytest.approx(exit_)
    assert t.gross == pytest.approx((exit_ - entry) * qty)
    assert t.cost == pytest.approx(M.total(entry * qty, exit_ * qty))
    assert t.net == pytest.approx(t.gross - t.cost)
    assert t.reason == "out" and t.date == D2
    assert t.entry_dt.time() == time(9, 25) and t.exit_dt.time() == time(10, 5)


def test_long_intrabar_target_at_level():
    # Resting +10-ish target 1710: minute 10:07 (inside the 10:05 window)
    # trades high 1711 without opening past the level -> fill AT the level.
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702),
                        "10:07": (1708, 1711, 1707, 1710)})
    strat = OneShot("long", time(9, 20),
                    manage_fn=lambda ctx, pos: Exit.at(1710.0, "rise", "target"))
    r = EquityBacktest(df, M).run(strat)
    assert len(r.trades) == 1
    t = r.trades[0]
    assert t.exit == pytest.approx(1710.0 * (1 - SLIP))
    assert t.reason == "target"
    assert t.exit_dt.time() == time(10, 7)       # the 1-min bar that hit


def test_long_target_gap_through_fills_at_open():
    # Minute 10:07 OPENS at 1713, past the 1710 target -> honest fill at open.
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702),
                        "10:07": (1713, 1714, 1712, 1713)})
    strat = OneShot("long", time(9, 20),
                    manage_fn=lambda ctx, pos: Exit.at(1710.0, "rise", "target"))
    r = EquityBacktest(df, M).run(strat)
    assert r.trades[0].exit == pytest.approx(1713.0 * (1 - SLIP))


def test_short_eod_square_off():
    # Short fills DOWN at entry, EOD buys back UP at the last 1-min close.
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702),
                        "15:29": (1690, 1690, 1690, 1690)})
    strat = OneShot("short", time(9, 20))        # no exit -> rides to EOD
    r = EquityBacktest(df, M).run(strat)
    assert len(r.trades) == 1
    t = r.trades[0]
    entry = 1702 * (1 - SLIP)                    # short sells down: 1701.149
    exit_ = 1690 * (1 + SLIP)                    # buys back up:    1690.845
    qty = int(100_000 // entry)                  # 58
    assert t.side == "SHORT" and t.qty == qty
    assert t.entry == pytest.approx(entry) and t.exit == pytest.approx(exit_)
    assert t.gross == pytest.approx((entry - exit_) * qty)
    assert t.reason == "eod" and t.exit_dt.time() == time(15, 29)


def test_short_intrabar_target_falls_to_level():
    # Short target = price FALLING to 1690 ("fall" trigger).
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702),
                        "11:03": (1692, 1693, 1689, 1690)})
    strat = OneShot("short", time(9, 20),
                    manage_fn=lambda ctx, pos: Exit.at(1690.0, "fall", "target"))
    r = EquityBacktest(df, M).run(strat)
    t = r.trades[0]
    assert t.exit == pytest.approx(1690.0 * (1 + SLIP))
    assert t.reason == "target" and t.exit_dt.time() == time(11, 3)


def test_min_bars_day_skipped():
    short_day = synth_day(D2, 1700.0, minutes=100)       # Muhurat-like stub
    df = frame(synth_day(D1, 1700.0) + short_day)
    bt = EquityBacktest(df, M)
    r = bt.run(OneShot("long", time(9, 20)))
    assert [t for t in r.trades if t.date == D2] == []
    assert (D2, "min_bars") in bt.skipped_days


def test_overnight_gap_day_skipped():
    # Day 2 opens 2x day 1's close (unadjusted 1:1 bonus) -> skip.
    df = frame(synth_day(D1, 1700.0) + synth_day(D2, 850.0))
    bt = EquityBacktest(df, M)
    r = bt.run(OneShot("long", time(9, 20)))
    assert [t for t in r.trades if t.date == D2] == []
    assert (D2, "overnight_gap") in bt.skipped_days


def test_partial_exit_fraction_rejected():
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702)})
    strat = OneShot("long", time(9, 20),
                    manage_fn=lambda ctx, pos: Exit.at(1710.0, "rise", "t",
                                                       fraction=0.5))
    with pytest.raises(ValueError):
        EquityBacktest(df, M).run(strat)


def test_qty_zero_when_capital_too_small():
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702)})
    strat = OneShot("long", time(9, 20), capital=1000.0)  # < 1 share
    r = EquityBacktest(df, M).run(strat)
    assert r.trades == []


def test_range_scaled_slippage_charges_fast_fill_minutes():
    # fill minute 09:25 spans 1692-1712 (mid 1702, range 20): with k=0.1 the
    # entry slip becomes 0.0005 + 0.1*20/1702; the flat-slip default is
    # unchanged by construction (k=0)
    df = two_flat_days({"09:25": (1702.0, 1712.0, 1692.0, 1702.0),
                        "10:05": (1712.0, 1712.0, 1712.0, 1712.0)})
    strat = lambda: OneShot("long", time(9, 20),
                            manage_fn=lambda ctx, pos:
                                Exit.market("out") if ctx.now.time() >= time(10, 0) else None)
    r = EquityBacktest(df, M, EquityKnobs(slippage_range_k=0.1)).run(strat())
    s_entry = 0.0005 + 0.1 * 20.0 / 1702.0
    assert r.trades[0].entry == pytest.approx(1702.0 * (1 + s_entry))
    assert r.trades[0].exit == pytest.approx(1712.0 * (1 - 0.0005))  # flat exit minute

    r = EquityBacktest(df, M).run(strat())                           # k off -> flat
    assert r.trades[0].entry == pytest.approx(1702.0 * (1 + 0.0005))


def test_entry_delay_bars_fills_one_bar_later():
    # signal 09:20 normally fills at the 09:25 open (1702); delayed one bar it
    # fills at the 09:30 open (1706) instead
    df = two_flat_days({"09:25": (1702.0, 1702.0, 1702.0, 1702.0),
                        "09:30": (1706.0, 1706.0, 1706.0, 1706.0)})
    strat = lambda: OneShot("long", time(9, 20))
    r = EquityBacktest(df, M, EquityKnobs(entry_delay_bars=1)).run(strat())
    assert r.trades[0].entry == pytest.approx(1706.0 * (1 + SLIP))
    assert r.trades[0].entry_dt.time() == time(9, 30)

    r = EquityBacktest(df, M).run(strat())
    assert r.trades[0].entry == pytest.approx(1702.0 * (1 + SLIP))


def test_multiple_orders_per_bar_rejected():
    # Equity v1 is single-position: two orders on one bar must fail loud,
    # not silently drop the second.
    class TwoShot(OneShot):
        def on_bar(self, ctx):
            orders = super().on_bar(ctx)
            return orders * 2 if orders else []

    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702)})
    with pytest.raises(ValueError):
        EquityBacktest(df, M).run(TwoShot("long", time(9, 20)))
