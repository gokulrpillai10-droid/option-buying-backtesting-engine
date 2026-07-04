"""The runner: drives one day of bars through the engine.

For each minute it builds a look-ahead-safe :class:`BarContext`, asks the
strategy for orders, and -- when flat -- routes an entry through the
:class:`ExecutionModel` (fill at t+1 open). Once filled it walks the option's
own bars from the fill bar to square-off, resolves the exit (stop / trailing /
time), and books a fully-costed :class:`Trade`. Rejections are kept, never
dropped (honesty rule).

Single position at a time; after an exit it keeps scanning for the next signal,
so a strategy may trade more than once a day.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import List, Optional

import pandas as pd

from engine.data.chain import Chain
from engine.execution.costs import CostModel
from engine.execution.model import ExecutionModel, Fill, Rejection
from engine.position.exits import resolve_exit
from engine.position.manager import Position, Trade
from engine.strategy.base import BarContext, Strategy


@dataclass(frozen=True)
class RejectionRecord:
    dt: datetime
    reason: str


@dataclass
class RunResult:
    trades: List[Trade] = field(default_factory=list)
    rejections: List[RejectionRecord] = field(default_factory=list)


class Runner:
    def __init__(self, chain: Chain, execution: ExecutionModel,
                 cost_model: CostModel, square_off: time,
                 stop_loss_pct: float, trailing_pct: Optional[float] = None,
                 spot=None, forward: pd.Series = None):
        # spot drives signals (accurate); forward is the legacy fallback.
        self._chain = chain
        self._spot = spot
        self._forward = forward
        self._em = execution
        self._costs = cost_model
        self._square_off = square_off
        self._stop = stop_loss_pct
        self._trail = trailing_pct

    def _minutes(self):
        if self._spot is not None:
            return list(self._spot.timestamps())
        return list(self._forward.index)

    def _context(self, now):
        if self._spot is not None:
            return BarContext(now=now, chain=self._chain, spot=self._spot)
        return BarContext(now=now, chain=self._chain, forward=self._forward)

    def run(self, strategy: Strategy) -> RunResult:
        result = RunResult()
        minutes = self._minutes()
        i = 0
        while i < len(minutes):
            now = minutes[i].to_pydatetime()
            ctx = self._context(now)
            orders = strategy.on_bar(ctx)
            if not orders:
                i += 1
                continue

            res = self._em.enter(orders[0], now)
            if isinstance(res, Rejection):
                result.rejections.append(RejectionRecord(now, res.reason))
                i += 1
                continue

            exit_idx = self._book_trade(res, minutes, i, result)
            i = exit_idx + 1   # resume scanning after the exit bar
        return result

    def _book_trade(self, fill: Fill, minutes, signal_i: int, result: RunResult) -> int:
        """Fill is at minutes[signal_i + 1]. Walk option bars to square-off,
        resolve the exit, append the trade, and return the exit bar index."""
        entry_i = signal_i + 1
        bars, idxs = [], []
        for j in range(entry_i, len(minutes)):
            dt = minutes[j].to_pydatetime()
            if dt.time() > self._square_off:
                break
            q = self._chain.snapshot_at(dt)
            quote = q.call(fill.strike) if fill.side.upper() == "CALL" else q.put(fill.strike)
            bars.append((quote.high, quote.low, quote.close))
            idxs.append(j)

        level, reason = resolve_exit(fill.price, bars, self._stop, self._trail)
        # find which bar triggered, to resume scanning after it
        exit_j = idxs[self._exit_bar_index(fill.price, bars, level, reason)]

        sell = self._em.exit_price(level)
        pos = Position(side=fill.side, strike=fill.strike, entry=fill.price, qty=fill.qty)
        result.trades.append(pos.close(exit=sell, cost_model=self._costs, reason=reason))
        return exit_j

    @staticmethod
    def _exit_bar_index(entry, bars, level, reason) -> int:
        if reason == "time_stop":
            return len(bars) - 1
        for k, (_h, low, _c) in enumerate(bars):
            if low <= level:
                return k
        return len(bars) - 1
