"""NIFTY regime finder — Step 1 of the regime project.

Ports the rule-based labeler from the external Regime_based_project notebook
(cell 19) with two fixes the review found there:
  1. warm-up is an EXPLICIT truncation (the notebook's dropna() was a no-op
     after .astype(int), leaving ~110 mislabeled early rows), and
  2. zero rolling-std yields "no signal" instead of inf z-scores.

Label pipeline (per day, all trailing — no look-ahead):

    closes ->  20d returns-vol ----\
           ->  20d trend (pct_chg) --> z-score vs rolling 90d baseline
                                        |z| > 0.5 -> bull/bear, high/low vol
                                        -> 4-of-5-day persistence -> stable_*

Spec: docs/superpowers/specs/2026-07-18-regime-finder-design.md
Input: analysis/nifty_daily.csv (^NSEI spot; NOT daily_underlying.csv — see spec).
Run:  PYTHONPATH=. python3 analysis/regime_finder.py | tee analysis/regime_finder_out.txt
Exits 1 on sanity-check failure.
"""
import sys

import numpy as np
import pandas as pd

CSV = "analysis/nifty_daily.csv"

TREND_COLS = ["bull", "bear"]
VOL_COLS = ["high_vol", "low_vol"]


def persistence(raw, need=4, win=5):
    """True where raw is True today AND >=need of the last win days (incl. today)."""
    raw = raw.astype(bool)
    return raw & (raw.rolling(win).sum() >= need)


def label_regimes(closes, lookback=20, z_win=90, z_thresh=0.5,
                  persist_need=4, persist_win=5):
    """Daily regime labels from a close series. Returns bool DataFrame
    (bull, bear, high_vol, low_vol, stable_bull, stable_bear, stable_low_vol)
    truncated to rows where both z-scores have full windows."""
    returns = closes.pct_change()
    rolling_vol = returns.rolling(lookback).std()
    trend = closes.pct_change(lookback)

    vol_sd = rolling_vol.rolling(z_win).std()
    trend_sd = trend.rolling(z_win).std()
    # warm-up = first row where both baselines have a full window
    # (computed BEFORE the zero-std masking below, so a mid-series flat
    # patch can't shift the truncation point)
    warm_ok = vol_sd.notna() & trend_sd.notna()
    if not warm_ok.any():
        raise ValueError(f"series too short: {len(closes)} rows, "
                         f"need > {lookback + z_win}")

    # zero-std guard: flat window -> no signal, never inf
    vol_z = (rolling_vol - rolling_vol.rolling(z_win).mean()) / vol_sd.replace(0.0, np.nan)
    trend_z = (trend - trend.rolling(z_win).mean()) / trend_sd.replace(0.0, np.nan)

    r = pd.DataFrame(index=closes.index)
    r["bull"] = trend_z > z_thresh
    r["bear"] = trend_z < -z_thresh
    r["high_vol"] = vol_z > z_thresh
    r["low_vol"] = vol_z < -z_thresh
    for name, col in [("stable_bull", "bull"), ("stable_bear", "bear"),
                      ("stable_low_vol", "low_vol")]:
        r[name] = persistence(r[col], persist_need, persist_win)
    return r.loc[warm_ok[warm_ok].index[0]:].astype(bool)


def episodes(mask):
    """Contiguous True runs of a bool Series -> [(start, end, days), ...]."""
    mask = mask.astype(bool)
    out = []
    run_id = (mask != mask.shift()).cumsum()
    for _, seg in mask.groupby(run_id):
        if seg.iloc[0]:
            out.append((seg.index[0], seg.index[-1], len(seg)))
    return out


def combined_state(labels):
    """One exclusive state per day; high-vol beats low-vol (notebook priority)."""
    s = pd.Series("Neutral", index=labels.index)
    s[labels["high_vol"] & labels["stable_bear"]] = "HighVol_Bear"
    s[labels["high_vol"] & labels["stable_bull"]] = "HighVol_Bull"
    free = s == "Neutral"
    s[free & labels["stable_low_vol"] & labels["stable_bear"]] = "LowVol_Bear"
    s[free & labels["stable_low_vol"] & labels["stable_bull"]] = "LowVol_Bull"
    return s


def lagged(labels):
    """Executable view: what a trader knows at today's open = yesterday's labels."""
    result = labels.shift(1)
    return result.fillna(False).astype(bool)
