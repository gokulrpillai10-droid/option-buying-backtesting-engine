"""HDFCBANK 1-min OHLC via the Kite historical API — chunked, cached, resumable.

Kite caps minute candles at ~60 days/request and rate-limits (~3 req/s), so we
fetch per CALENDAR MONTH (<=31 days), throttle between requests, retry with
exponential backoff, and write each month to its own parquet the moment it
lands — a crash resumes by skipping complete cached months. The CURRENT month
is always refetched (it may have gained days since the last run). The backtest
never touches Kite: it reads only ``load_cached`` output (deterministic,
offline-reproducible).

Credentials live in the tradealgo .env (refresh the daily token there first:
``cd /Users/gokul/Desktop/tradealgo && python3 zerodha_auth.py``). kiteconnect
and dotenv are imported lazily inside ``make_kite`` so the test suite and the
backtest need neither installed.
"""
from __future__ import annotations

import calendar
import glob
import json
import os
import time as _time
from datetime import date, datetime, timedelta
from typing import Callable, List, Optional, Tuple

import pandas as pd

TRADEALGO_ENV = "/Users/gokul/Desktop/tradealgo/.env"
INSTRUMENT_TOKENS = {"HDFCBANK": 341249}          # NSE instrument tokens
DEFAULT_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data_equity")


def month_range(start: date, end: date) -> List[Tuple[date, date]]:
    """Calendar-month windows covering [start, end], clipped to the bounds."""
    out = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        out.append((max(cur, start), min(last, end)))
        cur = last + timedelta(days=1)
    return out


def _normalize(records) -> pd.DataFrame:
    """Kite records (tz-aware 'date', lowercase ohlc) -> naive-IST OHLC frame."""
    if not records:
        return pd.DataFrame(columns=["DateTime", "Open", "High", "Low", "Close"])
    df = pd.DataFrame(records)
    dt = pd.to_datetime(df["date"])
    if getattr(dt.dt, "tz", None) is not None:
        dt = dt.dt.tz_localize(None)                 # drop tz, keep IST wall-clock
    return pd.DataFrame({"DateTime": dt,
                         "Open": df["open"].astype(float),
                         "High": df["high"].astype(float),
                         "Low": df["low"].astype(float),
                         "Close": df["close"].astype(float)})


def clean(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Integrity pass: dedupe/sort by DateTime, drop insane bars (non-positive
    prices, High < max(O,C), Low > min(O,C)). Returns (clean, n_dropped)."""
    df = df.drop_duplicates("DateTime").sort_values("DateTime").reset_index(drop=True)
    if df.empty:
        return df, 0
    ok = ((df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)
          & (df["High"] >= df[["Open", "Close"]].max(axis=1))
          & (df["Low"] <= df[["Open", "Close"]].min(axis=1)))
    return df[ok].reset_index(drop=True), int((~ok).sum())


def _fetch_retry(fetch_fn: Callable, token: int, lo: date, hi: date,
                 max_retries: int, sleep: Callable) -> list:
    if max_retries < 1:
        raise ValueError(f"max_retries must be >= 1, got {max_retries}")
    frm = datetime.combine(lo, datetime.min.time())
    to = datetime.combine(hi, datetime.max.time().replace(microsecond=0))
    for attempt in range(max_retries):
        try:
            return fetch_fn(token, frm, to, "minute")
        except Exception:
            if attempt == max_retries - 1:
                raise
            sleep(2 ** attempt)


def _coverage_path(outdir: str) -> str:
    return os.path.join(outdir, "_coverage.json")


def _read_coverage(outdir: str) -> dict:
    """{"YYYY-MM": "YYYY-MM-DD"} -- the requested end-date each cached month was
    fetched THROUGH (not the data's last bar). Missing/corrupt => empty."""
    p = _coverage_path(outdir)
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_coverage(outdir: str, cov: dict) -> None:
    p = _coverage_path(outdir)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cov, f, indent=0, sort_keys=True)
    os.replace(tmp, p)                               # atomic


def fetch_months(fetch_fn: Callable, symbol: str, token: int,
                 start: date, end: date, cache_dir: str = DEFAULT_CACHE,
                 throttle: float = 0.35, max_retries: int = 5,
                 sleep: Callable = _time.sleep,
                 today: Optional[date] = None) -> Tuple[int, int]:
    """Fetch [start, end] month-by-month into {cache_dir}/{symbol}/{YYYY-MM}.parquet.

    Resumable via a ``_coverage.json`` manifest recording the end-date each month
    was fetched THROUGH. A month is skipped only when it is finalized (``hi <
    today`` -- no new bars can appear) AND its cached coverage already reaches the
    requested end; a month first cached mid-month (partial) is re-fetched once it
    ends, instead of being frozen with a permanent hole. An empty fetch result is
    a transient API failure: it is never persisted (that would cement a gap) and
    never overwrites good cached data -- the month is left to retry. Returns
    (written, skipped)."""
    today = today or date.today()
    outdir = os.path.join(cache_dir, symbol)
    os.makedirs(outdir, exist_ok=True)
    cov = _read_coverage(outdir)
    written = skipped = 0
    for lo, hi in month_range(start, end):
        key = f"{lo:%Y-%m}"
        path = os.path.join(outdir, f"{key}.parquet")
        if key not in cov and os.path.exists(path) and hi < today:
            # legacy parquet from before coverage tracking: a finalized month on
            # disk is assumed complete through its end -- record it so the skip
            # below is explicit and future runs don't re-migrate it.
            cov[key] = hi.isoformat()
        if (os.path.exists(path) and hi < today
                and cov.get(key, "") >= hi.isoformat()):
            skipped += 1
            continue
        recs = _fetch_retry(fetch_fn, token, lo, hi, max_retries, sleep)
        df, _dropped = clean(_normalize(recs))
        if len(df) == 0:
            # never write an empty month (freezes a permanent gap) or overwrite
            # good data with it -- warn and leave it retryable next run.
            print(f"WARNING: 0 bars -- {symbol} {key}: not written (will retry)")
            sleep(throttle)
            continue
        print(f"{symbol} {key}: {len(df)} bars")
        df.to_parquet(path, index=False)
        cov[key] = hi.isoformat()
        _write_coverage(outdir, cov)                 # persist per month (crash-safe)
        written += 1
        sleep(throttle)
    _write_coverage(outdir, cov)                     # persist any legacy migrations
    return written, skipped


def load_cached(symbol: str, cache_dir: str = DEFAULT_CACHE) -> pd.DataFrame:
    """All cached months for ``symbol``, concatenated + cleaned — the 1-min
    frame the EquityBacktest (and SpotSeries) consumes."""
    paths = sorted(glob.glob(os.path.join(cache_dir, symbol, "*.parquet")))
    if not paths:
        raise FileNotFoundError(f"no cached months for {symbol!r} under {cache_dir!r}")
    df, _ = clean(pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True))
    return df


def make_kite():
    """Real Kite client from the tradealgo .env (network path — not unit-tested)."""
    from dotenv import load_dotenv          # lazy: only the live fetch needs these
    from kiteconnect import KiteConnect
    load_dotenv(TRADEALGO_ENV)
    kite = KiteConnect(api_key=os.environ["ZERODHA_API_KEY"])
    kite.set_access_token(os.environ["ZERODHA_ACCESS_TOKEN"])
    return kite
