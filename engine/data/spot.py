"""Look-ahead-safe spot OHLC series for SIGNALS.

Strategies must trigger on the real index (spot), not the option-derived Forward
(whose intraday path-noise produces false/missed triggers). ``SpotSeries`` wraps a
spot bar frame and only ever returns data *as of* a requested minute, so signal
logic physically cannot see future bars. Option *fills* still come from the
Data-v3 ``Chain`` -- spot decides when, the chain decides at what price.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pandas as pd


def load_spot_csv(path: str) -> "SpotSeries":
    """Load a Kite-format spot CSV (date,open,high,low,close[,volume]) into a
    SpotSeries. Handles tz-aware timestamps (stripped to naive IST) and
    lower/upper-case column names."""
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    dt = pd.to_datetime(df[cols.get("date", cols.get("datetime"))])
    if getattr(dt.dt, "tz", None) is not None:
        dt = dt.dt.tz_localize(None)
    out = pd.DataFrame({
        "DateTime": dt,
        "Open": df[cols["open"]].astype(float),
        "High": df[cols["high"]].astype(float),
        "Low": df[cols["low"]].astype(float),
        "Close": df[cols["close"]].astype(float),
    })
    return SpotSeries(out)


@dataclass(frozen=True)
class SpotBar:
    dt: datetime
    open: float
    high: float
    low: float
    close: float


def _resample_ohlc(one_min: pd.DataFrame, timeframe: str, label: str) -> pd.DataFrame:
    """Resample 1-min OHLC to ``timeframe``. ``label='left'`` => START-labeled bars."""
    closed = label
    g = one_min.set_index("DateTime").resample(timeframe, label=label, closed=closed)
    bars = pd.DataFrame({
        "Open": g["Open"].first(), "High": g["High"].max(),
        "Low": g["Low"].min(), "Close": g["Close"].last(),
    }).dropna()
    return bars.reset_index()


def _normalize_naive_ist(dt: pd.Series) -> pd.Series:
    """All timestamps to naive IST wall-clock (mixed tz-aware/naive silently misalign)."""
    dt = pd.to_datetime(dt)
    if getattr(dt.dt, "tz", None) is not None:
        dt = dt.dt.tz_localize(None)   # drop tz, keep IST wall-clock
    return dt


class SpotSeries:
    def __init__(self, df: pd.DataFrame):
        # one row per minute; columns DateTime, Open, High, Low, Close
        self._df = df.sort_values("DateTime").reset_index(drop=True)
        # O(1) bar lookup (the per-bar hot path) instead of scanning the frame
        self._pos = {ts: i for i, ts in enumerate(self._df["DateTime"])}

    @classmethod
    def from_index(cls, root: str, timeframe: str = "5min", label: str = "left") -> "SpotSeries":
        """Load the Data-v3 NIFTY50 INDEX spot across the scattered Data folders:
        discover -> tz-normalize -> dedupe by DateTime -> resample START-labeled.

        ``label='left'`` (START): a bar labelled B covers [B, B+timeframe) and is the
        engine's canonical convention (matches the +N-min fill contract, ARCH-1)."""
        pattern = os.path.join(root, "*", "Data-v3", "instrument=NIFTY50", "INDEX",
                               "index_data.parquet")
        paths = sorted(glob.glob(pattern))
        if not paths:
            raise FileNotFoundError(f"no INDEX parquets under {root!r}")
        frames = []
        for p in paths:
            df = pd.read_parquet(p, columns=["DateTime", "Open", "High", "Low", "Close"])
            df["DateTime"] = _normalize_naive_ist(df["DateTime"])
            frames.append(df)
        one_min = (pd.concat(frames, ignore_index=True)
                   .drop_duplicates("DateTime").sort_values("DateTime"))
        return cls(_resample_ohlc(one_min, timeframe, label))

    def _row_to_bar(self, row) -> SpotBar:
        return SpotBar(dt=row["DateTime"].to_pydatetime(),
                       open=float(row["Open"]), high=float(row["High"]),
                       low=float(row["Low"]), close=float(row["Close"]))

    def timestamps(self) -> List[pd.Timestamp]:
        """All bar timestamps in order (the runner's minute axis)."""
        return list(self._df["DateTime"])

    def bar_at(self, dt: datetime) -> Optional[SpotBar]:
        i = self._pos.get(pd.Timestamp(dt))
        return self._row_to_bar(self._df.iloc[i]) if i is not None else None

    def window(self, dt: datetime, n: int) -> List[SpotBar]:
        """The up-to-``n`` bars ENDING at ``dt`` (inclusive), newest last. O(n) via the
        position index -- for rolling indicators (ATR/ROC) without scanning all history."""
        i = self._pos.get(pd.Timestamp(dt))
        if i is None:
            return []
        lo = max(0, i - n + 1)
        return [self._row_to_bar(self._df.iloc[j]) for j in range(lo, i + 1)]

    def close_before(self, dt: datetime) -> Optional[float]:
        """Close of the last bar strictly before the START of dt's day (prev session
        close) -- for gap gates. O(log n)."""
        day_start = pd.Timestamp(dt).normalize()
        i = int(self._df["DateTime"].searchsorted(day_start))   # first bar >= midnight today
        return float(self._df.iloc[i - 1]["Close"]) if i > 0 else None

    def prev_day_hlc(self, dt: datetime):
        """(high, low, close) of the prior trading session -- the CPR inputs.
        High=max, Low=min, Close=last bar of that day. None if no prior session."""
        day_start = pd.Timestamp(dt).normalize()
        prior = self._df[self._df["DateTime"] < day_start]
        if prior.empty:
            return None
        prev_day = prior["DateTime"].max().normalize()
        rows = prior[prior["DateTime"] >= prev_day]
        return (float(rows["High"].max()), float(rows["Low"].min()),
                float(rows.iloc[-1]["Close"]))

    def price_at(self, dt: datetime) -> Optional[float]:
        """Close of the bar at-or-before ``dt`` (None before the session)."""
        prior = self._df[self._df["DateTime"] <= pd.Timestamp(dt)]
        return float(prior.iloc[-1]["Close"]) if not prior.empty else None

    def history(self, dt: datetime) -> List[SpotBar]:
        """All bars with timestamp <= ``dt`` (no future bars)."""
        prior = self._df[self._df["DateTime"] <= pd.Timestamp(dt)]
        return [self._row_to_bar(r) for _, r in prior.iterrows()]
