"""Per-minute exit resolution for a long option position.

Walks the option bars after entry in order and returns the first exit triggered:
a hard stop, a trailing stop off the running peak, or -- if neither fires -- a
time stop at the last bar's close (the runner uses this for EOD square-off).

Pure price logic: returns the raw exit LEVEL and a reason string. Slippage and
costs are applied downstream by the execution model and position.
"""
from __future__ import annotations

from typing import List, Optional, Tuple


def resolve_exit(entry: float, bars: List[Tuple[float, float, float]],
                 stop_loss_pct: float,
                 trailing_pct: Optional[float] = None) -> Tuple[float, str]:
    """``bars`` is an ordered list of ``(high, low, close)`` after entry.

    Returns ``(exit_price, reason)`` where reason is one of ``stop_loss``,
    ``trailing_stop`` or ``time_stop``.
    """
    hard_stop = entry * (1 - stop_loss_pct)
    peak = entry
    for high, low, _close in bars:
        peak = max(peak, high)
        level = hard_stop
        if trailing_pct is not None:
            level = max(hard_stop, peak * (1 - trailing_pct))
        if low <= level:
            reason = "trailing_stop" if level > hard_stop else "stop_loss"
            return level, reason
    return bars[-1][2], "time_stop"
