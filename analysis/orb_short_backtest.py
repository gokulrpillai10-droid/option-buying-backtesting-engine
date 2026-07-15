"""ADANIENT ORB fade-short backtest — OOS train/test, net of Zerodha MIS costs.

The only fitted parameter is the ramp regime gate (which days to stand aside).
Gate variants are swept on the TRAIN split then FROZEN for the TEST split, so
the reported test edge is honest out-of-sample. Everything else is fixed by
design from the pattern study: 15m OR, first 5m close below OR-low, pre-11:00
trigger, no gap-up days, OR-high stop, EOD square-off.

Run: PYTHONPATH=<repo> python3 analysis/orb_short_backtest.py
"""
from datetime import date

import pandas as pd

from engine.equity.backtest import EquityBacktest, EquityKnobs
from engine.equity.costs import EquityCostModel
from engine.equity.kite_loader import load_cached
from strategies.orb_short_equity import OrbShortEquity

SYMBOL = "ADANIENT"
QTY = 500                 # fixed shares/trade (None -> 100k capital sizing)
TRAIN_END = date(2024, 12, 31)
TEST_START = date(2025, 1, 1)


def daily_closes(df):
    day = df["DateTime"].dt.normalize()
    return df.groupby(day)["Close"].last()          # indexed by session date


def make_gates(closes):
    """Regime-gate factories. Each returns date -> bool (True = stand aside)
    using PRIOR sessions' closes only (no look-ahead). Insufficient history
    -> don't skip."""
    c = closes.sort_index()
    dates = [d.date() for d in c.index]
    pos = {d: i for i, d in enumerate(dates)}

    def nd_high(n):
        def skip(d):
            i = pos.get(d)
            if i is None or i < n:
                return False
            prior = c.iloc[max(0, i - n):i]          # last n closes before d
            return prior.iloc[-1] >= prior.max()     # yesterday = n-day high
        return skip

    def ret5(x):
        def skip(d):
            i = pos.get(d)
            if i is None or i < 6:
                return False
            return c.iloc[i - 1] / c.iloc[i - 6] - 1 > x
        return skip

    return {"none": None,
            "10d_high": nd_high(10), "20d_high": nd_high(20),
            "5d_ret>3%": ret5(0.03), "5d_ret>5%": ret5(0.05),
            "5d_ret>8%": ret5(0.08)}


def report(tag, r):
    print(f"  {tag:<12} trades={len(r.trades):3d}  net={r.net():>10,.0f}  "
          f"PF={r.profit_factor():5.2f}  win={r.win_rate():5.1%}  "
          f"tail-3={r.tail_excl(3):>10,.0f}  t={r.t_stat():+.2f}")


def main():
    df = load_cached(SYMBOL)
    closes = daily_closes(df)
    gates = make_gates(closes)
    bt = EquityBacktest(df, EquityCostModel(), EquityKnobs(timeframe="5min"))
    sizing = f"qty={QTY}/trade" if QTY else "capital=100k"
    print(f"{SYMBOL} bars={len(df):,}  {df['DateTime'].min().date()} -> "
          f"{df['DateTime'].max().date()}   slippage=0.05%  {sizing}\n")

    print(f"TRAIN sweep (through {TRAIN_END}):")
    best_name, best_net = None, float("-inf")
    for name, gate in gates.items():
        r = bt.run(OrbShortEquity(qty=QTY, regime_skip=gate), end=TRAIN_END)
        report(name, r)
        if len(r.trades) >= 25 and r.net() > best_net:
            best_name, best_net = name, r.net()
    print(f"\nfrozen gate: {best_name}\n")

    def yearly(r):
        by = r.by_year()
        n = pd.Series([t.date.year for t in r.trades]).value_counts()
        for y in sorted(by):
            print(f"    {y}: net={by[y]:>10,.0f}  trades={n.get(y, 0):3d}")

    r_train = bt.run(OrbShortEquity(qty=QTY, regime_skip=gates[best_name]),
                     end=TRAIN_END)
    print("TRAIN year-by-year (fitted, in-sample):")
    yearly(r_train)

    print(f"\nTEST ({TEST_START} ->, frozen OOS):")
    r_test = bt.run(OrbShortEquity(qty=QTY, regime_skip=gates[best_name]),
                    start=TEST_START)
    report(best_name, r_test)
    print("  TEST year-by-year (frozen OOS):")
    yearly(r_test)
    print("\n  ablation (test, NOT the frozen pick):")
    report("none", bt.run(OrbShortEquity(qty=QTY, regime_skip=None),
                          start=TEST_START))

    reasons = pd.Series([t.reason for t in r_test.trades]).value_counts()
    print(f"\n  test exits: {dict(reasons)}")
    print(f"  skipped days (data gates): {len(bt.skipped_days)}")

    # constant-risk cross-check: fixed qty across a 112->4000 price history
    # makes rupee years incomparable; 100k-capital sizing normalizes it
    print("\nCROSS-CHECK, capital=100k sizing (same frozen gate):")
    rc_train = bt.run(OrbShortEquity(regime_skip=gates[best_name]), end=TRAIN_END)
    rc_test = bt.run(OrbShortEquity(regime_skip=gates[best_name]), start=TEST_START)
    print(f"  train PF={rc_train.profit_factor():.2f} t={rc_train.t_stat():+.2f}; "
          f"test PF={rc_test.profit_factor():.2f} t={rc_test.t_stat():+.2f}")
    print("  train years:"); yearly(rc_train)
    print("  test years:"); yearly(rc_test)


if __name__ == "__main__":
    main()
