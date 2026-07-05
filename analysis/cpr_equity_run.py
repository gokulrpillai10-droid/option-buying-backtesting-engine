"""CPR-equity target sweep on HDFCBANK (spec 2026-07-03).

Raw unfiltered CPR cross, long + short, fixed Rs100k/trade, Zerodha MIS costs,
0.05% slip. Sweeps the single price-point target the same way the options
CPR's +50 premium target was found. target=None = stop/EOD only (baseline).
"""
from engine.equity.backtest import EquityBacktest
from engine.equity.costs import EquityCostModel
from engine.equity.kite_loader import load_cached
from strategies.cpr_breakout_equity import CPRBreakoutEquity

df = load_cached("HDFCBANK")
bt = EquityBacktest(df, EquityCostModel())
print(f"bars={len(df):,}  {df['DateTime'].min().date()} -> {df['DateTime'].max().date()}\n")

for tgt in (None, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0):
    r = bt.run(CPRBreakoutEquity(target_pts=tgt))
    label = "none" if tgt is None else f"{tgt:g}"
    print(f"target={label:>5}: trades={len(r.trades):4d}  net={r.net():>12,.0f}  "
          f"PF={r.profit_factor():5.2f}  win={r.win_rate():5.1%}  "
          f"tail-5={r.tail_excl(5):>12,.0f}")
    print(f"              by_year: "
          f"{{{', '.join(f'{y}: {v:,.0f}' for y, v in sorted(r.by_year().items()))}}}")

print(f"\nskipped days ({len(bt.skipped_days)}):", bt.skipped_days)
