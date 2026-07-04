"""MarketData — the unified, accurate-data facade.

Knows all the data: the resident spot (signals + the trading-day calendar) and the
option Catalog (fills). Selects the right contract per day from the REAL expiry
partitions (never a weekday rule — the expiry weekday changed Thu->Tue ~Sep 2025).

Streaming (``stream``) loads each expiry parquet exactly once — added in the next
increment alongside the Backtest driver.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Iterator, List, Optional, Tuple

import pandas as pd

from engine.data.chain import Chain
from engine.data.spot import SpotSeries


class MarketData:
    def __init__(self, catalog, spot: SpotSeries, min_dte: int = 3,
                 reader: Callable[[str], pd.DataFrame] = pd.read_parquet):
        self._cat = catalog
        self._spot = spot
        self._min_dte = min_dte
        self._read = reader
        self._expiries = sorted(pd.Timestamp(e).date() for e in catalog.expiries())
        self._days = sorted({ts.to_pydatetime().date() for ts in spot.timestamps()})

    def trading_days(self, start: date, end: date) -> List[date]:
        return [d for d in self._days if start <= d <= end]

    def expiry_for(self, day: date) -> Optional[date]:
        """Nearest expiry on/after ``day`` with at least ``min_dte`` days to expiry."""
        for e in self._expiries:
            if (e - day).days >= self._min_dte:
                return e
        return None

    def spot(self) -> SpotSeries:
        return self._spot

    def stream(self, start: date, end: date) -> Iterator[Tuple[date, dict]]:
        """Yield (expiry, {day: Chain}) batches over [start, end], loading each
        expiry parquet EXACTLY ONCE (ARCH-4). Days are grouped by their chosen
        expiry; an expiry with no path on disk is skipped."""
        by_exp: dict = {}
        for d in self.trading_days(start, end):
            e = self.expiry_for(d)
            if e is not None:
                by_exp.setdefault(e, []).append(d)

        for expiry in sorted(by_exp):
            path = self._cat.path_for(expiry.isoformat())
            if path is None:
                continue                                  # missing expiry -> skip
            df = self._read(path)                         # loaded once
            df = df.assign(_d=pd.to_datetime(df["DateTime"]).dt.date)
            chains = {}
            for d in by_exp[expiry]:
                day_df = df[df["_d"] == d].drop(columns="_d")
                if len(day_df):
                    chains[d] = Chain(day_df)
            yield expiry, chains
