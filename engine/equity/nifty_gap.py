"""Per-date NIFTY gap from the in-repo snapshot (data_index/NIFTY/gap.parquet).

Isolates the gap-router's one external input behind a tiny lookup. Missing
dates return None -> the router treats that as 'NIFTY not aligned'.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Optional

import pandas as pd

DEFAULT_STORE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data_index", "NIFTY", "gap.parquet")


class NiftyGapProvider:
    def __init__(self, store_path: str = DEFAULT_STORE):
        df = pd.read_parquet(store_path)
        self._gap = {pd.Timestamp(d).date(): float(g)
                     for d, g in zip(df["date"], df["ngap"])}

    def gap_for(self, d: date) -> Optional[float]:
        return self._gap.get(d)
