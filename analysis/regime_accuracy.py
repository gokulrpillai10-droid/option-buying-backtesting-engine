"""Accuracy test for the NIFTY regime detector.

A regime label has no ground-truth answer sheet, so "accuracy" = does the label,
known YESTERDAY (lagged, no look-ahead), predict what happens NEXT? Three tests,
all on the daily price + VIX series (no option-chain walk):

  1. VOL-STATE FORECAST — does Calm/Stormy predict the next window-day realized
     vol? (mean fwd-vol Calm vs Stormy + Welch t + a Calm->low / Stormy->high
     confusion matrix). The core test: a vol detector must FORECAST vol, not mirror it.
  2. DIRECTION FORECAST — does Bull/Bear predict forward returns? (mean + sign hit).
  3. PERSISTENCE — does the label stick (P(same tomorrow), avg episode length)? A
     regime you can act on must persist; one that flips daily is noise.

Honesty note: an accurate label is NOT the same as a profitable trade -- the regime
TIMING is already a documented NO-GO vs always-short. This measures "is the label
real?", not "does trading it pay?".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NIFTY_CSV = "analysis/nifty_daily.csv"
VIX_CSV = "analysis/india_vix.csv"
WINDOWS = (5, 10)


# ------------------------------------------------------------------ #
# pure helpers (tested)
# ------------------------------------------------------------------ #
def forward_return(closes: pd.Series, window: int) -> pd.Series:
    """Return over the NEXT ``window`` days at each day (NaN past the end)."""
    return closes.shift(-window) / closes - 1.0


def forward_realized_vol(closes: pd.Series, window: int) -> pd.Series:
    """Std of daily returns over the NEXT ``window`` days at each day."""
    rets = closes.pct_change()
    return rets.rolling(window).std().shift(-window)


def forward_drawdown(closes: pd.Series, window: int) -> pd.Series:
    """Worst (most negative) return over the NEXT ``window`` days -- the biggest
    dip from today's close within the window. NaN past the end."""
    mins = pd.concat([closes.shift(-k) for k in range(1, window + 1)], axis=1).min(axis=1)
    return mins / closes - 1.0


def confusion(pred: pd.Series, actual: pd.Series) -> dict:
    """accuracy / precision / recall for aligned boolean predict vs actual."""
    d = pd.DataFrame({"p": pred, "a": actual}).dropna()
    p, a = d["p"].astype(bool), d["a"].astype(bool)
    tp = int((p & a).sum()); fp = int((p & ~a).sum())
    tn = int((~p & ~a).sum()); fn = int((~p & a).sum())
    n = len(d)
    return {"n": n,
            "accuracy": (tp + tn) / n if n else np.nan,
            "precision": tp / (tp + fp) if (tp + fp) else np.nan,
            "recall": tp / (tp + fn) if (tp + fn) else np.nan,
            "tp": tp, "fp": fp, "tn": tn, "fn": fn}


def welch_t(a, b) -> float:
    a = np.asarray(a, float); a = a[~np.isnan(a)]
    b = np.asarray(b, float); b = b[~np.isnan(b)]
    if len(a) < 2 or len(b) < 2:
        return np.nan
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return float((a.mean() - b.mean()) / se) if se > 0 else np.nan


def horizon_persistence(mask: pd.Series, horizon: int) -> float:
    """P(the condition still holds ``horizon`` days ahead | it holds today) --
    how far out the CURRENT label keeps describing the market."""
    m = mask.astype(bool)
    ahead = m.shift(-horizon)[m].dropna()
    return float(ahead.mean()) if len(ahead) else np.nan


