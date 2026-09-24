"""Extend the NIFTY50 INDEX spot parquet via Kite historical API.

Fetches 1-min OHLC for NIFTY50 (instrument 256265) from the gap between the
existing index_data.parquet end and today, caches by month to data_spot_kite/,
then writes a new batch parquet that SpotSeries.from_index() picks up alongside
the existing data.

Usage (refresh the daily Kite token first):
    cd /Users/gokul/Desktop/tradealgo && python3 zerodha_auth.py
    python analysis/fetch_nifty_spot.py

The script is idempotent — safe to re-run. The current month is always
re-fetched. Each successfully fetched month is written before the next fetch,
so a crash mid-run resumes by skipping already-cached complete months.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.equity.kite_loader import fetch_months, load_cached, make_kite

NIFTY_TOKEN = 256265

# Existing index_data.parquet ends here (2026-02-25 15:31 IST).
# Fetch starts from the beginning of that month so we catch any late-arriving bars;
# from_index() deduplicates by DateTime, so overlap is harmless.
FETCH_START = date(2026, 2, 1)

# Trim boundary: bars up to and including this date already live in the
# existing parquet — the new batch file starts the day after.
EXISTING_END_DATE = date(2026, 2, 25)

DATA_ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "

# Month-by-month cache (same pattern as kite_loader equity cache).
CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data_spot_kite",
)

# SpotSeries.from_index() globs root/*/Data-v3/instrument=NIFTY50/INDEX/index_data.parquet
# so this new batch dir is picked up automatically alongside NIFTY50_MERGED.
OUT_DIR = os.path.join(
    DATA_ROOT, "NIFTY50_KITE_2026", "Data-v3", "instrument=NIFTY50", "INDEX"
)
OUT_PATH = os.path.join(OUT_DIR, "index_data.parquet")


def fetch_and_write(
    fetch_fn,
    start: date = FETCH_START,
    end: date | None = None,
    cache_dir: str = CACHE_DIR,
    out_path: str = OUT_PATH,
    existing_end: date = EXISTING_END_DATE,
) -> int:
    """Fetch NIFTY50 spot from *start* to *end* (default: today), trim to bars
    strictly after *existing_end*, and write to *out_path*.

    Returns the number of new 1-min bars written (0 if nothing new).
    Separated from ``main()`` so tests can inject a fake fetch_fn and tmp paths.
    """
    today = end or date.today()
    written_months, skipped = fetch_months(
        fetch_fn=fetch_fn,
        symbol="NIFTY50",
        token=NIFTY_TOKEN,
        start=start,
        end=today,
        cache_dir=cache_dir,
        today=today,
    )
    print(f"Kite fetch: {written_months} months written, {skipped} skipped")

    df = load_cached("NIFTY50", cache_dir=cache_dir)
    cutoff = pd.Timestamp(existing_end) + pd.Timedelta(days=1)
    df = df[df["DateTime"] >= cutoff].copy().reset_index(drop=True)

    if df.empty:
        print("No new bars beyond existing data end — nothing to write.")
        return 0

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_parquet(out_path, index=False)
    print(
        f"Written {len(df)} bars  "
        f"({df['DateTime'].iloc[0]}  →  {df['DateTime'].iloc[-1]})"
    )
    print(f"Output: {out_path}")
    return len(df)


def main() -> None:
    kite = make_kite()
    fetch_and_write(fetch_fn=kite.historical_data)


if __name__ == "__main__":
    main()
