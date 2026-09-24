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
from typing import Dict, List, Optional, Tuple

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
    metrics: Dict[str, object]
    reasons: List[str] = field(default_factory=list)   # failing gates (empty for a clean GO)
    name: str = "strategy"

    def __str__(self) -> str:
        return self.report()

    def report(self) -> str:
        m = self.metrics
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"

        calmar_str = f"{m['calmar']:.2f}" if m.get("calmar") is not None else "n/a"
        lines = [
            f"EDGE VERDICT [{self.name}]:  {self.verdict}",
            f"  trades {int(m['n_trades'])}  net Rs{m['net']:,.0f}  PF {pf}  "
            f"win {m['win_rate']:.0%}  t-stat {m['t_stat']:+.2f}",
            f"  Sharpe {m['sharpe']:.2f}  Sortino {m['sortino']:.2f}  "
            f"Calmar {calmar_str}  maxDD Rs{m['max_drawdown']:,.0f}  "
            f"bootstrap p(net<=0) {m['bootstrap_p']:.3f}",
            f"  split-half early Rs{m['early_net']:,.0f} / late Rs{m['late_net']:,.0f}  "
            f"years +{int(m['years_positive'])}/{int(m['years_total'])}  "
            f"net-excl-top{TAIL_N} Rs{m['tail_excl_net']:,.0f}",
            f"  avg daily Rs{m['avg_daily']:,.0f}  avg weekly Rs{m['avg_weekly']:,.0f}",
        ]

        # CAGR / total return (only when capital was provided)
        if m.get("cagr") is not None:
            lines.append(
                f"  CAGR {m['cagr']:.1%}  total return {m['total_return_pct']:.1%}  "
                f"(capital Rs{m['capital']:,.0f})"
            )

        # VaR / CVaR
        lines.append(
            f"  VaR 95% Rs{m['var_95']:,.0f}  CVaR 95% Rs{m['cvar_95']:,.0f}  "
            f"VaR 99% Rs{m['var_99']:,.0f}  CVaR 99% Rs{m['cvar_99']:,.0f}"
        )

        # max drawdown period
        pk, tr, rc = m.get("peak_date"), m.get("trough_date"), m.get("recovery_date")
        if pk is not None:
            rc_str = str(rc) if rc is not None else "no recovery"
            lines.append(f"  maxDD period: peak {pk} → trough {tr} → recovery {rc_str}")

        # by-year
        by = m.get("by_year")
        if by:
            lines.append("  by year: " + "  ".join(
                f"{y} Rs{v:,.0f} {'+' if v > 0 else '-'}" for y, v in by.items()))

        if m.get("cost_frac") is not None:
            lines.append(f"  costs {m['cost_frac']:.0%} of gross")

        # exit distribution
        ed = m.get("exit_dist")
        if ed:
            lines.append("  exits: " + "  ".join(
                f"{k} {v:.0%}" for k, v in sorted(ed.items(), key=lambda x: -x[1])))

        if self.reasons:
            lines.append("  failed gates: " + "; ".join(self.reasons))
        return "\n".join(lines)


def _by_day_dated(trades) -> List[Tuple[object, float]]:
    """Per-calendar-day net as (date, net) pairs, ascending by date."""
    acc: Dict[object, float] = {}
    for t in trades:
        acc[t.date] = acc.get(t.date, 0.0) + t.net
    return [(d, acc[d]) for d in sorted(acc)]


def _by_day(trades) -> List[float]:
    """Per-calendar-day net, ordered by date."""
    return [x for _, x in _by_day_dated(trades)]


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


def _sortino(daily: List[float], years: float) -> float:
    """Sortino ratio: mean / std-of-negative-days * sqrt(annualization).
    Uses std of negative trading days as the downside denominator, matching
    the convention in cpr_tearsheet.py."""
    n = len(daily)
    if n < 2:
        return 0.0
    mean = sum(daily) / n
    neg = [x for x in daily if x < 0]
    if len(neg) < 2:
        return 0.0
    n_neg = len(neg)
    mean_neg = sum(neg) / n_neg
    var_neg = sum((x - mean_neg) ** 2 for x in neg) / (n_neg - 1)
    dn = math.sqrt(var_neg)
    if dn == 0:
        return 0.0
    td_per_year = n / years if years > 0 else n
    return (mean / dn) * math.sqrt(td_per_year)


def _max_drawdown(daily: List[float]) -> float:
    peak = cum = 0.0
    mdd = 0.0
    for x in daily:
        cum += x
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return mdd


