"""ADANIENT gap-down continuation short — full-period backtest, year by year.

Honesty note: unlike the ORB runner there is no train sweep here — the spec
was fixed from the gap_breakout_study before this backtest ran. BUT the two
refinements (gap capped at 1.5%, drive >= 0.25%) were observed on the FULL
6.5y study window, so 2025-26 is not pristine OOS for them. To keep that
visible, the LOOSE variant (gap >= 0.5%, any drive below open — the raw
6-of-7-years observation) runs alongside the SPEC variant, and both report
2020-24 vs 2025-26 aggregates separately.

Run: PYTHONPATH=<repo> python3 analysis/gap_drive_backtest.py
"""
from datetime import date

import pandas as pd

from engine.equity.backtest import EquityBacktest, EquityKnobs
from engine.equity.costs import EquityCostModel
from engine.equity.kite_loader import load_cached
from strategies.gap_drive_short_equity import GapDriveShortEquity

SYMBOL = "ADANIENT"
QTY = 500
SPLIT = date(2024, 12, 31)          # study-era years vs recent years

VARIANTS = {
    "spec":  dict(gap_min=0.005, gap_max=0.015, drive_thr=0.0025),
    "loose": dict(gap_min=0.005, gap_max=1.0, drive_thr=0.0),
}


def report(tag, r):
    print(f"  {tag:<10} trades={len(r.trades):3d}  net={r.net():>10,.0f}  "
          f"PF={r.profit_factor():5.2f}  win={r.win_rate():5.1%}  "
          f"tail-3={r.tail_excl(3):>10,.0f}  t={r.t_stat():+.2f}")


def main():
    df = load_cached(SYMBOL)
    bt = EquityBacktest(df, EquityCostModel(), EquityKnobs(timeframe="5min"))
    print(f"{SYMBOL} bars={len(df):,}  {df['DateTime'].min().date()} -> "
          f"{df['DateTime'].max().date()}   slippage=0.05%  qty={QTY}\n")

    for name, kw in VARIANTS.items():
        strat = lambda: GapDriveShortEquity(qty=QTY, **kw)
        r_all = bt.run(strat())
        print(f"== {name.upper()}  (gap {kw['gap_min']:.1%}-"
              f"{min(kw['gap_max'], 0.10):.1%}, drive>={kw['drive_thr']:.2%}) ==")
        report("full", r_all)
        report("2020-24", bt.run(strat(), end=SPLIT))
        report("2025-26", bt.run(strat(), start=date(2025, 1, 1)))
        by = r_all.by_year()
        n = pd.Series([t.date.year for t in r_all.trades]).value_counts()
        for y in sorted(by):
            print(f"    {y}: net={by[y]:>10,.0f}  trades={n.get(y, 0):2d}")
        reasons = pd.Series([t.reason for t in r_all.trades]).value_counts()
        print(f"  exits: {dict(reasons)}\n")


if __name__ == "__main__":
    main()
