"""Shared loaders for the daily NIFTY-close + India-VIX series.

Every regime / vol-surface study reads the same two CSVs the same way (a
date-indexed `close` column). This is the one place that knows their paths and
schema, so a data-file move or schema tweak is a single edit instead of a
scatter across ~9 scripts.
"""
import pandas as pd

NIFTY_CSV = "analysis/nifty_daily.csv"
VIX_CSV = "analysis/india_vix.csv"


def _load_close(path):
    return pd.read_csv(path, parse_dates=["date"], index_col="date")["close"]


def load_closes(path=NIFTY_CSV):
    """Daily NIFTY close series, date-indexed."""
    return _load_close(path)


def load_vix(path=VIX_CSV):
    """Daily India-VIX close series, date-indexed (raw points, e.g. 13.5)."""
    return _load_close(path)


def load_closes_vix(nifty=NIFTY_CSV, vix=VIX_CSV):
    """Convenience for the many studies that need both: `(closes, vix)`."""
    return load_closes(nifty), load_vix(vix)
