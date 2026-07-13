"""Pure gap-router decision logic — no pandas, no I/O.

Shared by strategies/gap_router_equity.py, analysis/hdfc_gapandgo.py, and the
unit tests so the classification can never drift (same role sr_core.py plays
for the S/R tools). Two checkpoints: decide_open (09:15) and decide_0930.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Thresholds:
    gap_thr: float = 0.0015     # min |gap| to act
    big_thr: float = 0.004      # "big" gap
    nifty_min: float = 0.001    # NIFTY must actually gap to count as aligned
    drive_thr: float = 0.003    # first-15-min drive to call momentum


@dataclass(frozen=True)
class Decision:
    branch: str                 # "fade" | "go" | "none" | "defer"
    side: Optional[str]         # "long" | "short" | None


def compute_gap(open_: float, prev_close: float) -> float:
    return open_ / prev_close - 1.0


def signed_drive(px: float, open_: float, gap: float) -> float:
    """(px/open - 1) measured in the gap's direction: + = extending the gap,
    - = reverting toward prior close."""
    s = 1.0 if gap > 0 else -1.0
    return (px / open_ - 1.0) * s


def aligned(gap: float, ngap: Optional[float], thr: Thresholds) -> bool:
    if ngap is None:
        return False
    same_dir = (gap > 0) == (ngap > 0)
    return same_dir and abs(ngap) >= thr.nifty_min


def fade_side(gap: float) -> str:
    return "short" if gap > 0 else "long"      # against the gap


def go_side(gap: float) -> str:
    return "long" if gap > 0 else "short"      # with the gap


def decide_open(gap: float, ngap: Optional[float], thr: Thresholds) -> Decision:
    """09:15 checkpoint. FADE small unconfirmed gaps immediately; defer the
    rest to 09:30; NO-TRADE when there's no real gap."""
    if abs(gap) <= thr.gap_thr:
        return Decision("none", None)
    small = abs(gap) <= thr.big_thr
    if small and not aligned(gap, ngap, thr):
        return Decision("fade", fade_side(gap))
    return Decision("defer", None)


def decide_0930(gap: float, ngap: Optional[float], drive: float,
                thr: Thresholds) -> Decision:
    """09:30 checkpoint for deferred gaps: reverting -> fade, driving hard and
    (big or NIFTY-aligned) -> go, otherwise stand aside."""
    if drive <= 0:
        return Decision("fade", fade_side(gap))
    big_or_aligned = abs(gap) > thr.big_thr or aligned(gap, ngap, thr)
    if drive >= thr.drive_thr and big_or_aligned:
        return Decision("go", go_side(gap))
    return Decision("none", None)
