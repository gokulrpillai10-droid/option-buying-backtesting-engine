"""Open equity positions and the costed Trades they close into.

Reuses the engine's frozen ``Trade`` dataclass unmodified (side carries
"LONG"/"SHORT", strike is 0 — no derivative layer), so all existing Result
metrics and tear-sheet code work unchanged on equity results.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.equity.costs import EquityCostModel
from engine.position.manager import Trade


@dataclass(frozen=True)
class EquityPosition:
    direction: str      # "long" or "short"
    entry: float        # fill price (already slipped by the driver)
    qty: int

    def close(self, exit: float, cost_model: EquityCostModel,
              reason: str = "") -> Trade:
        """Close the full position at ``exit`` -> a fully-costed Trade.
        Long: buy at entry, sell at exit. Short: sell at entry, buy at exit."""
        if self.direction == "long":
            gross = (exit - self.entry) * self.qty
            buy_value, sell_value = self.entry * self.qty, exit * self.qty
        else:
            gross = (self.entry - exit) * self.qty
            buy_value, sell_value = exit * self.qty, self.entry * self.qty
        cost = cost_model.total(buy_value=buy_value, sell_value=sell_value)
        return Trade(side=self.direction.upper(), strike=0, qty=self.qty,
                     entry=self.entry, exit=exit,
                     gross=gross, cost=cost, net=gross - cost, reason=reason)
