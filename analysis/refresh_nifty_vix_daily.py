"""Refresh NIFTY 50 + India VIX DAILY OHLC into the regime detector's CSVs.

Pulls day-interval bars from Kite for NIFTY 50 and India VIX, then merges
(dedupe by date, keep latest) into analysis/nifty_daily.csv and
analysis/india_vix.csv — the two files analysis/regime_vol.py reads. Safe to
re-run: it only adds missing/partial recent days.

Prereq: refresh the daily Kite token first —
    cd /Users/gokul/Desktop/tradealgo && python3 zerodha_auth.py
"""
from datetime import date, datetime

import pandas as pd

from engine.equity.kite_loader import make_kite

TARGETS = {
    "analysis/nifty_daily.csv": "NSE:NIFTY 50",
    "analysis/india_vix.csv": "NSE:INDIA VIX",
}
FETCH_FROM = date(2026, 7, 1)          # generous overlap; dedupe cleans the seam


def _resolve_tokens(kite, symbols):
    """symbol -> instrument_token via a live LTP call (verifies the token too)."""
    q = kite.ltp(symbols)
    return {s: q[s]["instrument_token"] for s in symbols}


def _fetch_daily(kite, token, start, end):
    recs = kite.historical_data(token,
                                datetime.combine(start, datetime.min.time()),
                                datetime.combine(end, datetime.max.time().replace(microsecond=0)),
                                "day")
    if not recs:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.date
    return df[["date", "open", "high", "low", "close"]]


def _merge_into(path, new_df):
    old = pd.read_csv(path)
    old["date"] = pd.to_datetime(old["date"]).dt.date
    merged = (pd.concat([old, new_df], ignore_index=True)
              .drop_duplicates("date", keep="last")   # new bars win (heal partials)
              .sort_values("date").reset_index(drop=True))
    merged.to_csv(path, index=False)
    return old["date"].max(), merged["date"].max(), len(merged) - len(old)


def main():
    kite = make_kite()
    tokens = _resolve_tokens(kite, list(TARGETS.values()))
    today = date.today()
    for path, symbol in TARGETS.items():
        new = _fetch_daily(kite, tokens[symbol], FETCH_FROM, today)
        was, now, added = _merge_into(path, new)
        print(f"{path:<28} {symbol:<15} {was} -> {now}  (+{added} rows)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
