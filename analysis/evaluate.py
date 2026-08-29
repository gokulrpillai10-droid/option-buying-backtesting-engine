"""The shared GO/NO-GO edge battery — the 'find the edge FROM THE DATA' step.

Every strategy's backtest Result runs the SAME gauntlet here, so the verdict is
consistent and never hand-rolled per script. Give it a Result (anything with
`.trades`, each trade carrying `.net` and `.date`) and it returns a
`Verdict(GO | MARGINAL | NO-GO)` with the reasons any gate failed.

The gates (each documented, thresholds are module constants):
  profitable      net > 0 AND profit_factor >= PF_GO
  significant     |t-stat| >= T_GO on the per-trade net series
  robust          block-bootstrap p(net <= 0) < P_MAX
  holds out       split-half (early vs late by date median) BOTH positive
  broad           years_positive / years >= YEARS_FRAC (not one year carrying it)
  not tail-fed    net excluding the top TAIL_N winners still > 0

NO-GO short-circuits on: too few trades, net <= 0, or profit_factor < 1.
GO requires every gate. Anything positive-but-fragile is MARGINAL, with the
failing gates named — the honest 'no longer a clean NO-GO, not a deployable
edge either' bucket.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# --- gate thresholds (the house GO/NO-GO standard) ---
MIN_TRADES = 10       # below this the sample can't support a verdict
PF_GO = 1.2           # profit factor floor for a GO
T_GO = 3.0            # |t-stat| floor (the project's "|t|<3 = no edge" bar)
P_MAX = 0.05          # max bootstrap p(net<=0) for "robust"
YEARS_FRAC = 0.6      # fraction of years that must be positive
TAIL_N = 5            # winners removed for the tail-dependence check
_BOOT_N = 2000        # bootstrap resamples
_BOOT_SEED = 12345    # fixed -> deterministic verdicts


@dataclass
class Verdict:
    verdict: str                       # "GO" | "MARGINAL" | "NO-GO"
    metrics: Dict[str, float]
    reasons: List[str] = field(default_factory=list)   # failing gates (empty for a clean GO)
    name: str = "strategy"

    def __str__(self) -> str:
        return self.report()

    def report(self) -> str:
        m = self.metrics
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        lines = [
            f"EDGE VERDICT [{self.name}]:  {self.verdict}",
            f"  trades {int(m['n_trades'])}  net Rs{m['net']:,.0f}  PF {pf}  "
            f"win {m['win_rate']:.0%}  t-stat {m['t_stat']:+.2f}",
            f"  Sharpe(trade-day,ann) {m['sharpe']:.2f}  maxDD Rs{m['max_drawdown']:,.0f}  "
            f"bootstrap p(net<=0) {m['bootstrap_p']:.3f}",
            f"  split-half early Rs{m['early_net']:,.0f} / late Rs{m['late_net']:,.0f}  "
            f"years +{int(m['years_positive'])}/{int(m['years_total'])}  "
            f"net-excl-top{TAIL_N} Rs{m['tail_excl_net']:,.0f}",
        ]
        by = m.get("by_year")
        if by:
            lines.append("  by year: " + "  ".join(
                f"{y} Rs{v:,.0f} {'+' if v > 0 else '-'}" for y, v in by.items()))
        if m.get("cost_frac") is not None:
            lines.append(f"  costs {m['cost_frac']:.0%} of gross")
        if self.reasons:
            lines.append("  failed gates: " + "; ".join(self.reasons))
        return "\n".join(lines)


def _by_day(trades) -> List[float]:
    """Per-calendar-day net, ordered by date (the trade-day P&L series)."""
    acc: Dict[object, float] = {}
    for t in trades:
        acc[t.date] = acc.get(t.date, 0.0) + t.net
    return [acc[d] for d in sorted(acc)]


def _t_stat(nets: List[float]) -> float:
    n = len(nets)
    if n < 2:
        return 0.0
    mean = sum(nets) / n
    var = sum((x - mean) ** 2 for x in nets) / (n - 1)
    sd = math.sqrt(var)
    return (mean / (sd / math.sqrt(n))) if sd > 0 else 0.0


def _sharpe(daily: List[float], years: float) -> float:
    n = len(daily)
    if n < 2:
        return 0.0
    mean = sum(daily) / n
    var = sum((x - mean) ** 2 for x in daily) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    td_per_year = n / years if years > 0 else n
    return (mean / sd) * math.sqrt(td_per_year)


def _max_drawdown(daily: List[float]) -> float:
    peak = cum = 0.0
    mdd = 0.0
    for x in daily:
        cum += x
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return mdd


def _bootstrap_p(nets: List[float]) -> float:
    """Fraction of block-resampled backtests whose total net <= 0 — a robustness
    p-value (does the edge survive reshuffling which trades occurred?)."""
    n = len(nets)
    if n < 2:
        return 1.0
    rng = random.Random(_BOOT_SEED)
    le0 = 0
    for _ in range(_BOOT_N):
        s = sum(nets[rng.randrange(n)] for _ in range(n))
        if s <= 0:
            le0 += 1
    return le0 / _BOOT_N


def evaluate(result, name: str = "strategy", gross: Optional[float] = None,
             min_trades: int = MIN_TRADES) -> Verdict:
    """Run the full edge battery on ``result`` and classify GO / MARGINAL / NO-GO."""
    trades = list(getattr(result, "trades", []))
    n = len(trades)
    nets = [float(t.net) for t in trades]
    net = sum(nets)
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    gross_loss = -sum(losses)
    pf = (sum(wins) / gross_loss) if gross_loss > 0 else (float("inf") if wins else 0.0)

    daily = _by_day(trades) if trades else []
    dates = [t.date for t in trades]
    years_span = 1.0
    if dates:
        span_days = (max(dates) - min(dates)).days
        years_span = max(span_days / 365.25, 1.0 / 12)   # >= ~1 month
    by_year: Dict[int, float] = {}
    for t in trades:
        by_year[t.date.year] = by_year.get(t.date.year, 0.0) + t.net
    years_total = len(by_year)
    years_positive = sum(1 for v in by_year.values() if v > 0)

    tail_excl_net = net - sum(sorted(nets, reverse=True)[:TAIL_N])

    early_net = late_net = 0.0
    if dates:
        med = sorted(dates)[len(dates) // 2]
        early_net = sum(t.net for t in trades if t.date <= med)
        late_net = sum(t.net for t in trades if t.date > med)
        if late_net == 0.0 and early_net == net:      # median at the end -> split by index
            half = n // 2
            early_net = sum(nets[:half])
            late_net = sum(nets[half:])

    metrics = {
        "n_trades": n, "net": net, "profit_factor": pf,
        "win_rate": (len(wins) / n) if n else 0.0,
        "t_stat": _t_stat(nets),
        "sharpe": _sharpe(daily, years_span),
        "max_drawdown": _max_drawdown(daily),
        "bootstrap_p": _bootstrap_p(nets),
        "early_net": early_net, "late_net": late_net,
        "years_total": years_total, "years_positive": years_positive,
        "by_year": dict(sorted(by_year.items())),
        "tail_excl_net": tail_excl_net,
        "cost_frac": ((gross - net) / gross) if (gross and gross > 0) else None,
    }

    # --- NO-GO short-circuits ---
    if n < min_trades:
        return Verdict("NO-GO", metrics, [f"insufficient sample: {n} trades < {min_trades}"], name)
    if net <= 0:
        return Verdict("NO-GO", metrics, [f"net Rs{net:,.0f} <= 0"], name)
    if pf < 1.0:
        return Verdict("NO-GO", metrics, [f"profit_factor {pf:.2f} < 1.0"], name)

    # --- robustness gates (positive but must earn a GO) ---
    reasons: List[str] = []
    if pf < PF_GO:
        reasons.append(f"profit_factor {pf:.2f} < {PF_GO}")
    if abs(metrics["t_stat"]) < T_GO:
        reasons.append(f"|t-stat| {abs(metrics['t_stat']):.2f} < {T_GO}")
    if metrics["bootstrap_p"] >= P_MAX:
        reasons.append(f"bootstrap p(net<=0) {metrics['bootstrap_p']:.3f} >= {P_MAX}")
    if not (early_net > 0 and late_net > 0):
        reasons.append(f"split-half not both positive (early Rs{early_net:,.0f} / late Rs{late_net:,.0f})")
    if years_total and (years_positive / years_total) < YEARS_FRAC:
        reasons.append(f"only {years_positive}/{years_total} years positive")
    if tail_excl_net <= 0:
        reasons.append(f"tail-dependent: net excl top-{TAIL_N} = Rs{tail_excl_net:,.0f}")

    return Verdict("GO" if not reasons else "MARGINAL", metrics, reasons, name)


def compare_to_nulls(strategy_net: float, nulls: Dict[str, float]) -> List[str]:
    """Strategy must beat EVERY null (always-long/short/random/block-shuffle...).
    Returns the list of nulls it FAILED to beat (empty = beats all). The nulls are
    strategy-specific so the runner supplies their nets; this just enforces the rule."""
    return [f"does not beat null '{k}' (Rs{v:,.0f} >= Rs{strategy_net:,.0f})"
            for k, v in nulls.items() if strategy_net <= v]
