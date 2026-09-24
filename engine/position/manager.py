"""Open positions and the closed trades they become.

A ``Position`` is a long-premium option holding. Closing it at an exit price
produces a fully-costed ``Trade`` (gross, transaction cost, net) using the
shared :class:`~engine.execution.costs.CostModel`. P&L is the long-premium
formula ``(exit - entry) * qty`` for both calls and puts -- buying either
profits when its own premium rises.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.execution.costs import CostModel


@dataclass(frozen=True)
class Trade:
    side: str
    strike: int
    qty: int
    entry: float
    exit: float
    gross: float
    cost: float
    net: float
    reason: str = ""
    date: object = None      # trading date (set by the Backtest driver)
    entry_dt: object = None  # fill minute of entry (set by the Backtest driver)
    exit_dt: object = None   # fill minute of exit


@dataclass(frozen=True)
class SpreadLeg:
    action: str   # "buy" or "sell"
    side: str     # "CALL" or "PUT"
    strike: int
    entry: float  # fill premium (already entry-slipped by the driver)
    qty: int


@dataclass(frozen=True)
class SpreadPosition:
    """A multi-leg (vertical) position. Combined P&L = sum of per-leg P&L, where a
    BUY leg profits when its premium rises and a SELL leg profits when it falls."""
    legs: tuple   # of SpreadLeg

    def debit(self) -> float:
        """Net premium paid to open (buy premium - sell premium received)."""
        return sum((1 if lg.action == "buy" else -1) * lg.entry * lg.qty for lg in self.legs)

    def unrealized(self, prices) -> float:
        """Combined unrealized P&L given current per-leg prices (aligned to ``legs``)."""
        tot = 0.0
        for lg, p in zip(self.legs, prices):
            tot += (p - lg.entry) * lg.qty if lg.action == "buy" else (lg.entry - p) * lg.qty
        return tot

    def close(self, exit_prices, cost_model: CostModel, slip: float = 0.0,
              reason: str = "") -> Trade:
        """Close all legs at ``exit_prices`` (adverse slip applied per leg) -> one
        combined, fully-costed Trade. A BUY leg sells-to-close DOWN; a SELL leg
        buys-to-close UP -- both adverse."""
        gross = cost = 0.0
        for lg, p in zip(self.legs, exit_prices):
            if lg.action == "buy":
                xp = p * (1 - slip)
                gross += (xp - lg.entry) * lg.qty
                cost += cost_model.total(buy_value=lg.entry * lg.qty,
                                         sell_value=xp * lg.qty, n_orders=2)
            else:
                xp = p * (1 + slip)
                gross += (lg.entry - xp) * lg.qty
                cost += cost_model.total(buy_value=xp * lg.qty,
                                         sell_value=lg.entry * lg.qty, n_orders=2)
        qty = self.legs[0].qty
        entry_u = self.debit() / qty if qty else 0.0          # per-unit debit (capital deployed)
        return Trade(side="SPREAD", strike=self.legs[0].strike, qty=qty,
                     entry=entry_u, exit=entry_u + (gross / qty if qty else 0.0),
                     gross=gross, cost=cost, net=gross - cost, reason=reason)


@dataclass(frozen=True)
class Position:
    side: str
    strike: int
    entry: float
    qty: int

    def close(self, exit: float, cost_model: CostModel, n_orders: int = 2,
              reason: str = "", qty: float = None, extra_cost: float = 0.0) -> Trade:
        """Close ``qty`` units (default the full position) -> a costed Trade.
        Partial closes pass a smaller qty; the caller keeps the remainder.
        ``extra_cost`` adds a pre-computed charge (e.g. ITM exercise STT) on top
        of the statutory model — used when the normal sell-side STT is replaced by
        a settlement-value-based charge at expiry."""
        q = self.qty if qty is None else qty
        gross = (exit - self.entry) * q
        cost = cost_model.total(
            buy_value=self.entry * q,
            sell_value=exit * q,
            n_orders=n_orders,
        ) + extra_cost
        return Trade(
            side=self.side, strike=self.strike, qty=q,
            entry=self.entry, exit=exit,
            gross=gross, cost=cost, net=gross - cost, reason=reason,
        )
