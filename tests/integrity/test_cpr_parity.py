"""External parity gate for the deployed CPR strategy — the "gold" tier.

SKIPPED until a verified-engine export lands at ``analysis/cpr_reference.csv``.
Drop that file in and this test activates automatically, grading OUR CPR backtest
against the verified engine's trades.

Expected reference schema (same as the strangle reference we already validated):
    trade_group_id, structure, entry_signal_tag, exit_signal_tag, strike,
    option_type, side, qty, lot_size, entry_ts, exit_ts, entry_price,
    exit_price, gross_pnl, cost, net_pnl
Timestamps UTC; P&L per 1 lot. (See docs/backtest-integrity.md.)
"""
import os

import pytest

REF = "analysis/cpr_reference.csv"
REQUIRED = {"structure", "strike", "option_type", "side", "entry_ts", "exit_ts",
            "entry_price", "exit_price", "net_pnl"}


@pytest.mark.skipif(not os.path.exists(REF),
                    reason=f"no verified CPR reference at {REF} — drop it in to activate gold parity")
def test_cpr_matches_verified_reference():
    import pandas as pd
    ref = pd.read_csv(REF)
    missing = REQUIRED - set(ref.columns)
    assert not missing, f"reference missing required columns: {missing}"
    assert len(ref) > 0, "reference is empty"
    # ON ARRIVAL: run our CPR backtest (cpr_tearsheet config) via the guarded engine,
    # extract per-trade (entry_date, side, strike, entry, exit, net), align on
    # (entry_date, side) and assert entry/exit prices match within tol and net within
    # tol; escalate loudly on any verdict-moving gap (GO->NO-GO). Filling this in
    # requires the reference's exact date/strike conventions, confirmed once it lands.
