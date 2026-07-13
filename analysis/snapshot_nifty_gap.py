"""Snapshot per-date NIFTY gap into data_index/NIFTY/gap.parquet.

Keeps the gap-router backtest offline/reproducible: it reads this in-repo
snapshot, never the sibling tradealgo CSV at backtest time. Re-run to pull
forward when new NIFTY data lands.
"""
from __future__ import annotations

import os

import pandas as pd

NIFTY_CSV = "/Users/gokul/Desktop/tradealgo/nifty_5min_5yr.csv"
STORE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data_index", "NIFTY", "gap.parquet")


def build_gap_table(nifty_df: pd.DataFrame) -> pd.DataFrame:
    """Per-session open/prev-close -> gap. First session dropped (no prior)."""
    df = nifty_df.copy()
    df["day"] = pd.to_datetime(df["date"]).dt.normalize()
    daily = df.groupby("day").agg(nopen=("open", "first"),
                                  nclose=("close", "last"))
    daily["pc"] = daily["nclose"].shift(1)
    daily = daily.dropna(subset=["pc"])
    out = pd.DataFrame({
        "date": daily.index.date,
        "ngap": (daily["nopen"] / daily["pc"] - 1.0).values,
    })
    return out.reset_index(drop=True)


def main() -> None:
    nifty = pd.read_csv(NIFTY_CSV, parse_dates=["date"])
    out = build_gap_table(nifty)
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    out.to_parquet(STORE, index=False)
    print(f"wrote {len(out)} sessions -> {STORE}  "
          f"({out['date'].min()} -> {out['date'].max()})")


if __name__ == "__main__":
    main()
