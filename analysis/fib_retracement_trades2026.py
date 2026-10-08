"""Dump every 2026 fib_retracement trade."""
import sys
from datetime import date

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from strategies.fib_retracement import FibRetracement

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)
SLIP = 0.0025


def main():
    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    md = MarketData(cat, spot, min_dte=3)
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=SLIP))
    res = bt.run(FibRetracement(), START, END)

    t26 = [t for t in res.trades if getattr(t, "date", None) and t.date.year == 2026]
    t26.sort(key=lambda t: (t.date, t.entry_dt or 0))

    print(f"2026 fib_retracement trades: {len(t26)}")
    hdr = f"{'date':<11}{'side':<5}{'strike':>7}{'qty':>5}{'entry':>9}{'exit':>9}" \
          f"{'gross':>11}{'cost':>9}{'net':>11}  {'entry_dt':<17}{'exit_dt':<17}reason"
    print(hdr)
    print("-" * len(hdr))
    tot = 0.0
    wins = 0
    for t in t26:
        tot += t.net
        wins += 1 if t.net > 0 else 0
        print(f"{str(t.date):<11}{t.side:<5}{t.strike:>7}{t.qty:>5}"
              f"{t.entry:>9.2f}{t.exit:>9.2f}{t.gross:>11,.0f}{t.cost:>9,.0f}"
              f"{t.net:>11,.0f}  {str(t.entry_dt):<17}{str(t.exit_dt):<17}{t.reason}")
    print("-" * len(hdr))
    n = len(t26)
    print(f"TOTAL 2026 net Rs{tot:,.0f}   win {wins}/{n} ({(wins/n if n else 0):.0%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
