"""Scaffold a new strategy's triplet: strategy class + TDD test + runner.

Usage:
    PYTHONPATH=. python3 analysis/scaffold_strategy.py <name> [--equity]

Emits three files (never overwrites an existing one):
    strategies/<name>.py           the Strategy class (fill in the conditions)
    tests/unit/test_<name>.py      a TDD stub for the entry/exit conditions
    analysis/<name>_run.py         a runner wired to the shared evaluate() verdict

The generated files RUN as-is (the strategy just stands aside until you encode
its conditions), so step 6 of the flow — backtest on our data — works immediately.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _camel(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def _write(rel: str, body: str) -> str:
    path = os.path.join(ROOT, rel)
    if os.path.exists(path):
        raise SystemExit(f"refusing to overwrite existing {rel}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)
    return rel


_OPT_STRAT = '''"""TODO one-line conditions: <when to enter, what to buy, when to exit>."""
from engine.strategy.base import Strategy
from engine.execution.model import Order
from engine.strategy.exit import Exit


class {cls}(Strategy):
    def on_day_start(self, ctx):
        self._entered = False                 # engine is intraday-only; reset daily

    def on_bar(self, ctx):
        # ENTRY conditions. ctx is as-of-now (look-ahead safe): ctx.spot,
        # ctx.spot_bar, ctx.recent(n), ctx.prev_day_hlc(), ctx.prev_close().
        if self._entered:
            return []
        # if <entry condition>:
        #     self._entered = True
        #     return [Order(side="CALL", strike=ctx.atm_strike("CALL"), lots=1)]
        return []

    def manage(self, ctx, pos):
        # EXIT conditions. Return an Exit or None (hold to EOD square-off).
        # e.g. Exit.bracket(stop=pos.entry - 20, target=pos.entry + 50)
        #      Exit.trail(gap=12) / Exit.at(level, "fall") / Exit.market("out")
        return None
'''

_EQ_STRAT = '''"""TODO one-line conditions: <when to enter long/short, when to exit>."""
from engine.strategy.base import Strategy
from engine.equity.model import EquityOrder
from engine.strategy.exit import Exit

CAPITAL = 100_000          # rupees deployed per trade (engine -> share qty at fill)


class {cls}(Strategy):
    def on_day_start(self, ctx):
        self._entered = False

    def on_bar(self, ctx):
        if self._entered:
            return []
        # if <entry condition>:
        #     self._entered = True
        #     return [EquityOrder(direction="long", capital=CAPITAL)]
        return []

    def manage(self, ctx, pos):
        # e.g. Exit.at(level, "fall") for a stop, or Exit.market("out")
        return None
'''

_TEST = '''"""TDD: {name} entry/exit conditions fire correctly on a synthetic bar.

See tests/golden/test_backtest_driver.py for the BarContext/FakeMarket fixture
pattern, and tests/unit/test_cpr_breakout.py for a real strategy's unit test.
"""
from strategies.{name} import {cls}


def test_{name}_constructs():
    assert {cls}() is not None


def test_{name}_stands_aside_before_conditions_encoded():
    # replace with a real synthetic-bar assertion once conditions are written
    pass
'''

_OPT_RUN = '''"""Backtest {name} on our data + the shared GO/NO-GO edge verdict."""
import sys
from datetime import date

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from analysis.evaluate import evaluate
from strategies.{name} import {cls}

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)
SLIP = 0.0025


def main():
    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    md = MarketData(cat, spot, min_dte=3)
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=SLIP))
    res = bt.run({cls}(), START, END)
    print(evaluate(res, name="{name}").report())
    # Beat-the-nulls (strategy-specific): build null Results and use
    # analysis.evaluate.compare_to_nulls(res.net(), {{"always-short": ..., ...}}).
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

_EQ_RUN = '''"""Backtest {name} (equity) on our data + the shared GO/NO-GO edge verdict."""
import sys
from datetime import date

from engine.equity.kite_loader import load_cached
from engine.equity.backtest import EquityBacktest, EquityKnobs
from engine.equity.costs import EquityCostModel
from analysis.evaluate import evaluate
from strategies.{name} import {cls}

SYMBOL = "HDFCBANK"        # or ADANIENT / TATASTEEL


def main():
    df = load_cached(SYMBOL)
    bt = EquityBacktest(df, EquityCostModel(), EquityKnobs(timeframe="5min"))
    res = bt.run({cls}())
    print(evaluate(res, name="{name}").report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    equity = "--equity" in argv
    if len(args) != 1 or not re.fullmatch(r"[a-z][a-z0-9_]*", args[0]):
        raise SystemExit("usage: scaffold_strategy.py <snake_name> [--equity]")
    name = args[0]
    cls = _camel(name)
    strat = (_EQ_STRAT if equity else _OPT_STRAT).format(cls=cls)
    run = (_EQ_RUN if equity else _OPT_RUN).format(cls=cls, name=name)
    made = [
        _write(f"strategies/{name}.py", strat),
        _write(f"tests/unit/test_{name}.py", _TEST.format(cls=cls, name=name)),
        _write(f"analysis/{name}_run.py", run),
    ]
    print("scaffolded (" + ("equity" if equity else "options") + "):")
    for m in made:
        print("  " + m)
    print(f"\nnext: encode conditions in strategies/{name}.py, fill the test, then\n"
          f"  PYTHONPATH=. python3 analysis/{name}_run.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
