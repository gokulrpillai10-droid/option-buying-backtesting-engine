"""Exit value type + level-exit resolution (the accuracy-critical exit mechanics).

A strategy's manage() returns an Exit (or None). Two kinds:
  - Exit.market(reason): discretionary -> fills at the next 1-min option open (runner).
  - Exit.at(level, trigger, reason): a resting order resolved intrabar against the
    option bar. trigger='fall' = a stop (sell when price drops to level); 'rise' = a
    target (sell when price rises to level).

Gap-through (honest, per outside-voice P1-6/ARCH-2): if the bar OPENS already past the
level, the fill is the bar OPEN (worse for a stop, better for a target -- the realistic
fill of a resting order on a gap); otherwise the fill is the level. Recognized at the
bar's close time. 1-min granularity caps fidelity.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Tuple


@dataclass(frozen=True)
class Exit:
    kind: str                      # "market", "level", or "trail"
    level: Optional[float] = None
    trigger: Optional[str] = None  # "fall" (stop) or "rise" (target) for level exits
    reason: str = ""
    fraction: float = 1.0          # fraction of the position to close (scale-outs)
    gap: Optional[float] = None    # trailing distance (premium points) for "trail"
    arm: Optional[float] = None    # premium level that activates a "trail" (None = live at entry)

    @classmethod
    def market(cls, reason: str = "market", fraction: float = 1.0) -> "Exit":
        return cls(kind="market", reason=reason, fraction=fraction)

    @classmethod
    def trail(cls, gap: float, reason: str = "trail", fraction: float = 1.0,
              arm: Optional[float] = None) -> "Exit":
        """Trailing stop on the OPTION PREMIUM: exit when premium falls ``gap`` points
        below its running peak (peak tracked intrabar by the engine from entry).
        With ``arm``, the trail stays dormant until the peak first reaches that
        absolute premium level — bar highs still feed the peak while dormant."""
        return cls(kind="trail", gap=float(gap), reason=reason, fraction=fraction,
                   arm=None if arm is None else float(arm))

    @classmethod
    def at(cls, level: float, trigger: str, reason: str = "", fraction: float = 1.0) -> "Exit":
        if trigger not in ("fall", "rise"):
            raise ValueError("trigger must be 'fall' (stop) or 'rise' (target)")
        return cls(kind="level", level=float(level), trigger=trigger,
                   reason=reason or trigger, fraction=fraction)

    @classmethod
    def bracket(cls, stop: float, target: float,
                stop_reason: str = "stop", target_reason: str = "target",
                stop_fraction: float = 1.0, target_fraction: float = 1.0) -> "Bracket":
        """A premium stop + target resolved together on option bars (e.g. P&L +/-X).
        Per-side fractions allow e.g. a full-size stop but a partial scale-out at target."""
        return Bracket(stop=float(stop), target=float(target),
                       stop_reason=stop_reason, target_reason=target_reason,
                       stop_fraction=float(stop_fraction), target_fraction=float(target_fraction))


@dataclass(frozen=True)
class Bracket:
    kind: str = "bracket"
    stop: float = 0.0
    target: float = 0.0
    stop_reason: str = "stop"
    target_reason: str = "target"
    stop_fraction: float = 1.0
    target_fraction: float = 1.0

    def resolve(self, open_: float, high: float, low: float):
        """Check one option bar; stop (fall) is checked first (conservative).
        Returns (fill_price, reason, fraction) or (None, None, None)."""
        s_fill, s_hit = resolve_level_exit(
            Exit.at(self.stop, "fall"), open_, high, low)
        if s_hit:
            return s_fill, self.stop_reason, self.stop_fraction
        t_fill, t_hit = resolve_level_exit(
            Exit.at(self.target, "rise"), open_, high, low)
        if t_hit:
            return t_fill, self.target_reason, self.target_fraction
        return None, None, None

    def consume(self, reason: str) -> "Bracket":
        """After a PARTIAL fill of one side, the other side keeps resting:
        return a Bracket with the filled side moved out of reach (matched by
        its reason string; give the sides distinct reasons)."""
        if reason == self.target_reason:
            return replace(self, target=float("inf"))
        return replace(self, stop=float("-inf"))


class TwoStage:
    """Two-stage premium exit policy shared by scale-out strategies
    (cpr_breakout, ema5_itm): stage 1 rests an optional full-size premium
    stop at entry - ``stop_pts`` bracketed with a partial target at
    entry + ``target_pts``; once the target books (the runner shrinks
    pos.qty), stage 2 trails the carried lots' premium by ``trail_gap``.

    Call ``reset()`` when a new trade begins; ``exit_for(pos)`` on each
    manage() bar. Stage detection is qty-based: the first call records the
    full size, any smaller qty means the scale-out booked. ``scaled(pos)``
    exposes the stage for strategies with extra stage-1 exits.
    """
    NO_STOP = float("-inf")            # unreachable stop when no loss-cap is set

    def __init__(self, target_pts: float, trail_gap: float, fraction: float,
                 stop_pts: Optional[float] = None, stop_reason: str = "pstop",
                 target_reason: str = "book", trail_reason: str = "ptrail"):
        self.target_pts, self.trail_gap, self.fraction = target_pts, trail_gap, fraction
        self.stop_pts = stop_pts
        self.stop_reason, self.target_reason, self.trail_reason = \
            stop_reason, target_reason, trail_reason
        self.reset()

    def reset(self) -> None:
        self._full = None

    def scaled(self, pos) -> bool:
        """True once the stage-1 partial has booked (the runner shrank pos.qty)."""
        if self._full is None:
            self._full = pos.qty
        return pos.qty < self._full

    def exit_for(self, pos):
        if self.scaled(pos):
            return Exit.trail(self.trail_gap, self.trail_reason)
        stop = pos.entry - self.stop_pts if self.stop_pts is not None else self.NO_STOP
        return Exit.bracket(stop, pos.entry + self.target_pts,
                            stop_reason=self.stop_reason,
                            target_reason=self.target_reason,
                            target_fraction=self.fraction)


def resolve_level_exit(exit: Exit, open_: float, high: float, low: float
                       ) -> Tuple[Optional[float], bool]:
    """Resolve a level Exit against one option bar. Returns (fill_price, hit)."""
    lvl = exit.level
    if exit.trigger == "fall":                 # stop: triggers when price drops to level
        if low <= lvl:
            return (open_ if open_ < lvl else lvl), True   # gap-down -> worse open
    elif exit.trigger == "rise":               # target: triggers when price rises to level
        if high >= lvl:
            return (open_ if open_ > lvl else lvl), True   # gap-up -> better open
    return None, False
