"""Shared test fixtures: tiny synthetic option chains.

These mirror the real Data-v3 schema (per-minute long format, one row per
(DateTime, StrikePrice)) but stay small so unit tests are fast and deterministic.
"""
from datetime import datetime

import pandas as pd
import pytest

# Real-schema columns the engine relies on.
CHAIN_COLUMNS = [
    "DateTime", "StrikePrice", "Forward", "IV",
    "CallOpen", "CallHigh", "CallLow", "CallClose", "CallVolume", "CallOI",
    "CallDelta", "CallIsTradedStrike",
    "PutOpen", "PutHigh", "PutLow", "PutClose", "PutVolume", "PutOI",
    "PutDelta", "PutIsTradedStrike",
]


def make_row(dt, strike, *, forward=20000.0, iv=0.15,
             call_open=100.0, call_high=None, call_low=None, call_close=None,
             call_vol=500, call_oi=30000, call_delta=0.5, call_traded=True,
             put_open=100.0, put_high=None, put_low=None, put_close=None,
             put_vol=500, put_oi=30000, put_delta=-0.5, put_traded=True):
    return {
        "DateTime": dt, "StrikePrice": strike, "Forward": forward, "IV": iv,
        "CallOpen": call_open,
        "CallHigh": call_high if call_high is not None else call_open,
        "CallLow": call_low if call_low is not None else call_open,
        "CallClose": call_close if call_close is not None else call_open,
        "CallVolume": call_vol, "CallOI": call_oi,
        "CallDelta": call_delta, "CallIsTradedStrike": call_traded,
        "PutOpen": put_open,
        "PutHigh": put_high if put_high is not None else put_open,
        "PutLow": put_low if put_low is not None else put_open,
        "PutClose": put_close if put_close is not None else put_open,
        "PutVolume": put_vol, "PutOI": put_oi,
        "PutDelta": put_delta, "PutIsTradedStrike": put_traded,
    }


@pytest.fixture
def simple_chain_df():
    """Two minutes (09:15, 09:16), three strikes (19900/20000/20100)."""
    rows = []
    t1 = datetime(2024, 1, 1, 9, 15)
    t2 = datetime(2024, 1, 1, 9, 16)
    for dt, base in ((t1, 100.0), (t2, 110.0)):
        rows.append(make_row(dt, 19900, call_open=base + 50, put_open=base - 40))
        rows.append(make_row(dt, 20000, call_open=base, put_open=base))
        rows.append(make_row(dt, 20100, call_open=base - 40, put_open=base + 50))
    return pd.DataFrame(rows, columns=CHAIN_COLUMNS)
