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

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Exit:
    kind: str                      # "market", "level", or "trail"
    level: Optional[float] = None
    trigger: Optional[str] = None  # "fall" (stop) or "rise" (target) for level exits
    reason: str = ""
    fraction: float = 1.0          # fraction of the position to close (scale-outs)
    gap: Optional[float] = None    # trailing distance (premium points) for "trail"

    @classmethod
    def market(cls, reason: str = "market", fraction: float = 1.0) -> "Exit":
        return cls(kind="market", reason=reason, fraction=fraction)

    @classmethod
    def trail(cls, gap: float, reason: str = "trail", fraction: float = 1.0) -> "Exit":
        """Trailing stop on the OPTION PREMIUM: exit when premium falls ``gap`` points
        below its running peak (peak tracked intrabar by the engine from entry)."""
        return cls(kind="trail", gap=float(gap), reason=reason, fraction=fraction)

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
