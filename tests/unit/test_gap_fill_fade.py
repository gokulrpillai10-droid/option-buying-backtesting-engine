from datetime import datetime

from strategies.gap_fill_fade import GapFillFade
from strategies import gap_fill_params as p


class FakeQuote:
    def __init__(self, traded=True): self.is_traded = traded


class FakeSnap:
    def __init__(self, strikes): self._s = strikes
    def strikes(self): return self._s
    def call(self, k): return FakeQuote()
    def put(self, k): return FakeQuote()


class FakeChain:
    def __init__(self, strikes): self._s = strikes
    def snapshot_at(self, dt): return FakeSnap(self._s)


class _Bar:
    def __init__(self, open_, close): self.open = open_; self.close = close


class FakeCtx:
    def __init__(self, now, open_, close, pc,
                 strikes=(23900, 23950, 24000, 24050, 24100)):
        self._now = now; self._open = open_; self._close = close; self._pc = pc
        self._chain = FakeChain(list(strikes))
    @property
    def now(self): return self._now
    @property
    def spot_bar(self): return _Bar(self._open, self._close)
    def prev_close(self): return self._pc
    def snapshot(self): return self._chain.snapshot_at(self._now)


def _fade_select(settled, pc):
    s = GapFillFade(arm="fade")
    s.on_day_start(None)
    ctx = FakeCtx(datetime(2024, 6, 3, 9, 15), settled, settled, pc)
    s._record_open(ctx)
    return s._select(ctx)


def test_fade_skips_below_floor():
    assert _fade_select(24000 * (1 + p.FLOOR / 2), 24000) is None


def test_fade_skips_above_cap():
    assert _fade_select(24000 * (1 + p.CAP + 0.001), 24000) is None


def test_fade_gap_up_selects_put():
    side, _ = _fade_select(24000 * (1 + (p.FLOOR + p.CAP) / 2), 24000)
    assert side == "PUT"


def test_fade_gap_down_selects_call():
    side, _ = _fade_select(24000 * (1 - (p.FLOOR + p.CAP) / 2), 24000)
    assert side == "CALL"


def test_no_prev_close_stands_aside():
    assert _fade_select(24100, None) is None


def test_reversal_enters_call_on_reclaim():
    s = GapFillFade(arm="reversal")
    s.on_day_start(None)
    # 09:15: gap-down, day_open 23900, settled(close) 23910, pc 24000
    assert s.on_bar(FakeCtx(datetime(2024, 6, 3, 9, 15), 23900, 23910, 24000)) == []
    # 09:30: close 23950 reclaims the 23900 open -> CALL
    orders = s.on_bar(FakeCtx(datetime(2024, 6, 3, 9, 30), 23950, 23950, 24000))
    assert len(orders) == 1 and orders[0].side == "CALL"


def test_reversal_no_entry_if_not_reclaimed():
    s = GapFillFade(arm="reversal")
    s.on_day_start(None)
    s.on_bar(FakeCtx(datetime(2024, 6, 3, 9, 15), 23900, 23910, 24000))
    # 09:30 close 23880 still below the 23900 open -> no reclaim
    assert s.on_bar(FakeCtx(datetime(2024, 6, 3, 9, 30), 23880, 23880, 24000)) == []


def test_reversal_skips_gap_up():
    s = GapFillFade(arm="reversal")
    s.on_day_start(None)
    s.on_bar(FakeCtx(datetime(2024, 6, 3, 9, 15), 24100, 24100, 24000))
    assert s.on_bar(FakeCtx(datetime(2024, 6, 3, 9, 30), 24200, 24200, 24000)) == []


def test_reversal_skips_below_floor_even_if_reclaimed():
    s = GapFillFade(arm="reversal")
    s.on_day_start(None)
    settled = 24000 * (1 - p.FLOOR / 2)          # gap-down but below band
    s.on_bar(FakeCtx(datetime(2024, 6, 3, 9, 15), 23990, settled, 24000))
    # 09:30 reclaims (24010 > 23990 open) but gap below FLOOR -> no trade
    assert s.on_bar(FakeCtx(datetime(2024, 6, 3, 9, 30), 24010, 24010, 24000)) == []


def test_manage_returns_eod_after_square():
    s = GapFillFade(arm="fade")
    class Ctx:
        now = datetime(2024, 6, 3, 15, 20)
    assert s.manage(Ctx(), pos=None).reason == "eod"


def test_two_stage_wired_from_frozen_params():
    s = GapFillFade(arm="fade")
    assert s._two.target_pts == p.BOOK
    assert s._two.trail_gap == p.TRAIL
    assert s._two.stop_pts == p.STOP
    assert abs(s._two.fraction - 4 / 6) < 1e-9
