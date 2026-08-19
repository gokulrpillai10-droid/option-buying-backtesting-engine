"""Live forward-validation journal for the regime detector.

Run it (daily, or whenever the data refreshes) and it does two things:
  1. LOG today's prediction — the current regime label + what it implies for the
     next 5/10 trading days (Stormy=>high vol, Calm=>low vol, Bull=>up, Bear=>down).
  2. GRADE every past prediction whose horizon has now MATURED, against what the
     market actually did, and print a rolling out-of-sample scorecard.

This is the honest "does it hold on upcoming days" test: you predict now and grade
later, so the numbers are true out-of-sample (no fitting, no look-ahead). It
complements analysis/regime_accuracy.py (the historical estimate). Idempotent:
re-running the same day updates today's row and re-grades matured ones.

State: analysis/regime_forward_journal.csv (one row per prediction date).
"""
from __future__ import annotations

import os
from typing import Optional

import numpy as np
import pandas as pd

NIFTY_CSV = "analysis/nifty_daily.csv"
VIX_CSV = "analysis/india_vix.csv"
JOURNAL = "analysis/regime_forward_journal.csv"
HORIZONS = (5, 10)


# ------------------------------------------------------------------ #
# pure grading (tested)
# ------------------------------------------------------------------ #
def grade_vol(stormy: bool, calm: bool, fwd_vol: float,
              thr: float) -> Optional[bool]:
    """Was the vol-state call right? Stormy -> future vol should be > threshold,
    Calm -> below. Mixed days make no call (None)."""
    if stormy:
        return bool(fwd_vol > thr)
    if calm:
        return bool(fwd_vol < thr)
    return None


def grade_dir(bull: bool, bear: bool, fwd_ret: float) -> Optional[bool]:
    """Was the direction call right? Bull -> up, Bear -> down; Neutral -> None."""
    if bull:
        return bool(fwd_ret > 0)
    if bear:
        return bool(fwd_ret < 0)
    return None


# ------------------------------------------------------------------ #
# driver
# ------------------------------------------------------------------ #
def _predict_today(closes, vix):
    from analysis.regime_vol import sharpen_labels, sharp_state
    lab = sharpen_labels(closes, vix)
    row = lab.iloc[-1]
    d = lab.index[-1]
    return {
        "date": d.date().isoformat(),
        "close": float(closes.loc[d]), "vix": float(vix.loc[d]),
        "state": sharp_state(row),
        "stormy": bool(row["stormy"]), "calm": bool(row["calm"]),
        "bull": bool(row["bull"]), "bear": bool(row["bear"]),
    }


def _grade_rows(j: pd.DataFrame, closes) -> pd.DataFrame:
    """Fill fwd_vol/fwd_ret + hit columns for any row whose horizon has matured."""
    rets = closes.pct_change()
    dates = list(closes.index)
    pos = {t.date().isoformat(): i for i, t in enumerate(dates)}
    for i, r in j.iterrows():
        p = pos.get(r["date"])
        if p is None:
            continue
        # causal threshold: median of past w-day realized vols BEFORE this date
        for w in HORIZONS:
            if p + w >= len(dates):
                continue                                   # not matured yet
            fwd_vol = float(rets.iloc[p + 1:p + 1 + w].std())
            fwd_ret = float(closes.iloc[p + w] / closes.iloc[p] - 1.0)
            past_wv = rets.rolling(w).std().shift(-w).iloc[:p].dropna()
            thr = float(past_wv.median()) if len(past_wv) else np.nan
            j.at[i, f"fwd_vol_{w}"] = fwd_vol
            j.at[i, f"fwd_ret_{w}"] = fwd_ret
            vh = grade_vol(bool(r["stormy"]), bool(r["calm"]), fwd_vol, thr)
            dh = grade_dir(bool(r["bull"]), bool(r["bear"]), fwd_ret)
            j.at[i, f"vol_hit_{w}"] = vh
            j.at[i, f"dir_hit_{w}"] = dh
    return j


def run():
    closes = pd.read_csv(NIFTY_CSV, parse_dates=["date"], index_col="date")["close"]
    vix = pd.read_csv(VIX_CSV, parse_dates=["date"], index_col="date")["close"]

    pred = _predict_today(closes, vix)
    if os.path.exists(JOURNAL) and os.path.getsize(JOURNAL) > 0:
        j = pd.read_csv(JOURNAL)
        j = j[j["date"] != pred["date"]]                   # upsert today (idempotent)
        j = pd.concat([j, pd.DataFrame([pred])], ignore_index=True)
    else:
        j = pd.DataFrame([pred])
    j = j.sort_values("date").reset_index(drop=True)
    j = _grade_rows(j, closes)
    j.to_csv(JOURNAL, index=False)

    print("REGIME FORWARD-VALIDATION JOURNAL (live out-of-sample; predict now, grade later)")
    print(f"predictions logged: {len(j)}   latest: {pred['date']}  ->  {pred['state']} "
          f"(NIFTY {pred['close']:,.0f}, VIX {pred['vix']:.2f})\n")

    print("ROLLING SCORECARD (graded predictions only):")
    for w in HORIZONS:
        vh = j.get(f"vol_hit_{w}")
        dh = j.get(f"dir_hit_{w}")
        vh = vh.dropna() if vh is not None else pd.Series(dtype=float)
        dh = dh.dropna() if dh is not None else pd.Series(dtype=float)
        vtxt = f"{vh.mean():.0%} (n={len(vh)})" if len(vh) else "pending (none matured)"
        dtxt = f"{dh.mean():.0%} (n={len(dh)})" if len(dh) else "pending (none matured)"
        print(f"  h={w:>2}d:  vol-state hit {vtxt:<22}  direction hit {dtxt}")

    col = j.get(f"vol_hit_{HORIZONS[-1]}")
    graded = int(col.notna().sum()) if col is not None else 0
    ungraded = len(j) - graded
    print(f"\n{ungraded} prediction(s) still maturing — re-run after more sessions to grade them.")
    print(f"journal -> {JOURNAL}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(run())
