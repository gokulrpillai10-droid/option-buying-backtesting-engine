"""MarketData — the unified, accurate-data facade.

Knows all the data: the resident spot (signals + the trading-day calendar) and the
option Catalog (fills). Selects the right contract per day from the REAL expiry
partitions (never a weekday rule — the expiry weekday changed Thu->Tue ~Sep 2025).

Streaming (``stream``) loads each expiry parquet exactly once — added in the next
increment alongside the Backtest driver.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Callable, Iterable, Iterator, List, Optional, Tuple

import pandas as pd

from engine.data.chain import Chain
from engine.data.spot import SpotSeries
from engine.instruments import lot_size_for


class DataFreshnessError(ValueError):
    pass


def load_quarantine(path: str) -> set:
    """The quarantined-day set written by analysis/data_integrity.py."""
    with open(path) as f:
        payload = json.load(f)
    return {date.fromisoformat(d) for d in payload["quarantined"]}


class MarketData:
    def __init__(self, catalog, spot: SpotSeries, min_dte: int = 3,
                 reader: Callable[[str], pd.DataFrame] = pd.read_parquet,
                 quarantine: Optional[Iterable[date]] = None):
        self._cat = catalog
        self._spot = spot
        self._min_dte = min_dte
        self._read = reader
        self._quarantine = set(quarantine) if quarantine else set()
        self._expiries = sorted(pd.Timestamp(e).date() for e in catalog.expiries())
        self._days = sorted({ts.to_pydatetime().date() for ts in spot.timestamps()})

    def trading_days(self, start: date, end: date) -> List[date]:
        """Eligible days — quarantined ones (data_integrity failures) are
        excluded here so a bad-data day can never become a trade; count them
        via quarantined_between() and REPORT the number with any results."""
        return [d for d in self._days
                if start <= d <= end and d not in self._quarantine]

    def quarantined_between(self, start: date, end: date) -> int:
        return sum(1 for d in self._days
                   if start <= d <= end and d in self._quarantine)

    def expiry_for(self, day: date) -> Optional[date]:
        """Nearest expiry on/after ``day`` with at least ``min_dte`` days to expiry."""
        for e in self._expiries:
            if (e - day).days >= self._min_dte:
                return e
        return None

    def lot_size(self, expiry: date) -> int:
        """Authoritative lot size for the contract expiring on ``expiry`` (per-expiry
        EXPIRIES data). Falls back to the day-keyed front-weekly schedule if the
        catalog carries no lot table or the expiry precedes it."""
        lt = self._cat.lot_table() if hasattr(self._cat, "lot_table") else None
        if lt is not None:
            v = lt.for_expiry(expiry)
            if v is not None:
                return v
        return lot_size_for(expiry)

    def spot(self) -> SpotSeries:
        return self._spot

    def last_expiry_date(self) -> Optional[date]:
        return self._expiries[-1] if self._expiries else None

    def stream(self, start: date, end: date) -> Iterator[Tuple[date, dict]]:
        """Yield (expiry, {day: Chain}) batches over [start, end], loading each
        expiry parquet EXACTLY ONCE (ARCH-4). Days are grouped by their chosen
        expiry; an expiry with no path on disk is skipped."""
        last = self.last_expiry_date()
        if last is not None and end > last:
            raise DataFreshnessError(
                f"backtest end {end} is past the last available expiry {last} "
                f"({(end - last).days} days of missing chain data). "
                f"Refresh the data or set end <= {last}."
            )
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