# ------------------------------------------------------------------ #
# driver
# ------------------------------------------------------------------ #
def main():
    from analysis.regime_finder import lagged, episodes
    from analysis.regime_vol import sharpen_labels, sharp_state

    closes = pd.read_csv(NIFTY_CSV, parse_dates=["date"], index_col="date")["close"]
    vix = pd.read_csv(VIX_CSV, parse_dates=["date"], index_col="date")["close"]
    lab = sharpen_labels(closes, vix)                 # the ACTUAL detector output
    lagl = lagged(lab)                                # act on yesterday's label

    print("REGIME DETECTOR — ACCURACY TEST (labels lagged +1, no look-ahead)")
    print(f"window {lab.index[0].date()}..{lab.index[-1].date()}  n={len(lab)} days")
    print("(accurate label != profitable trade — regime TIMING is already NO-GO "
          "vs always-short)\n")

    for w in WINDOWS:
        frv = forward_realized_vol(closes, w).reindex(lab.index)
        fret = forward_return(closes, w).reindex(lab.index)
        storm, calm = lagl["stormy"], lagl["calm"]
        bull, bear = lagl["bull"], lagl["bear"]

        print(f"===== forward horizon = {w} trading days =====")

        # --- 1. VOL-STATE FORECAST ---
        v_storm = frv[storm].dropna(); v_calm = frv[calm].dropna()
        t = welch_t(v_storm, v_calm)
        med = frv.median()
        # Stormy predicts HIGH forward vol; Calm predicts LOW.
        pred_high = storm.reindex(frv.index, fill_value=False)
        actual_high = frv > med
        cm = confusion(pred_high[frv.notna() & (storm | calm)],
                       actual_high[frv.notna() & (storm | calm)])
        print(f"  VOL-STATE: fwd-vol Stormy={v_storm.mean()*100:.2f}%/d vs "
              f"Calm={v_calm.mean()*100:.2f}%/d  (Stormy-minus-Calm t={t:+.1f}"
              f"{' **' if abs(t) >= 3 else ''})")
        print(f"    Stormy->high-vol / Calm->low-vol: acc={cm['accuracy']:.0%} "
              f"precision={cm['precision']:.0%} recall={cm['recall']:.0%}  (n={cm['n']})")

        # --- 2. DIRECTION FORECAST ---
        r_bull = fret[bull].dropna(); r_bear = fret[bear].dropna()
        td = welch_t(r_bull, r_bear)
        bull_hit = (r_bull > 0).mean() if len(r_bull) else np.nan
        bear_hit = (r_bear < 0).mean() if len(r_bear) else np.nan
        print(f"  DIRECTION: fwd-ret Bull={r_bull.mean()*100:+.2f}% vs "
              f"Bear={r_bear.mean()*100:+.2f}%  (Bull-minus-Bear t={td:+.1f}"
              f"{' **' if abs(td) >= 3 else ''})")
        print(f"    Bull->up hit={bull_hit:.0%} (n={len(r_bull)})   "
              f"Bear->down hit={bear_hit:.0%} (n={len(r_bear)})\n")

    # --- 3. PERSISTENCE / STABILITY ---
    state = lab.apply(sharp_state, axis=1)
    same = (state == state.shift(-1)).iloc[:-1].mean()
    print("===== persistence / stability =====")
    print(f"  same combined state tomorrow: {same:.0%}")
    axes = [("calm", lab["calm"]), ("stormy", lab["stormy"]),
            ("bull", lab["bull"]), ("bear", lab["bear"])]
    for name, mask in axes:
        stay = horizon_persistence(mask, 1)
        eps = episodes(mask)
        avg = np.mean([d for _s, _e, d in eps]) if eps else np.nan
        print(f"  {name:<7} P(still {name} tomorrow)={stay:.0%}   "
              f"episodes={len(eps)}  avg length={avg:.1f}d")

    # --- 4. LABEL HORIZON (does TODAY's label still hold N days out?) ---
    print("\n===== label horizon (today's label persisting N days out) =====")
    for w in WINDOWS:
        acc = (state == state.shift(-w)).iloc[:-w].mean()
        cells = "  ".join(f"{name}={horizon_persistence(mask, w):.0%}"
                          for name, mask in axes)
        print(f"  h={w:>2}d: same combined state={acc:.0%}   |  {cells}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
