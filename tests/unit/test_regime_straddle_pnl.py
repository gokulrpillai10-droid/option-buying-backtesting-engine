"""T1 — two-sided straddle thermometer P&L helper (regime validation).

A LONG straddle (buy CALL + buy PUT) is deployed on stormy/BUY days and profits
when the underlying moves (either premium rises). A SHORT straddle (sell both)
is deployed on calm/SELL days and profits from theta as premiums decay -- but
bleeds when a calm patch ends in a gap. These tests pin that sign behaviour and
the honest cost/slippage treatment, reusing the engine's SpreadPosition math.
"""
from engine.execution.costs import CostModel
from engine.position.manager import Trade
from analysis.regime_straddle_pnl import (
    straddle_trade, direction_for, summarize, atm_strike, straddle_from_snapshots,
)


class _Q:
    def __init__(self, traded, close=0.0):
        self.is_traded, self.close = traded, close


class _Snap:
    """Duck-typed OptionChainSnapshot. ``calls``/``puts`` map strike->close for
    traded strikes; a strike absent from the map is treated as not-traded."""
    def __init__(self, calls, puts):
        self._c, self._p = dict(calls), dict(puts)
    def strikes(self):
        return sorted(set(self._c) | set(self._p))
    def call(self, k):
        return _Q(k in self._c, self._c.get(k, 0.0))
    def put(self, k):
        return _Q(k in self._p, self._p.get(k, 0.0))


def _t(net):
    return Trade(side="SPREAD", strike=0, qty=0, entry=0.0, exit=0.0,
                 gross=net, cost=0.0, net=net)


def test_long_straddle_profits_when_premium_rises():
    # buy call+put at 100 each; both rise -> long straddle wins
    t = straddle_trade(strike=20000, entry_call=100.0, entry_put=100.0,
                       exit_call=160.0, exit_put=130.0, qty=75,
                       direction="long", cost_model=CostModel(), slip=0.0)
    # gross = ((160-100) + (130-100)) * 75 = 90 * 75 = 6750
    assert t.gross == 6750.0
    assert 0 < t.net < t.gross      # costs reduce a real win


def test_short_straddle_profits_when_premium_decays():
    # sell call+put at 100 each; both decay -> theta captured
    t = straddle_trade(strike=20000, entry_call=100.0, entry_put=100.0,
                       exit_call=40.0, exit_put=30.0, qty=75,
                       direction="short", cost_model=CostModel(), slip=0.0)
    # short gross = ((100-40) + (100-30)) * 75 = 130 * 75 = 9750
    assert t.gross == 9750.0
    assert 0 < t.net < t.gross


def test_short_straddle_bleeds_when_calm_ends_in_a_gap():
    # the tail the eng-review flagged: a gap-up spikes the call -> short loses big
    t = straddle_trade(strike=20000, entry_call=100.0, entry_put=100.0,
                       exit_call=400.0, exit_put=20.0, qty=75,
                       direction="short", cost_model=CostModel(), slip=0.0)
    # short gross = ((100-400) + (100-20)) * 75 = (-220) * 75 = -16500
    assert t.gross == -16500.0
    assert t.net < t.gross          # costs make the loss worse, not better


def test_slippage_is_adverse_on_both_sides():
    kw = dict(strike=20000, entry_call=100.0, entry_put=100.0,
              exit_call=160.0, exit_put=130.0, qty=75, cost_model=CostModel())
    long_clean = straddle_trade(direction="long", slip=0.0, **kw).net
    long_slip = straddle_trade(direction="long", slip=0.0025, **kw).net
    assert long_slip < long_clean   # slip always reduces the realized result


def test_direction_for_maps_vol_state_to_side():
    assert direction_for(stable_low_vol=True, high_vol=False) == "short"   # calm -> theta
    assert direction_for(stable_low_vol=False, high_vol=True) == "long"    # stormy -> gamma
    assert direction_for(stable_low_vol=False, high_vol=False) is None     # neutral -> stand aside
    # a day cannot be both, but if labels ever collide, danger wins (go long vol)
    assert direction_for(stable_low_vol=True, high_vol=True) == "long"


def test_summarize_aggregates_net_winrate_and_profit_factor():
    s = summarize([_t(100.0), _t(-40.0), _t(60.0)])
    assert s["n"] == 3
    assert s["net"] == 120.0
    assert s["wins"] == 2
    assert s["win_rate"] == 2 / 3
    assert s["profit_factor"] == 160.0 / 40.0     # gross wins / |gross losses|


def test_summarize_empty_is_zeroed_not_a_crash():
    s = summarize([])
    assert s["n"] == 0 and s["net"] == 0.0
    assert s["win_rate"] == 0.0
    assert s["profit_factor"] == 0.0              # no losses AND no wins -> defined 0, not inf/NaN


def test_atm_strike_picks_nearest_with_both_legs_traded():
    snap = _Snap(calls={19900: 5, 20000: 5, 20100: 5},
                 puts={19900: 5, 20000: 5, 20100: 5})
    assert atm_strike(snap, ref_price=20010) == 20000     # nearest to spot


def test_atm_strike_skips_strike_missing_a_leg():
    # 20000 is nearest but its PUT is not traded -> must fall to a fully-traded strike
    snap = _Snap(calls={19900: 5, 20000: 5, 20100: 5}, puts={19900: 5, 20100: 5})
    assert atm_strike(snap, ref_price=20010) == 20100     # nearest where BOTH trade


def test_atm_strike_none_when_no_strike_has_both_legs():
    snap = _Snap(calls={20000: 5}, puts={})
    assert atm_strike(snap, ref_price=20000) is None


def test_straddle_from_snapshots_assembles_a_trade():
    # calm/short: enter ATM 20000 straddle at 120+110=230, decay to 40+30=70
    entry = _Snap(calls={20000: 120.0}, puts={20000: 110.0})
    exit_ = _Snap(calls={20000: 40.0}, puts={20000: 30.0})
    t = straddle_from_snapshots(entry, exit_, ref_price=20005, qty=75,
                                direction="short", cost_model=CostModel(), slip=0.0)
    # short gross = ((120-40) + (110-30)) * 75 = 160 * 75 = 12000
    assert t.strike == 20000
    assert t.gross == 12000.0
    assert 0 < t.net < t.gross


def test_straddle_from_snapshots_none_when_no_atm_or_exit_leg():
    entry = _Snap(calls={20000: 100.0}, puts={})          # no ATM (put missing)
    exit_ = _Snap(calls={20000: 40.0}, puts={20000: 30.0})
    assert straddle_from_snapshots(entry, exit_, 20000, 75, "short", CostModel()) is None
    # ATM ok at entry, but the strike vanished from the exit chain -> None, not a crash
    entry2 = _Snap(calls={20000: 100.0}, puts={20000: 90.0})
    exit2 = _Snap(calls={}, puts={})
    assert straddle_from_snapshots(entry2, exit2, 20000, 75, "short", CostModel()) is None
