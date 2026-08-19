"""Pure daily open-interest features for the regime OI triage.

Snapshot-in / feature-out, on the same duck-typed chain snapshot the straddle
helpers use (``strikes()`` + ``call(k).oi`` / ``put(k).oi``), so the identical
functions run on a Data-v3 EOD snapshot or a live Kite snapshot. These pin PCR,
max-pain, OI walls (support/resistance), and the 4-quadrant buildup classifier.
"""
import math

from analysis.oi_features import pcr, max_pain, oi_walls, classify_buildup


class _Q:
    def __init__(self, oi):
        self.oi, self.is_traded = oi, True


class _Snap:
    """Duck-typed chain snapshot: strike -> OI maps for calls/puts. Mirrors the
    REAL OptionChainSnapshot by RAISING KeyError on an absent strike (the toy must
    not silently return 0, or it hides the exact bug the band-PCR guard fixes)."""
    def __init__(self, calls, puts):
        self._c, self._p = dict(calls), dict(puts)

    def strikes(self):
        return sorted(set(self._c) | set(self._p))

    def call(self, k):
        if k not in self._c and k not in self._p:
            raise KeyError(k)
        return _Q(self._c.get(k, 0))

    def put(self, k):
        if k not in self._c and k not in self._p:
            raise KeyError(k)
        return _Q(self._p.get(k, 0))


def test_pcr_band_past_the_listed_chain_does_not_crash():
    # a band strike absent from the chain must contribute 0 OI, not raise KeyError
    snap = _Snap(calls={100: 300}, puts={100: 150})
    assert pcr(snap, strikes=[90, 100, 110]) == 150 / 300


def test_pcr_is_put_over_call_oi():
    snap = _Snap(calls={100: 300, 110: 0}, puts={110: 0, 120: 100})
    assert pcr(snap) == 100 / 300


def test_pcr_restricted_to_a_strike_band():
    snap = _Snap(calls={100: 300, 120: 100}, puts={100: 50, 120: 400})
    # only the 120 strike -> 400/100
    assert pcr(snap, strikes=[120]) == 4.0


def test_pcr_zero_call_oi_is_nan_not_inf():
    snap = _Snap(calls={100: 0}, puts={100: 500})
    assert math.isnan(pcr(snap))


def test_max_pain_is_the_min_total_payout_strike():
    # calls piled at 100, puts at 120 (asymmetric) -> payout minimized at 100
    snap = _Snap(calls={100: 300, 110: 0, 120: 0}, puts={100: 0, 110: 0, 120: 100})
    # P=100: 0 + 20*100 = 2000 ; P=110: 10*300 + 10*100 = 4000 ; P=120: 20*300 = 6000
    assert max_pain(snap) == 100


def test_oi_walls_are_max_call_and_max_put_oi_strikes():
    snap = _Snap(calls={100: 300, 110: 50, 120: 10}, puts={100: 20, 110: 40, 120: 400})
    call_wall, put_wall = oi_walls(snap)
    assert call_wall == 100     # resistance (most call OI)
    assert put_wall == 120      # support (most put OI)


def test_oi_walls_none_when_no_oi():
    snap = _Snap(calls={100: 0}, puts={100: 0})
    assert oi_walls(snap) == (None, None)


def test_classify_buildup_all_four_quadrants():
    assert classify_buildup(d_call_oi=50, d_put_oi=50, price_ret=0.01) == "long_buildup"
    assert classify_buildup(d_call_oi=-50, d_put_oi=-30, price_ret=0.01) == "short_covering"
    assert classify_buildup(d_call_oi=40, d_put_oi=40, price_ret=-0.01) == "short_buildup"
    assert classify_buildup(d_call_oi=-40, d_put_oi=-40, price_ret=-0.01) == "long_unwinding"


def test_classify_buildup_flat_on_zero_price_or_zero_oi_change():
    assert classify_buildup(0, 0, 0.01) == "flat"       # no net OI change
    assert classify_buildup(50, 50, 0.0) == "flat"      # no price move
