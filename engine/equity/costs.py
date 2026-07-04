"""India cash-equity intraday (MIS) cost model — Zerodha rate card.

Independent of the F&O CostModel: completely different charges (percentage
brokerage with a cap, sell-side-only STT at 0.025%, NSE cash exchange rate),
no shared logic beyond the .total(...) shape. Slippage is NOT here — the
EquityBacktest applies it adversely to fill prices, same convention as the
options engine.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EquityCostModel:
    brokerage_pct: float = 0.0003    # 0.03% of order value...
    brokerage_cap: float = 20.0      # ...capped at Rs20 per executed order
    stt_sell: float = 0.00025        # STT 0.025%, SELL side only (intraday equity)
    exchange: float = 0.0000297      # NSE cash txn charge, both sides
    sebi: float = 0.000001           # SEBI turnover fee, both sides
    stamp_buy: float = 0.00003       # stamp duty 0.003%, buy side only
    gst: float = 0.18                # GST on (brokerage + exchange + sebi)

    def total(self, buy_value: float, sell_value: float) -> float:
        """Round-trip cost for one buy order moving ``buy_value`` and one sell
        order moving ``sell_value`` (both in rupees)."""
        brokerage = (min(self.brokerage_cap, self.brokerage_pct * buy_value)
                     + min(self.brokerage_cap, self.brokerage_pct * sell_value))
        stt = self.stt_sell * sell_value
        exch = self.exchange * (buy_value + sell_value)
        sebi = self.sebi * (buy_value + sell_value)
        stamp = self.stamp_buy * buy_value
        gst = self.gst * (brokerage + exch + sebi)
        return brokerage + stt + exch + sebi + stamp + gst
