"""Pure vol-surface factors for the second (options-structure) regime detector:
ATM IV, delta-strike selection, put/call skew (risk reversal), and VRP. Snapshot-in
(duck-typed: strikes()/call(k).iv/.delta), so it runs on Data-v3 and Kite alike."""
import math

from analysis.vol_surface_features import (
    atm_iv, strike_nearest_delta, skew, vrp,
)


class _Q:
    def __init__(self, iv, delta):
        self.iv, self.delta, self.is_traded = iv, delta, True


class _Snap:
    """iv shared per strike (the Data-v3 IV column); call/put deltas differ."""
    def __init__(self, ivs, cdelta, pdelta):
        self._iv, self._cd, self._pd = dict(ivs), dict(cdelta), dict(pdelta)

    def strikes(self):
        return sorted(self._iv)

    def call(self, k):
        if k not in self._iv:
            raise KeyError(k)
        return _Q(self._iv[k], self._cd[k])

    def put(self, k):
        if k not in self._iv:
            raise KeyError(k)
        return _Q(self._iv[k], self._pd[k])


# strikes 19800..20200; IV smile (higher on the wings); deltas roughly monotone
_IV = {19800: 0.19, 19900: 0.17, 20000: 0.15, 20100: 0.155, 20200: 0.165}
_CD = {19800: 0.80, 19900: 0.65, 20000: 0.50, 20100: 0.25, 20200: 0.12}
_PD = {19800: -0.12, 19900: -0.25, 20000: -0.50, 20100: -0.72, 20200: -0.85}
_SNAP = _Snap(_IV, _CD, _PD)


def test_atm_iv_is_iv_at_nearest_strike_to_forward():
    assert atm_iv(_SNAP, forward=20010) == 0.15      # nearest strike 20000


def test_strike_nearest_delta_picks_the_right_wing():
    assert strike_nearest_delta(_SNAP, 0.25, "call") == 20100    # call delta ~0.32 closest to .25
    assert strike_nearest_delta(_SNAP, -0.25, "put") == 19900    # put delta ~-0.35 closest to -.25


def test_skew_is_put_wing_minus_call_wing_iv():
    # 25d put strike 19900 (iv .17) minus 25d call strike 20100 (iv .155)
    assert round(skew(_SNAP), 4) == round(0.17 - 0.155, 4)       # positive = put skew (fear)


def test_vrp_is_iv_over_realized_and_nan_safe():
    assert vrp(0.18, 0.12) == 0.18 / 0.12
    assert math.isnan(vrp(0.18, 0.0))       # zero realized vol -> NaN, not inf
