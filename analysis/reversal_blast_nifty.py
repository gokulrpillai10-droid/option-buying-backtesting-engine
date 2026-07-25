"""Intraday Reversal Blast on NIFTY (index), 2019-12 -> 2026-02.

Same signal engine as analysis/reversal_blast_run.py (3-oscillator extreme on
5-min + green/red signal candle + 1-min 5/15 EMA trigger), but on the NIFTY
INDEX instead of ADANIENT cash. Two things change:

  * P&L is in INDEX POINTS (futures-style), lot-agnostic -- the honest unit for
    "does the signal capture net points after friction". Friction = SLIP_PTS
    round-turn (approx a liquid NIFTY-fut spread). No equity STT/brokerage card.
  * The profit target is swept in POINTS (20-75), not 1.25% -- a 1.25% index
    target (~300pt) never fills intraday, so it is not the meaningful test. A
    points target sized near the 5-min signal-candle stop gives a real R:R.

Motivation (durable repo fact, gap-fill-fade-project): the INDEX mean-reverts
while single stocks trend. ADANIENT killed this setup as a falling-knife; NIFTY
is where a reversal thesis has its best structural shot. Same dir-null control:
if the reversal call has edge it must beat a coin-flip of direction.

Run: PYTHONPATH=<repo> python3 analysis/reversal_blast_nifty.py
"""
from __future__ import annotations

import random
from datetime import timedelta

import pandas as pd

from engine.backtest import Result
from engine.data.spot import SpotSeries
from engine.position.manager import Trade
from analysis.reversal_blast_run import (
    build_5min, day_signals, prep_1min, _trigger_idx, _first_idx_after,
    _resolve_exit, SQUARE, TIMEOUT)

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
SLIP_PTS = 1.0          # round-turn friction ~ liquid NIFTY-fut spread (~0.004%)
SEED = 7


def load_nifty_1min() -> pd.DataFrame:
    return SpotSeries.from_index(ROOT, "1min")._df.copy()


def _pts_trade(direction, entry_open, exit_raw, reason, day, entry_dt, exit_dt):
    """Points-based Trade: net = captured points - round-turn slippage."""
    gross = (exit_raw - entry_open) if direction == "long" else (entry_open - exit_raw)
    net = gross - 2 * SLIP_PTS
    return Trade(side=direction.upper(), strike=0, qty=1, entry=entry_open,
                 exit=exit_raw, gross=gross, cost=2 * SLIP_PTS, net=net,
                 reason=reason, date=day, entry_dt=entry_dt, exit_dt=exit_dt)


def run(m5, rows_by_day, mode, tgt_pts) -> Result:
    """mode in {full, noema, hard50, dirnull}. Point target only (no recross)."""
    res = Result()
    rng = random.Random(SEED)
    for day, m5day in m5.groupby("day"):
        rows = rows_by_day.get(day)
        if not rows:
            continue
        for exec_start, direction, stop_lvl, opp_lvl in day_signals(m5day):
            if mode == "dirnull" and rng.random() < 0.5:
                direction = "long" if direction == "short" else "short"
                stop_lvl = opp_lvl
            if mode == "noema":
                ei = _first_idx_after(rows, exec_start)
            else:
                ei = _trigger_idx(rows, exec_start, direction, hard50=(mode == "hard50"))
            if ei is None or ei >= len(rows):
                continue
            entry_open = rows[ei].Open
            if rows[ei].DateTime.time() >= SQUARE:
                continue
            xraw, reason, xdt = _resolve_exit(rows, ei, direction, entry_open,
                                              stop_lvl, use_emax=False, tgt_pts=tgt_pts)
            res.trades.append(_pts_trade(direction, entry_open, xraw, reason, day,
                                         rows[ei].DateTime, xdt))
            break
    return res


def report(tag, r: Result):
    longs = [t for t in r.trades if t.side == "LONG"]
    shorts = [t for t in r.trades if t.side == "SHORT"]
    avg = r.net() / len(r.trades) if r.trades else 0.0
    print(f"  {tag:<14} trades={len(r.trades):4d}  netPts={r.net():>9,.0f}  "
          f"avg={avg:+6.1f}  PF={r.profit_factor():5.2f}  win={r.win_rate():5.1%}  "
          f"t={r.t_stat():+.2f}  [L {len(longs)} / S {len(shorts)}]")


def main():
    df1 = load_nifty_1min()
    print(f"NIFTY bars={len(df1):,}  {df1['DateTime'].min().date()} -> "
          f"{df1['DateTime'].max().date()}   friction={SLIP_PTS:.1f}pt round-turn  "
          f"(P&L in index points)\n")
    m5 = build_5min(df1)
    rows_by_day = prep_1min(df1)

    print("== POINTS-TARGET SWEEP (full entry: osc+candle+EMA, signal-candle stop) ==")
    for pts in (20, 30, 40, 50, 75):
        report(f"{pts}pt", run(m5, rows_by_day, "full", pts))

    print("\n== CONTROLS at 40pt ==")
    r40 = run(m5, rows_by_day, "full", 40)
    report("40pt full", r40)
    report("40pt no-EMA", run(m5, rows_by_day, "noema", 40))
    c40 = run(m5, rows_by_day, "dirnull", 40)
    report("40pt dir-null", c40)

    print("\n== 40pt full: per-year (net points) ==")
    by = r40.by_year()
    n = pd.Series([t.date.year for t in r40.trades]).value_counts()
    for y in sorted(by):
        yl = [t for t in r40.trades if t.date.year == y]
        wr = sum(1 for t in yl if t.net > 0) / len(yl) if yl else 0.0
        print(f"    {y}: netPts={by[y]:>9,.0f}  trades={n.get(y, 0):3d}  win={wr:5.1%}")
    print(f"  exits: {dict(pd.Series([t.reason for t in r40.trades]).value_counts())}")

    print("\n== VERDICT ==")
    a, c = r40.net(), c40.net()
    if a > 0 and a > 1.3 * c:
        print(f"  40pt full nets {a:,.0f}pt vs dir-null {c:,.0f}pt -- the reversal "
              f"call CLEARS the coin-flip on NIFTY. Worth an options/futures cost pass.")
    else:
        print(f"  40pt full nets {a:,.0f}pt vs dir-null {c:,.0f}pt. The direction "
              f"call does {'not clear' if a <= c else 'barely clear'} the coin-flip "
              f"null even gross of real option/fut costs.")
    print("  (Points P&L, friction only. If a cell looks positive here, re-test "
          "through NIFTY options/futures with the real cost card before believing it.)")


if __name__ == "__main__":
    main()
