"""HDFCBANK Gap Router backtest — OOS train/test, net of Zerodha MIS costs.

Thresholds are swept on the TRAIN split (2020-2023) then FROZEN and applied to
the TEST split (2024-2026) so the reported test edge is honest out-of-sample.
Runs on 1-min bars (thin fade edge -> minimal entry lag). Reads only in-repo
cached data (HDFCBANK parquet + data_index/NIFTY snapshot).
"""
from datetime import date

from engine.equity.backtest import EquityBacktest, EquityKnobs
from engine.equity.costs import EquityCostModel
from engine.equity.gap_features import Thresholds
from engine.equity.kite_loader import load_cached
from engine.equity.nifty_gap import NiftyGapProvider
from strategies.gap_router_equity import GapRouterEquity

TRAIN_END = date(2023, 12, 31)
TEST_START = date(2024, 1, 1)


def report(tag, r):
    print(f"  {tag:<14} trades={len(r.trades):4d}  net={r.net():>12,.0f}  "
          f"PF={r.profit_factor():5.2f}  win={r.win_rate():5.1%}  "
          f"tail-5={r.tail_excl(5):>12,.0f}")


def main():
    df = load_cached("HDFCBANK")
    nifty = NiftyGapProvider()
    bt = EquityBacktest(df, EquityCostModel(), EquityKnobs(timeframe="1min"))
    print(f"bars={len(df):,}  {df['DateTime'].min().date()} -> "
          f"{df['DateTime'].max().date()}\n")

    # --- sweep drive_thr / big_thr on TRAIN, pick best net ---
    best, best_net = None, float("-inf")
    for big in (0.003, 0.004, 0.005):
        for drive in (0.002, 0.003, 0.004):
            thr = Thresholds(big_thr=big, drive_thr=drive)
            r = bt.run(GapRouterEquity(nifty, thresholds=thr), end=TRAIN_END)
            if r.net() > best_net:
                best, best_net = thr, r.net()
    print(f"best train thresholds: big_thr={best.big_thr} drive_thr={best.drive_thr}\n")

    print("TRAIN (2020-2023, fitted):")
    report("all", bt.run(GapRouterEquity(nifty, thresholds=best), end=TRAIN_END))
    print("\nTEST (2024-2026, frozen OOS):")
    r_test = bt.run(GapRouterEquity(nifty, thresholds=best), start=TEST_START)
    report("all", r_test)
    print(f"\n  test by_year: "
          f"{{{', '.join(f'{y}: {v:,.0f}' for y, v in sorted(r_test.by_year().items()))}}}")
    print(f"\nskipped days: {len(bt.skipped_days)}")


if __name__ == "__main__":
    main()
