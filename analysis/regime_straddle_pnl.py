"""T1 — two-sided straddle thermometer for the buy/sell regime detector.

The GO gate: does SHORTING a straddle on calm/SELL days (theta) and BUYING one
on stormy/BUY days (gamma) actually pay, net of costs, OOS, vs nulls? This module
is validation-only -- it does NOT route live orders.

P&L reuses the engine's proven ``SpreadPosition`` (a straddle is a 2-leg position;
short-straddle math is first-class there), so costs + adverse slippage are the
same primitives the backtest is trusted on.

Spec: docs/superpowers/specs/2026-08-13-regime-buysell-detector-design.md
"""
from __future__ import annotations

from engine.execution.costs import CostModel
from engine.position.manager import SpreadLeg, SpreadPosition, Trade


def atm_strike(snapshot, ref_price: float):
    """Nearest strike to ``ref_price`` where BOTH the call and put are traded,
    or ``None`` if no strike has both legs. A straddle needs both legs fillable at
    the same strike, so a strike missing one side is skipped (not silently used).
    """
    best, best_dist = None, None
    for k in snapshot.strikes():
        if not (snapshot.call(k).is_traded and snapshot.put(k).is_traded):
            continue
        dist = abs(k - ref_price)
        if best_dist is None or dist < best_dist:
            best, best_dist = k, dist
    return best


def straddle_from_snapshots(entry_snap, exit_snap, ref_price: float, qty: int,
                            direction: str, cost_model: CostModel, slip: float = 0.0):
    """Assemble one straddle ``Trade`` from an entry and an exit chain snapshot of
    the SAME expiry. Returns ``None`` (skip, never crash) if there is no ATM strike
    with both legs traded at entry, or that strike is not tradeable at exit.
    """
    strike = atm_strike(entry_snap, ref_price)
    if strike is None:
        return None
    if strike not in exit_snap.strikes():
        return None
    xc, xp = exit_snap.call(strike), exit_snap.put(strike)
    if not (xc.is_traded and xp.is_traded):
        return None
    ec, ep = entry_snap.call(strike), entry_snap.put(strike)
    return straddle_trade(strike, ec.close, ep.close, xc.close, xp.close,
                          qty, direction, cost_model, slip)


def summarize(trades) -> dict:
    """Aggregate a set of straddle ``Trade``s into thermometer stats.

    ``profit_factor`` = gross wins / |gross losses|; defined as 0.0 when there are
    no losses AND no wins (empty), avoiding inf/NaN. All figures are net of costs.
    """
    n = len(trades)
    net = sum(t.net for t in trades)
    wins = [t.net for t in trades if t.net > 0]
    losses = [t.net for t in trades if t.net < 0]
    gross_loss = -sum(losses)
    pf = (sum(wins) / gross_loss) if gross_loss > 0 else 0.0
    return {
        "n": n,
        "net": net,
        "wins": len(wins),
        "win_rate": (len(wins) / n) if n else 0.0,
        "profit_factor": pf,
    }


def direction_for(stable_low_vol: bool, high_vol: bool):
    """Map a day's vol state to the straddle side, or ``None`` (stand aside).

    Stormy (``high_vol``) -> ``'long'`` (buy gamma); calm (``stable_low_vol``) ->
    ``'short'`` (sell theta). The two are mutually exclusive on the data, but if
    they ever collide, danger wins -- go LONG vol rather than short into a spike.
    """
    if high_vol:
        return "long"
    if stable_low_vol:
        return "short"
    return None


def straddle_trade(strike: int, entry_call: float, entry_put: float,
                   exit_call: float, exit_put: float, qty: int,
                   direction: str, cost_model: CostModel, slip: float = 0.0) -> Trade:
    """One ATM straddle round-trip as a costed ``Trade``.

    ``direction='long'`` buys both legs (profits when premium rises = gamma);
    ``'short'`` sells both legs (profits when premium falls = theta). Adverse
    slippage + the India F&O cost card are applied via ``SpreadPosition.close``.
    """
    action = "buy" if direction == "long" else "sell"
    legs = (
        SpreadLeg(action=action, side="CALL", strike=strike, entry=entry_call, qty=qty),
        SpreadLeg(action=action, side="PUT", strike=strike, entry=entry_put, qty=qty),
    )
    return SpreadPosition(legs=legs).close(
        exit_prices=(exit_call, exit_put), cost_model=cost_model, slip=slip,
        reason=f"straddle_{direction}",
    )
