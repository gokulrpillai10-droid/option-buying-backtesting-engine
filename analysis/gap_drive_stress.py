"""Gap-drive short — slippage/latency stress matrix (the pre-go-live gate).

The committed backtest (gap_drive_backtest.py) ran flat 0.05% slip. This runs
the same two variants under harsher, state-dependent friction and entry
latency; the strategy survives only if the LOOSE spec (the actual survivor —
gap>=0.5%, below open at 09:30, OR-high stop, EOD) keeps PF meaningfully
above 1 and its positive-years count under the WORST row:

  base        flat 0.05%/side (the committed number)
  rk=0.05     + 0.05 x (fill-minute range/mid) — fast tape pays more
  rk=0.10     + 0.10 x range/mid (harsh: a 1% fill minute adds 0.1%/side)
  2x flat     flat 0.10%/side
  worst       flat 0.10%/side + rk=0.10
  delay1      flat 0.05%/side, entry filled one 5-min bar late

Run: PYTHONPATH=<repo> python3 analysis/gap_drive_stress.py
"""
from datetime import date

from engine.equity.backtest import EquityBacktest, EquityKnobs
from engine.equity.costs import EquityCostModel
from engine.equity.kite_loader import load_cached
from strategies.gap_drive_short_equity import GapDriveShortEquity

SYMBOL = "ADANIENT"
QTY = 500

VARIANTS = {
    "loose": dict(gap_min=0.005, gap_max=1.0, drive_thr=0.0),
    "spec":  dict(gap_min=0.005, gap_max=0.015, drive_thr=0.0025),
}

CONFIGS = {
    "base":    EquityKnobs(),
    "rk=0.05": EquityKnobs(slippage_range_k=0.05),
    "rk=0.10": EquityKnobs(slippage_range_k=0.10),
    "2x flat": EquityKnobs(slippage_pct=0.0010),
    "worst":   EquityKnobs(slippage_pct=0.0010, slippage_range_k=0.10),
    "delay1":  EquityKnobs(entry_delay_bars=1),
}


def main():
    df = load_cached(SYMBOL)
    print(f"{SYMBOL} bars={len(df):,}  {df['DateTime'].min().date()} -> "
          f"{df['DateTime'].max().date()}   qty={QTY}\n")
    for vname, kw in VARIANTS.items():
        print(f"== {vname.upper()} ==")
        print(f"{'config':<9}{'trades':>7}{'Net Rs':>11}{'PF':>7}{'Win%':>6}"
              f"{'t':>7}{'+yrs':>6}")
        for cname, knobs in CONFIGS.items():
            bt = EquityBacktest(df, EquityCostModel(), knobs)
            r = bt.run(GapDriveShortEquity(qty=QTY, **kw))
            by = r.by_year()
            pos_years = sum(1 for v in by.values() if v > 0)
            print(f"{cname:<9}{len(r.trades):>7}{r.net():>11,.0f}"
                  f"{r.profit_factor():>7.2f}{r.win_rate()*100:>5.0f}%"
                  f"{r.t_stat():>+7.2f}{pos_years:>4d}/{len(by)}")
        print()


if __name__ == "__main__":
    main()
