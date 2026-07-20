"""Real-data slice: catches chain-selection / spot_at / settled-open wiring bugs
that synthetic units miss. Skips cleanly if the local NIFTY cache is absent."""
import os
from datetime import date

import pytest

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
pytestmark = pytest.mark.skipif(not os.path.isdir(ROOT),
                                reason="local NIFTY option cache not present")


def test_fade_produces_trades_on_a_known_gap_month():
    from engine.backtest import Backtest, Knobs
    from engine.data.catalog import Catalog
    from engine.data.market import MarketData
    from engine.data.spot import SpotSeries
    from engine.execution.costs import CostModel
    from strategies.gap_fill_fade import GapFillFade

    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    md = MarketData(cat, spot, min_dte=1)
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025))
    res = bt.run(GapFillFade(arm="fade"), date(2024, 6, 1), date(2024, 6, 30))
    # at least one in-band gap day in the month; each trade is a PUT or CALL,
    # exits only via the sanctioned reasons.
    assert len(res.trades) >= 1
    for t in res.trades:
        assert t.side in ("CALL", "PUT")
        assert t.reason in ("book", "ptrail", "pstop", "eod")
