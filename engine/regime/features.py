"""Per-day regime feature scores, each in ``[-1, +1]``.

Sign convention (single, everywhere): **+1 favors option BUYING (gamma / storm),
-1 favors option SELLING (theta / calm).** Every helper is trailing / expanding
only — it never reads a value stamped at or after the day it scores, so the
composite is look-ahead safe (asserted in the leak test).

Modules (spec §67-74):
  vol_state      PRIMARY  reuse regime_finder high_vol / stable_low_vol labels
  iv_rv_ratio    VRP      India VIX vs 21d realized vol (tenor-matched)
  vix_percentile IV level expanding min-1y VIX percentile
  vol_dynamics   dynamics VIX expanding vs contracting
  skew           context  ATM put-vs-call IV skew from a chain snapshot
"""
import numpy as np
import pandas as pd

TRADING_DAYS = 252


def vol_state(labels: pd.DataFrame) -> pd.Series:
    """PRIMARY axis. Map `regime_finder.label_regimes` output to a score:
    ``high_vol`` (storm) → +1 BUY/gamma, ``stable_low_vol`` (calm) → -1 SELL/theta,
    otherwise 0. The two label columns are mutually exclusive on real data."""
    score = pd.Series(0.0, index=labels.index)
    score[labels["stable_low_vol"].astype(bool)] = -1.0
    score[labels["high_vol"].astype(bool)] = 1.0
    return score


def iv_rv_ratio(vix: pd.Series, closes: pd.Series, window: int = 21) -> pd.Series:
    """VRP refinement. ``ratio = VIX / annualized(window)-day realized vol``.
    Rich premium (ratio > 1) → SELL (negative); cheap (ratio < 1) → BUY (positive):
    ``score = clip(1 - ratio, -1, 1)``. Zero/flat realized vol → 0.0 (no signal, no
    division by zero); warm-up (insufficient window) stays NaN."""
    vix, closes = vix.align(closes, join="inner")
    rv_pct = closes.pct_change().rolling(window).std() * np.sqrt(TRADING_DAYS) * 100.0
    ratio = vix / rv_pct.where(rv_pct > 0)          # rv==0 -> NaN -> guarded below
    score = (1.0 - ratio).clip(-1.0, 1.0)
    score[(rv_pct == 0.0)] = 0.0                    # flat window: explicit no-signal
    return score


def vix_percentile(vix: pd.Series, min_periods: int = TRADING_DAYS) -> pd.Series:
    """IV-level context from the expanding VIX percentile (look-ahead safe; NaN
    until ``min_periods`` observations). Low percentile = cheap premium → BUY (+1);
    high = rich → SELL (-1): ``score = 1 - 2*pct``."""
    pct = vix.expanding(min_periods=min_periods).rank(pct=True, method="max")
    return 1.0 - 2.0 * pct


def vol_dynamics(vix: pd.Series, lookback: int = 10, scale: float = 0.2) -> pd.Series:
    """Vol expanding vs contracting over `lookback` days. Expanding (VIX rising) →
    BUY gamma (+); contracting → SELL theta (-). ``score = clip(rel/scale, -1, 1)``
    where ``rel`` is the fractional change; ``scale`` (0.2 = 20%) sets saturation."""
    rel = (vix - vix.shift(lookback)) / vix.shift(lookback)
    return (rel / scale).clip(-1.0, 1.0)


def skew(snapshot, ref_price: float, scale: float = 0.1):
    """ATM put-vs-call IV skew from a chain snapshot. Fear skew (puts richer than
    calls) leans BUY/gamma (+); returns a score in ``[-1, +1]`` or ``None`` when a
    greek is missing (passed through so the composite renormalizes without it)."""
    strike = int(round(ref_price / 50.0) * 50)
    try:
        call_iv = snapshot.call(strike).iv
        put_iv = snapshot.put(strike).iv
    except Exception:
        return None
    if call_iv is None or put_iv is None or call_iv <= 0:
        return None
    rel = (put_iv - call_iv) / call_iv
    return float(np.clip(rel / scale, -1.0, 1.0))
