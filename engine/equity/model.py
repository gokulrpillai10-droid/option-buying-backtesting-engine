"""A strategy's intent to trade shares — sized in rupees or fixed units.

Mirrors the options Order but for cash equity: ``direction`` replaces
side/strike. Default sizing is fixed CAPITAL per trade (the engine converts
to a share qty at the fill price, so capital-at-risk stays constant as the
stock's price level drifts over a multi-year backtest). A fixed ``qty``
overrides capital sizing when the study wants constant share count instead
(rupee risk then drifts with the price level — that is the caller's choice).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EquityOrder:
    direction: str            # "long" (buy shares) or "short" (intraday short-sell)
    capital: float = 0.0      # rupees to deploy; qty = floor(capital / fill_price)
    qty: Optional[int] = None  # fixed share count; overrides capital sizing
