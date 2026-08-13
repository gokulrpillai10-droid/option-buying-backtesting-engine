"""T1 — two-sided straddle thermometer P&L helper (regime validation).

A LONG straddle (buy CALL + buy PUT) is deployed on stormy/BUY days and profits
when the underlying moves (either premium rises). A SHORT straddle (sell both)
is deployed on calm/SELL days and profits from theta as premiums decay -- but
bleeds when a calm patch ends in a gap. These tests pin that sign behaviour and
the honest cost/slippage treatment, reusing the engine's SpreadPosition math.
"""
from engine.execution.costs import CostModel
from engine.position.manager import Trade
from analysis.regime_straddle_pnl import straddle_trade, direction_for, summarize


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
