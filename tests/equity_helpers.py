"""Synthetic 1-min equity frames for the equity tests.

``synth_day`` builds a full flat NSE session (09:15..15:29 = 375 minutes) at a
base price with per-minute OHLC overrides — small, deterministic, and enough
to exercise resampling, intrabar exits, and day-gate rules.
"""
from datetime import datetime, timedelta

import pandas as pd


def bar(dt, o, h=None, l=None, c=None):
    return {"DateTime": pd.Timestamp(dt), "Open": float(o),
            "High": float(h if h is not None else o),
            "Low": float(l if l is not None else o),
            "Close": float(c if c is not None else o)}


def synth_day(day, base, overrides=None, start="09:15", minutes=375):
    """A full session at ``base``; ``overrides`` = {"HH:MM": (o, h, l, c)}."""
    h0, m0 = (int(x) for x in start.split(":"))
    t0 = datetime(day.year, day.month, day.day, h0, m0)
    ov = overrides or {}
    rows = []
    for i in range(minutes):
        dt = t0 + timedelta(minutes=i)
        key = dt.strftime("%H:%M")
        if key in ov:
            o, h, l, c = ov[key]
            rows.append(bar(dt, o, h, l, c))
        else:
            rows.append(bar(dt, base))
    return rows


def frame(rows):
    return pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"])
