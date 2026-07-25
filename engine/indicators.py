"""Reusable technical-indicator primitives (oscillators + EMA).

Pure functions over pandas Series -> Series, look-ahead-safe by construction:
every value at bar ``i`` uses only bars ``<= i`` (rolling / ewm are backward
windows), so a strategy reading indicator[i] cannot see the future. The engine
had EMA usage scattered in strategies but no shared oscillator layer; the
Reversal-Blast study (RSI + Stochastic + Williams %R alignment) is the first
caller, and these accrue for the next question.

Conventions match the common charting defaults (TradingView / Zerodha Kite):
- RSI uses Wilder smoothing (ewm alpha=1/period), the standard RSI(14).
- Stochastic returns SLOW %K (raw %K smoothed by ``smooth_k``); %D is a further
  SMA of that. "Stochastic < 20" in the strategy means slow %K < 20.
- Williams %R is in [-100, 0]; -100 = at the period low, 0 = at the period high.
"""
from __future__ import annotations

import pandas as pd


def ema(values: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (span form, no warmup bias correction)."""
    return values.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI in [0, 100]. Neutral 50 where average loss is zero."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    # all-gain window (avg_loss == 0) -> RS = inf -> RSI 100; seed NaN stays NaN
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    return out


def stoch_k(high: pd.Series, low: pd.Series, close: pd.Series,
            period: int = 14, smooth_k: int = 3) -> pd.Series:
    """Slow %K in [0, 100]: raw %K = 100*(close-LL)/(HH-LL), smoothed by
    ``smooth_k``. Flat range (HH==LL) -> neutral 50."""
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    rng = hh - ll
    raw_k = 100.0 * (close - ll) / rng
    raw_k = raw_k.where(rng != 0.0, 50.0)
    return raw_k.rolling(smooth_k).mean()


def williams_r(high: pd.Series, low: pd.Series, close: pd.Series,
               period: int = 14) -> pd.Series:
    """Williams %R in [-100, 0]. Flat range (HH==LL) -> neutral -50."""
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    rng = hh - ll
    out = -100.0 * (hh - close) / rng
    return out.where(rng != 0.0, -50.0)
