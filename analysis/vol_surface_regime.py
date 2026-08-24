"""Composite vol-surface regime detector (the second, options-structure detector).

Independent of the price detector (regime_finder/regime_vol). Reads the cached
vol-surface factors, scores each into a 0/1/2 stress score by its own TRAILING
percentile (causal, look-ahead safe), sums the winning factors, and buckets the
total into Complacent / Normal / Stressed. VRP is dropped (forward-vol gate t=1.8,
noise); the composite uses skew + atm_iv + term + accel (all cleared |t|>=3).

Then it validates: does the label forecast next-10d realized vol, and does it add
signal BEYOND India VIX (the price detector already has VIX ~ atm_iv)? The
incremental test is the honest one -- a second detector must earn its keep.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

from analysis.regime_accuracy import welch_t, forward_realized_vol
from analysis.regime_data import load_closes, load_vix
from analysis.regime_vol import expanding_percentile

FACTORS_CSV = "analysis/vol_surface_daily.csv"
BREADTH_CSV = "analysis/nifty_breadth_daily.csv"
OUT_CSV = "analysis/vol_surface_regime.csv"
COMPOSITE = ["skew", "atm_iv", "term", "accel", "breadth"]   # vrp dropped (noise)
INVERT = {"breadth": True}       # LOW breadth (few stocks healthy) = stress
WARMUP = 252

COMPLACENT, NORMAL, STRESSED = "Complacent", "Normal", "Stressed"


# ------------------------------------------------------------------ #
# pure scoring (tested)
# ------------------------------------------------------------------ #
def _tercile(frac: float):
    """Map a fraction in [0, 1] to a 0/1/2 tercile score (bottom third -> 0,
    middle -> 1, top -> 2); NaN passes through. The single tercile cut this
    detector uses -- shared by the per-factor score and the composite bucket."""
    if pd.isna(frac):
        return np.nan
    if frac >= 2 / 3:
        return 2
    if frac <= 1 / 3:
        return 0
    return 1


def stress_score(series: pd.Series, min_periods: int = WARMUP,
                 invert: bool = False) -> pd.Series:
    """0/1/2 stress score by trailing percentile: top tercile -> 2 (stress),
    bottom -> 0, middle -> 1. ``invert=True`` flips it (LOW value = stress).
    Look-ahead safe (expanding percentile); NaN during warm-up."""
    pct = expanding_percentile(series.astype(float), min_periods=min_periods)
    if invert:
        pct = 1.0 - pct
    return pct.apply(_tercile)


def bucket(total: float, n_factors: int) -> str:
    """Summed 0..2*n stress score -> Complacent / Normal / Stressed (same tercile
    cut as the per-factor score, applied to the normalized total)."""
    return {2: STRESSED, 1: NORMAL, 0: COMPLACENT}[_tercile(total / (2 * n_factors))]


# ------------------------------------------------------------------ #
# driver
# ------------------------------------------------------------------ #
def label_series():
    df = pd.read_csv(FACTORS_CSV, parse_dates=["date"]).set_index("date")
    if os.path.exists(BREADTH_CSV):
        b = pd.read_csv(BREADTH_CSV, parse_dates=["date"]).set_index("date")["breadth"]
        df["breadth"] = b.reindex(df.index)
    scores = pd.DataFrame(
        {f: stress_score(df[f], invert=INVERT.get(f, False)) for f in COMPOSITE},
        index=df.index)
    valid = scores.dropna()
    total = valid.sum(axis=1)
    lab = total.apply(lambda t: bucket(t, len(COMPOSITE)))
    out = df.loc[valid.index].copy()
    for f in COMPOSITE:
        out[f"score_{f}"] = valid[f]
    out["stress_total"] = total
    out["regime"] = lab
    return out


def main():
    if not os.path.exists(FACTORS_CSV):
        print(f"missing {FACTORS_CSV} — run analysis/vol_surface_daily.py first")
        return 1
    out = label_series()
    out.to_csv(OUT_CSV)

    closes = load_closes()
    fvol = forward_realized_vol(closes, 10); fvol.index = fvol.index.date
    f = fvol.reindex(pd.to_datetime(out.index).date)
    f.index = out.index
    lab = out["regime"]

    print("VOL-SURFACE COMPOSITE REGIME — Complacent / Normal / Stressed")
    print(f"factors={'+'.join(COMPOSITE)} (vrp dropped as noise)  n={len(out)} "
          f"days {out.index.min().date()}..{out.index.max().date()}\n")

    print("forward 10d realized vol by regime (does the label separate future vol?):")
    means = {}
    for r in (COMPLACENT, NORMAL, STRESSED):
        v = f[lab == r].dropna()
        means[r] = v.mean()
        print(f"  {r:<11} n={len(v):>4}  fwd-vol={v.mean()*100:.2f}%/d")
    t = welch_t(f[lab == STRESSED].dropna(), f[lab == COMPLACENT].dropna())
    print(f"  Stressed-minus-Complacent t={t:+.1f}"
          f"{' ** real separation' if abs(t) >= 3 else ' (weak)'}")

    # --- incremental to VIX: does it separate WITHIN the middle-VIX days? ---
    vix = load_vix()
    vix.index = vix.index.date
    vpct = expanding_percentile(vix.reindex(pd.to_datetime(out.index).date).astype(float), WARMUP)
    vpct.index = out.index
    midvix = (vpct > 1 / 3) & (vpct < 2 / 3)
    fs = f[midvix & (lab == STRESSED)].dropna()
    fc = f[midvix & (lab == COMPLACENT)].dropna()
    ti = welch_t(fs, fc)
    print(f"\nINCREMENTAL vs VIX (within middle-VIX days only, VIX ambiguous):")
    print(f"  Stressed fwd-vol={fs.mean()*100:.2f}% (n={len(fs)}) vs "
          f"Complacent={fc.mean()*100:.2f}% (n={len(fc)})  t={ti:+.1f}"
          f"{' ** adds beyond VIX' if abs(ti) >= 3 else ' (no clear add over VIX)'}")

    # --- breadth incremental to VIX (the orthogonal factor's own test) ---
    if "breadth" in out:
        dm = pd.DataFrame({"b": out["breadth"], "f": f, "mid": midvix}).dropna()
        dm = dm[dm["mid"]]
        lo, hi = dm["b"].quantile(1 / 3), dm["b"].quantile(2 / 3)
        low_b = dm.loc[dm["b"] <= lo, "f"]; high_b = dm.loc[dm["b"] >= hi, "f"]
        tb = welch_t(low_b, high_b)          # low breadth should -> higher fwd vol
        print(f"\nBREADTH alone vs VIX (within middle-VIX days): low-breadth "
              f"fwd-vol={low_b.mean()*100:.2f}% (n={len(low_b)}) vs high-breadth="
              f"{high_b.mean()*100:.2f}% (n={len(high_b)})  t={tb:+.1f}"
              f"{' ** breadth adds beyond VIX' if abs(tb) >= 3 else ' (no add over VIX)'}")

    bval = f"{out['breadth'].iloc[-1]:.0%}" if "breadth" in out else "n/a"
    print(f"\nCURRENT ({out.index[-1].date()}): {lab.iloc[-1]}  "
          f"(stress {int(out['stress_total'].iloc[-1])}/{2*len(COMPOSITE)}; "
          f"skew {out['skew'].iloc[-1]:+.3f}, atm_iv {out['atm_iv'].iloc[-1]:.3f}, "
          f"term {out['term'].iloc[-1]:+.3f}, breadth {bval})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
