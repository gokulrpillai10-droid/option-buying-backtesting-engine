"""The strategy's order intent.

An ``Order`` is a strategy's request to trade one option leg. Strategies express
size in ``lots``; the engine converts to ``qty`` via the date-keyed lot size
(``engine.instruments.lot_size_for``). ``qty`` may be set directly (explicit).

Fills themselves are produced by the ``Backtest`` driver, which reads the
option-chain snapshot at B+timeframe (the anti-look-ahead fill contract). This
module only carries intent -- it never touches data or produces a price.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Order:
    """A strategy's intent to trade one option leg.

    ``side`` is "CALL"/"PUT"; ``action`` is "buy" (long premium) or "sell" (the
    short leg of a covered spread). Size is ``lots`` (the engine applies the
    date-keyed lot size) unless ``qty`` is supplied explicitly.
    """
    side: str          # "CALL" or "PUT"
    strike: int
    qty: Optional[int] = None
    lots: int = 1
    action: str = "buy"  # "buy" (long premium) or "sell" (short leg of a spread)
