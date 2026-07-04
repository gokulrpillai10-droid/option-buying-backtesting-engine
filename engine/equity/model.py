"""A strategy's intent to trade shares — sized in rupees, not units.

Mirrors the options Order but for cash equity: ``direction`` replaces
side/strike, and size is fixed CAPITAL per trade (the engine converts to a
share qty at the fill price, so capital-at-risk stays constant as the stock's
price level drifts over a multi-year backtest).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EquityOrder:
    direction: str      # "long" (buy shares) or "short" (intraday short-sell)
    capital: float      # rupees to deploy; qty = floor(capital / fill_price)
