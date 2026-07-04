"""OptionQuote exposes IV + Greeks (pass-through from the Data-v3 parquet).

Signals can read everything (the user chose 'everything for signals'). IV/Gamma/Vega
are chain-level; Theta/Rho are per-leg. All are optional pass-through: present in real
data, None when a frame lacks the column (no BS recompute — spec P3-4).
"""
from datetime import datetime

import pandas as pd

from engine.data.chain import Chain


def _frame_with_greeks():
    return pd.DataFrame([{
        "DateTime": datetime(2024, 1, 1, 9, 15), "StrikePrice": 20000, "Forward": 20000.0,
        "IV": 0.15, "Gamma": 0.001, "Vega": 12.0,
        "CallOpen": 100, "CallHigh": 100, "CallLow": 100, "CallClose": 100,
        "CallVolume": 500, "CallOI": 30000, "CallDelta": 0.5, "CallTheta": -8.0,
        "CallRho": 3.0, "CallIsTradedStrike": True,
        "PutOpen": 90, "PutHigh": 90, "PutLow": 90, "PutClose": 90,
        "PutVolume": 400, "PutOI": 25000, "PutDelta": -0.5, "PutTheta": -7.0,
        "PutRho": -2.0, "PutIsTradedStrike": True,
    }])


def test_quote_exposes_iv_and_greeks():
    snap = Chain(_frame_with_greeks()).snapshot_at(datetime(2024, 1, 1, 9, 15))
    c = snap.call(20000)
    assert c.iv == 0.15 and c.gamma == 0.001 and c.vega == 12.0
    assert c.theta == -8.0 and c.rho == 3.0           # per-leg (Call)
    p = snap.put(20000)
    assert p.theta == -7.0 and p.rho == -2.0          # per-leg (Put)
    assert p.iv == 0.15 and p.gamma == 0.001          # chain-level shared


def test_greeks_none_when_column_absent(simple_chain_df):
    # simple_chain_df has IV but not Gamma/Vega/Theta/Rho -> those are None
    snap = Chain(simple_chain_df).snapshot_at(datetime(2024, 1, 1, 9, 15))
    c = snap.call(20000)
    assert c.iv == 0.15
    assert c.gamma is None and c.vega is None and c.theta is None and c.rho is None
