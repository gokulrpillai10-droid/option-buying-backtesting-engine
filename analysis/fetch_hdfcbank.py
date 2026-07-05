"""One-shot HDFCBANK 1-min fetch/refresh into data_equity/ (resumable).

Prereq: refresh the daily Kite token first —
    cd /Users/gokul/Desktop/tradealgo && python3 zerodha_auth.py

Re-running skips complete cached months and refetches only the current month,
so it doubles as the daily/weekly refresh command.
"""
from datetime import date

from engine.equity.kite_loader import (INSTRUMENT_TOKENS, fetch_months,
                                       load_cached, make_kite)

START = date(2020, 1, 1)      # ~matches the NIFTY store's range

kite = make_kite()
written, skipped = fetch_months(kite.historical_data, "HDFCBANK",
                                INSTRUMENT_TOKENS["HDFCBANK"],
                                start=START, end=date.today())
print(f"months written={written} skipped={skipped}")
df = load_cached("HDFCBANK")
print(f"bars={len(df):,}  {df['DateTime'].min()} -> {df['DateTime'].max()}")
