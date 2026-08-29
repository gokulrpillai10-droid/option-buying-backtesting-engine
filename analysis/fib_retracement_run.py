"""Backtest fib_retracement on our data + the shared GO/NO-GO edge verdict."""
import sys
from datetime import date

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from analysis.evaluate import evaluate
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
    print(evaluate(res, name="fib_retracement").report())
    # Beat-the-nulls (strategy-specific): build null Results and use
    # analysis.evaluate.compare_to_nulls(res.net(), {"always-short": ..., ...}).
    return 0


if __name__ == "__main__":
    sys.exit(main())
