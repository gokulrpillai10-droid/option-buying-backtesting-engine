"""India F&O cost model for option buying.

The only place transaction costs (brokerage + statutory charges) are computed.
Rates default to the published India F&O option card but are configurable so a
"stress" preset can dial them up. Slippage is NOT here -- it is applied
adversely to the fill price inside the execution model.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    brokerage_per_order: float = 20.0   # flat per executed order
    stt_sell: float = 0.001             # STT, 0.1% of sell-side premium turnover
    exchange: float = 0.0003503         # exchange txn charge, both sides
    sebi: float = 0.000001              # SEBI turnover fee, both sides
    stamp_buy: float = 0.00003          # stamp duty, buy side only
    gst: float = 0.18                   # GST on (brokerage + exchange + sebi)
    exercise_stt_rate: float = 0.0015  # ITM exercise: 0.15% of intrinsic value, not spot

    def total(self, buy_value: float, sell_value: float, n_orders: int) -> float:
        """Total round-trip cost for ``n_orders`` executions moving ``buy_value``
        of premium in and ``sell_value`` out (both in rupees)."""
        brokerage = self.brokerage_per_order * n_orders
        stt = self.stt_sell * sell_value
        exch = self.exchange * (buy_value + sell_value)
        sebi = self.sebi * (buy_value + sell_value)
        stamp = self.stamp_buy * buy_value
        gst = self.gst * (brokerage + exch + sebi)
        return brokerage + stt + exch + sebi + stamp + gst

    def exercise_stt(self, intrinsic_value: float) -> float:
        """STT on ITM long option exercised at expiry.
        ``intrinsic_value`` = (spot - strike) × qty for calls, (strike - spot) × qty for puts.
        This replaces the normal stt_sell for that leg."""
        return self.exercise_stt_rate * intrinsic_value
