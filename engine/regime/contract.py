"""Detector entry point: `classify(as_of)` -> `RegimeBias`.

Look-ahead safety is enforced here in one place: every input series is sliced to
rows **strictly before** ``as_of`` (prior-close in → today's bias out), so no
module can read a value stamped at or after today's open. The leak test poisons
the as_of row and asserts the bias is unchanged.

Breadth and skew are optional — a missing module is passed to the classifier as
None and its weight is renormalized away (see `classifier.composite`).
"""
from dataclasses import dataclass

import pandas as pd

from analysis.regime_finder import label_regimes
from analysis.regime_data import load_closes_vix
from engine.regime.features import (
    vol_state, iv_rv_ratio, vix_percentile, vol_dynamics, skew,
)
from engine.regime.breadth import breadth_score
from engine.regime.classifier import composite, BASE_WEIGHTS


@dataclass(frozen=True)
class RegimeBias:
    as_of: pd.Timestamp
    label: str               # "BUY" | "SELL" | "NEUTRAL"
    score: float
    confidence: float
    modules: dict            # module name -> score (or None if absent)


def _last(series):
    """Value on the most recent (prior-close) day, or None if empty/NaN."""
    if series is None or len(series) == 0:
        return None
    v = series.iloc[-1]
    return None if pd.isna(v) else float(v)


def classify(as_of, closes=None, vix=None, snapshot=None, ref_price=None,
             weights=BASE_WEIGHTS, thr=0.15, min_agreement=0.6,
             label_kwargs=None) -> RegimeBias:
    """Daily BUY/SELL/NEUTRAL bias for ``as_of``, computed from prior-close data.

    `closes`/`vix` default to the on-disk daily series; pass them explicitly (e.g.
    in tests, or a backtest walk) to inject data. `snapshot`/`ref_price` enable the
    skew module from a chain snapshot at the prior close; omit them to skip skew.
    """
    as_of = pd.Timestamp(as_of)
    if closes is None or vix is None:
        d_closes, d_vix = load_closes_vix()
        closes = d_closes if closes is None else closes
        vix = d_vix if vix is None else vix

    # THE look-ahead cut: strictly-before as_of. Nothing downstream sees >= as_of.
    closes = closes[closes.index < as_of]
    vix = vix[vix.index < as_of]

    labels = label_regimes(closes, **(label_kwargs or {}))
    modules = {
        "vol_state": _last(vol_state(labels)),
        "vol_dynamics": _last(vol_dynamics(vix)),
        "iv_rv": _last(iv_rv_ratio(vix, closes)),
        "vix_level": _last(vix_percentile(vix)),
        "skew": skew(snapshot, ref_price) if (snapshot is not None and ref_price is not None) else None,
        "breadth": breadth_score(),
    }
    comp = composite(modules, weights=weights, thr=thr, min_agreement=min_agreement)
    return RegimeBias(as_of, comp.label, comp.score, comp.confidence, modules)
