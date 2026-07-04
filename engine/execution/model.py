"""Execution model -- the accuracy core.

The ONLY place an entry fill is produced. Sequence for an entry:

    signal at t (chain read as-of t only)
      -> tradeability check (IsTradedStrike at t)
      -> liquidity gate (Volume / OI minimums at t)
      -> fill at t+1 open                     (anti-look-ahead)
      -> adverse slippage applied to the fill

Every order either returns a ``Fill`` or a ``Rejection`` carrying a reason --
orders are never silently dropped (honesty rule).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from engine.data.chain import Chain


@dataclass(frozen=True)
class Order:
    """A strategy's intent to buy one option (long premium only).

    Strategies express size in ``lots``; the engine converts to ``qty`` via the
    date-keyed lot size. ``qty`` may be set directly (legacy / explicit)."""
    side: str          # "CALL" or "PUT"
    strike: int
    qty: Optional[int] = None
    lots: int = 1
    action: str = "buy"  # "buy" (long premium) or "sell" (short leg of a spread)


@dataclass(frozen=True)
class Fill:
    side: str
    strike: int
    price: float
    qty: int


@dataclass(frozen=True)
class Rejection:
    reason: str


class ExecutionModel:
    def __init__(self, chain: Chain, slippage_pct: float = 0.0,
                 min_volume: int = 0, min_oi: int = 0):
        self._chain = chain
        self._slip = slippage_pct
        self._min_volume = min_volume
        self._min_oi = min_oi

    def enter(self, order: Order, signal_dt: datetime):
        """Resolve ``order`` into a ``Fill`` (at the t+1 open, slipped up) or a
        ``Rejection`` with a reason, judged on data as of ``signal_dt``."""
        snap = self._chain.snapshot_at(signal_dt)
        quote = snap.call(order.strike) if order.side.upper() == "CALL" else snap.put(order.strike)

        if not quote.is_traded:
            return Rejection("not_tradeable")
        if quote.volume < self._min_volume or quote.oi < self._min_oi:
            return Rejection("insufficient_liquidity")

        nxt = self._chain.next_open(signal_dt, order.strike, order.side)
        if nxt is None:
            return Rejection("no_next_bar")

        return Fill(side=order.side, strike=order.strike,
                    price=nxt * (1 + self._slip), qty=order.qty)

    def exit_price(self, level: float) -> float:
        """Adverse slippage on the way out: a long position sells DOWN."""
        return level * (1 - self._slip)
