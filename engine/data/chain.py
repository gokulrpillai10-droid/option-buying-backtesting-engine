"""Point-in-time option chain.

A ``Chain`` wraps a per-minute long-format DataFrame (one row per
(DateTime, StrikePrice), with Call* and Put* columns) and only ever exposes
data *as of* a requested minute. This is the foundation of structural
look-ahead prevention: callers cannot reach into future bars through it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pandas as pd


@dataclass(frozen=True)
class OptionQuote:
    """One side (call or put) of one strike at one minute.

    IV/Gamma/Vega are chain-level; Theta/Rho per-leg. All Greeks are optional
    pass-through (None when the source frame lacks the column; no recompute).
    """
    strike: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: int
    delta: float
    is_traded: bool
    iv: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    theta: Optional[float] = None
    rho: Optional[float] = None


class OptionChainSnapshot:
    """The full chain at a single minute."""

    def __init__(self, dt: datetime, frame: pd.DataFrame):
        self._dt = dt
        self._frame = frame.set_index("StrikePrice")

    @property
    def as_of(self) -> datetime:
        """The timestamp this snapshot was taken at (its explicit as-of stamp)."""
        return self._dt

    def strikes(self) -> List[int]:
        return sorted(int(s) for s in self._frame.index)

    @staticmethod
    def _opt(row, col):
        """Optional float pass-through: None if column absent or NaN."""
        if col not in row.index:
            return None
        v = row[col]
        return None if pd.isna(v) else float(v)

    def call(self, strike: int) -> OptionQuote:
        row = self._frame.loc[strike]
        return OptionQuote(
            strike=int(strike),
            open=float(row["CallOpen"]),
            high=float(row["CallHigh"]),
            low=float(row["CallLow"]),
            close=float(row["CallClose"]),
            volume=int(row["CallVolume"]),
            oi=int(row["CallOI"]),
            delta=float(row["CallDelta"]),
            is_traded=bool(row["CallIsTradedStrike"]),
            iv=self._opt(row, "IV"), gamma=self._opt(row, "Gamma"),
            vega=self._opt(row, "Vega"), theta=self._opt(row, "CallTheta"),
            rho=self._opt(row, "CallRho"),
        )

    def put(self, strike: int) -> OptionQuote:
        row = self._frame.loc[strike]
        return OptionQuote(
            strike=int(strike),
            open=float(row["PutOpen"]),
            high=float(row["PutHigh"]),
            low=float(row["PutLow"]),
            close=float(row["PutClose"]),
            volume=int(row["PutVolume"]),
            oi=int(row["PutOI"]),
            delta=float(row["PutDelta"]),
            is_traded=bool(row["PutIsTradedStrike"]),
            iv=self._opt(row, "IV"), gamma=self._opt(row, "Gamma"),
            vega=self._opt(row, "Vega"), theta=self._opt(row, "PutTheta"),
            rho=self._opt(row, "PutRho"),
        )


class Chain:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def snapshot_at(self, dt: datetime) -> OptionChainSnapshot:
        frame = self._df[self._df["DateTime"] == pd.Timestamp(dt)]
        return OptionChainSnapshot(dt, frame)

    def next_open(self, dt: datetime, strike: int, side: str) -> Optional[float]:
        """Open price of ``strike``/``side`` at the first minute strictly after
        ``dt``. Returns ``None`` if there is no following bar. This is the only
        sanctioned fill price source — fills never occur at ``dt`` itself.
        """
        col = "CallOpen" if side.upper() == "CALL" else "PutOpen"
        future = self._df[
            (self._df["DateTime"] > pd.Timestamp(dt))
            & (self._df["StrikePrice"] == strike)
        ]
        if future.empty:
            return None
        next_dt = future["DateTime"].min()
        row = future[future["DateTime"] == next_dt].iloc[0]
        return float(row[col])
