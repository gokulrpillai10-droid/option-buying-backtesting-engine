"""Market-breadth daily series for the vol-surface regime detector.

Pulls daily closes for the NIFTY-50 basket from Kite and computes breadth =
% of constituents trading above their 50-DMA (a market-internals gauge, orthogonal
to the vol surface). Writes analysis/nifty_breadth_daily.csv.

v1 uses a FIXED CURRENT basket (survivorship-biased for old dates -- point-in-time
membership is a separate data project; flagged in TODOS). Prereq: fresh Kite token.
    PYTHONPATH=. python3 analysis/breadth_fetch.py
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from engine.equity.kite_loader import make_kite

NIFTY_CSV = "analysis/nifty_daily.csv"
OUT_CSV = "analysis/nifty_breadth_daily.csv"
START = date(2019, 6, 1)
MA = 50

# Fixed current NIFTY-50 basket (best-effort; unresolved symbols are skipped).
NIFTY50 = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "ITC", "LT", "KOTAKBANK",
    "AXISBANK", "SBIN", "BHARTIARTL", "BAJFINANCE", "ASIANPAINT", "HINDUNILVR",
    "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND", "WIPRO", "ONGC",
    "NTPC", "POWERGRID", "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "HCLTECH", "TECHM",
    "ADANIENT", "ADANIPORTS", "COALINDIA", "GRASIM", "HINDALCO", "DRREDDY", "CIPLA",
    "BAJAJFINSV", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO", "BPCL", "BRITANNIA",
    "APOLLOHOSP", "INDUSINDBK", "TATACONSUM", "SBILIFE", "HDFCLIFE", "SHRIRAMFIN",
    "LTIM", "TRENT", "BEL",
]


def _closes(kite, token, start, end):
    """Daily closes, chunked into <2000-day windows (Kite's day-interval cap)."""
    recs = []
    lo = start
    while lo <= end:
        hi = min(lo + timedelta(days=1400), end)
        recs += kite.historical_data(
            token, datetime.combine(lo, datetime.min.time()),
            datetime.combine(hi, datetime.max.time().replace(microsecond=0)), "day")
        lo = hi + timedelta(days=1)
    if not recs:
        return None
    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    return df.drop_duplicates("date").set_index("date")["close"].astype(float)


def main():
    kite = make_kite()
    inst = kite.instruments("NSE")
    tok = {i["tradingsymbol"]: i["instrument_token"] for i in inst
           if i.get("segment") == "NSE" and i.get("instrument_type") == "EQ"}
    got = {s: tok[s] for s in NIFTY50 if s in tok}
    print(f"resolved {len(got)}/{len(NIFTY50)} constituents")

    end = date.today()
    series = {}
    for i, (s, t) in enumerate(got.items(), 1):
        try:
            c = _closes(kite, t, START, end)
            if c is not None and len(c) > MA:
                series[s] = c
        except Exception as e:
            print(f"  skip {s}: {e}")
    print(f"fetched {len(series)} constituents with data")

    closes = pd.DataFrame(series).sort_index()
    ma = closes.rolling(MA).mean()
    above = closes > ma
    breadth = above.where(closes.notna()).mean(axis=1)      # fraction above 50-DMA
    n = closes.notna().sum(axis=1)

    nifty = pd.read_csv(NIFTY_CSV, parse_dates=["date"]).set_index("date").index
    out = pd.DataFrame({"breadth": breadth, "n_constituents": n}).reindex(
        nifty.normalize()).dropna(subset=["breadth"])
    out.index.name = "date"
    out.to_csv(OUT_CSV)
    print(f"breadth {out.index.min().date()}..{out.index.max().date()}  "
          f"n={len(out)} days  latest={out['breadth'].iloc[-1]:.0%} above 50-DMA "
          f"({int(out['n_constituents'].iloc[-1])} stocks)")
    print(f"-> {OUT_CSV}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
