from datetime import datetime, time

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


class FakeCtx:
    """Minimal BarContext stand-in for selection tests."""
    def __init__(self, now, settled, pc, strikes=(23900, 23950, 24000, 24050, 24100)):
        self._now = now; self._settled = settled; self._pc = pc
        self._chain = FakeChain(list(strikes))
    @property
    def now(self): return self._now
    @property
    def spot_bar(self):
        class B: pass
        b = B(); b.open = self._settled; b.high = self._settled
        b.low = self._settled; b.close = self._settled; return b
    def prev_close(self): return self._pc
    def snapshot(self): return self._chain.snapshot_at(self._now)


def _ctx(settled, pc):
    return FakeCtx(datetime(2024, 6, 3, 9, 15), settled, pc)


def test_fade_skips_below_floor():
    # gap = FLOOR/2 -> below floor -> no trade
    s = GapFillFade(arm="fade")
    settled = 24000 * (1 + p.FLOOR / 2)
    assert s._select(_ctx(settled, 24000)) is None


def test_fade_skips_above_cap():
    s = GapFillFade(arm="fade")
    settled = 24000 * (1 + p.CAP + 0.001)
    assert s._select(_ctx(settled, 24000)) is None


def test_fade_gap_up_selects_put():
    s = GapFillFade(arm="fade")
    settled = 24000 * (1 + (p.FLOOR + p.CAP) / 2)     # in band, gap up
    side, strike = s._select(_ctx(settled, 24000))
    assert side == "PUT"


def test_fade_gap_down_selects_call():
    s = GapFillFade(arm="fade")
    settled = 24000 * (1 - (p.FLOOR + p.CAP) / 2)     # in band, gap down
    side, strike = s._select(_ctx(settled, 24000))
    assert side == "CALL"


def test_no_prev_close_stands_aside():
    s = GapFillFade(arm="fade")
    assert s._select(_ctx(24100, None)) is None


def test_manage_returns_eod_after_square():
    s = GapFillFade(arm="fade")
    class Ctx:
        now = datetime(2024, 6, 3, 15, 20)
    ex = s.manage(Ctx(), pos=None)
    assert ex.reason == "eod"


def test_two_stage_wired_from_frozen_params():
    s = GapFillFade(arm="fade")
    assert s._two.target_pts == p.BOOK
    assert s._two.trail_gap == p.TRAIL
    assert s._two.stop_pts == p.STOP
    assert abs(s._two.fraction - 4 / 6) < 1e-9