def _max_drawdown_info(daily_dated: List[Tuple[object, float]]):
    """Returns (max_dd, peak_date, trough_date, recovery_date).
    All dates are None when there is no drawdown."""
    if not daily_dated:
        return 0.0, None, None, None
    dates = [d for d, _ in daily_dated]
    daily = [x for _, x in daily_dated]
    n = len(daily)

    cum = []
    s = 0.0
    for x in daily:
        s += x
        cum.append(s)

    mdd = 0.0
    peak = 0.0
    cur_peak_i = 0
    best_peak_i = 0
    best_trough_i = 0

    for i in range(n):
        if cum[i] > peak:
            peak = cum[i]
            cur_peak_i = i
        dd = cum[i] - peak
        if dd < mdd:
            mdd = dd
            best_peak_i = cur_peak_i
            best_trough_i = i

    if mdd == 0.0:
        return 0.0, None, None, None

    peak_level = cum[best_peak_i]
    recovery_date = None
    for i in range(best_trough_i + 1, n):
        if cum[i] >= peak_level:
            recovery_date = dates[i]
            break

    return mdd, dates[best_peak_i], dates[best_trough_i], recovery_date


def _quantile(sorted_data: List[float], q: float) -> float:
    """Linear-interpolation quantile on a pre-sorted list."""
    n = len(sorted_data)
    if n == 0:
        return 0.0
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_data[lo] * (1 - frac) + sorted_data[hi] * frac


def _var_cvar(daily: List[float], conf: float = 0.95) -> Tuple[float, float]:
    """Historical-simulation VaR and CVaR at the given confidence level.
    Returns (var, cvar) as raw quantile values — negative means a daily loss."""
    if len(daily) < 2:
        return 0.0, 0.0
    sd = sorted(daily)
    var = _quantile(sd, 1 - conf)
    tail = [x for x in sd if x <= var]
    cvar = sum(tail) / len(tail) if tail else var
    return var, cvar


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
             capital: Optional[float] = None,
             min_trades: int = MIN_TRADES) -> Verdict:
    """Run the full edge battery on ``result`` and classify GO / MARGINAL / NO-GO.

    Args:
        result:     backtest Result with a ``.trades`` iterable.
        name:       label for the report header.
        gross:      gross P&L (before costs) — used to compute cost_frac if provided.
        capital:    starting capital in Rs — enables CAGR and total_return_pct.
        min_trades: override the minimum-sample gate (default MIN_TRADES).
    """
    trades = list(getattr(result, "trades", []))
    n = len(trades)
    nets = [float(t.net) for t in trades]
    net = sum(nets)
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    gross_loss = -sum(losses)
    pf = (sum(wins) / gross_loss) if gross_loss > 0 else (float("inf") if wins else 0.0)

    daily_dated = _by_day_dated(trades)
    daily = [x for _, x in daily_dated]

    dates = [t.date for t in trades]
    years_span = 1.0
    if dates:
        span_days = (max(dates) - min(dates)).days
        years_span = max(span_days / 365.25, 1.0 / 12)

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
        if late_net == 0.0 and early_net == net:
            half = n // 2
            early_net = sum(nets[:half])
            late_net = sum(nets[half:])

    # --- drawdown ---
    mdd, peak_date, trough_date, recovery_date = _max_drawdown_info(daily_dated)

    # --- risk ratios ---
    sharpe = _sharpe(daily, years_span)
    sortino = _sortino(daily, years_span)
    calmar = ((net / years_span) / abs(mdd)) if mdd < 0 else None

    # --- VaR / CVaR (95% and 99%) ---
    var_95, cvar_95 = _var_cvar(daily, 0.95)
    var_99, cvar_99 = _var_cvar(daily, 0.99)

    # --- CAGR / total return (requires capital) ---
    cagr = total_return_pct = None
    if capital and capital > 0:
        total_return_pct = net / capital
        cagr = (1 + total_return_pct) ** (1 / years_span) - 1

    # --- avg daily / weekly ---
    avg_daily = sum(daily) / len(daily) if daily else 0.0
    avg_weekly = avg_daily * 5

    # --- exit distribution ---
    exit_dist: Dict[str, float] = {}
    for t in trades:
        r = getattr(t, "reason", None) or "unknown"
        exit_dist[r] = exit_dist.get(r, 0) + 1
    if exit_dist and n:
        exit_dist = {k: v / n for k, v in exit_dist.items()}

    metrics = {
        "n_trades": n, "net": net, "profit_factor": pf,
        "win_rate": (len(wins) / n) if n else 0.0,
        "t_stat": _t_stat(nets),
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": mdd,
        "peak_date": peak_date, "trough_date": trough_date, "recovery_date": recovery_date,
        "var_95": var_95, "cvar_95": cvar_95,
        "var_99": var_99, "cvar_99": cvar_99,
        "avg_daily": avg_daily, "avg_weekly": avg_weekly,
        "cagr": cagr, "total_return_pct": total_return_pct, "capital": capital,
        "bootstrap_p": _bootstrap_p(nets),
        "early_net": early_net, "late_net": late_net,
        "years_total": years_total, "years_positive": years_positive,
        "by_year": dict(sorted(by_year.items())),
        "tail_excl_net": tail_excl_net,
        "cost_frac": ((gross - net) / gross) if (gross and gross > 0) else None,
        "exit_dist": exit_dist,
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
