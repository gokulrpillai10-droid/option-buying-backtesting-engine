"""Load Data-v3 parquet partitions into point-in-time ``Chain`` objects.

The loader is the only component that touches parquet on disk. It reads an
expiry partition (optionally filtered to a single trading session) and hands
back a ``Chain`` — from there everything is point-in-time and look-ahead safe.
"""
from __future__ import annotations

from datetime import date
from typing import List

import pandas as pd

from engine.data.catalog import Catalog
from engine.data.chain import Chain


class DataLoader:
    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def _read(self, expiry: str) -> pd.DataFrame:
        path = self._catalog.path_for(expiry)
        if path is None:
            raise KeyError(f"no data partition for expiry {expiry}")
        return pd.read_parquet(path)

    def load_expiry(self, expiry: str) -> Chain:
        return Chain(self._read(expiry))

    def trading_dates(self, expiry: str) -> List[date]:
        df = self._read(expiry)
        return sorted({ts.date() for ts in df["DateTime"]})

    def load_day(self, expiry: str, session: date) -> Chain:
        df = self._read(expiry)
        df = df[df["DateTime"].dt.date == session]
        return Chain(df.reset_index(drop=True))
