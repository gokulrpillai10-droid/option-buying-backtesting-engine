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


def persistence(raw, need=4, win=5):
    """True where raw is True today AND >=need of the last win days (incl. today)."""
    raw = raw.astype(bool)
    return raw & (raw.rolling(win).sum() >= need)


def label_regimes(closes, lookback=20, z_win=90, z_thresh=0.5,
                  persist_need=4, persist_win=5, ma_win=200, slope_win=20):
    """Daily regime labels from a close series. Returns bool DataFrame
    (bull, bear, high_vol, low_vol, stable_bull, stable_bear, stable_low_vol)
    truncated to the first row where both the volatility baseline and the
    long moving average are valid.

    Two axes, each defined by what it does well:
      * DIRECTION (bull/bear) is ABSOLUTE — price vs its long MA plus MA slope.
        A relative trend z-score (the notebook's original) is mean-reverting and
        labels ~25% of a steady rally as bear; the absolute gate does not.
      * VOLATILITY (high/low) is RELATIVE — vol z-scored vs a 90-day baseline.
        Vol genuinely is a relative concept and this axis recovers COVID cleanly.
    """
    returns = closes.pct_change()
    rolling_vol = returns.rolling(lookback).std()

    # --- volatility axis (relative z-score, unchanged) ---
    vol_sd = rolling_vol.rolling(z_win).std()
    # zero-std guard: flat window -> no signal, never inf
    vol_z = (rolling_vol - rolling_vol.rolling(z_win).mean()) / vol_sd.replace(0.0, np.nan)

    # --- direction axis (absolute: price vs long MA + MA slope) ---
    ma = closes.rolling(ma_win).mean()
    rising = ma > ma.shift(slope_win)

    # warm-up = first row where BOTH the vol baseline and the MA are valid.
    # bear uses ~rising (False where ma is NaN), so pre-warmup rows can't be
    # spuriously bear even before truncation.
    warm_ok = vol_sd.notna() & ma.notna()
    if not warm_ok.any():
        raise ValueError(f"series too short: {len(closes)} rows, "
                         f"need > {max(ma_win, lookback + z_win)}")

    r = pd.DataFrame(index=closes.index)
    r["bull"] = (closes > ma) & rising
    r["bear"] = (closes < ma) & ~rising
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
    return labels.shift(1, fill_value=False)


COVID = slice("2020-02-15", "2020-05-31")
BULL_2324 = slice("2023-05-01", "2024-09-30")


def _sanity(labels):
    """Known-history checks; returns list of failure strings (empty = pass)."""
    fails = []
    covid = labels.loc[COVID]
    hv = covid["high_vol"].mean()
    br = covid["bear"].mean()
    print(f"\nSANITY  COVID {COVID.start}..{COVID.stop} ({len(covid)}d): "
          f"high_vol {hv:.0%} (need >=50%), bear {br:.0%} (need >=30%)")
    if hv < 0.5:
        fails.append(f"COVID high_vol share {hv:.0%} < 50%")
    if br < 0.3:
        fails.append(f"COVID bear share {br:.0%} < 30%")
    bull = labels.loc[BULL_2324]
    sb = bull["stable_bull"].mean()
    sbear = bull["stable_bear"].mean()
    print(f"SANITY  bull {BULL_2324.start}..{BULL_2324.stop} ({len(bull)}d): "
          f"stable_bull {sb:.0%} (need >=30%), stable_bear {sbear:.0%} (need <=5%)")
    if sb < 0.30:
        fails.append(f"2023-24 stable_bull share {sb:.0%} < 30%")
    if sbear > 0.05:
        fails.append(f"2023-24 stable_bear share {sbear:.0%} > 5%")
    return fails


def main():
    closes = pd.read_csv(CSV, parse_dates=["date"], index_col="date")["close"]
    labels = label_regimes(closes)
    print(f"NIFTY REGIME FINDER — labeled {len(labels)} days "
          f"{labels.index[0].date()} .. {labels.index[-1].date()} "
          f"(input {CSV}, {len(closes)} rows)")

    print("\nDAYS & EPISODES per label")
    print(f"{'label':<16}{'days':>6}{'episodes':>10}{'longest':>9}")
    for col in labels.columns:
        eps = episodes(labels[col])
        longest = max((d for _, _, d in eps), default=0)
        print(f"{col:<16}{labels[col].sum():>6}{len(eps):>10}{longest:>9}")

    state = combined_state(labels)
    print("\nCOMBINED-STATE day counts:", dict(state.value_counts()))

    print("\nREGIME CALENDAR (non-Neutral combined-state runs of >=3 days)")
    run_id = (state != state.shift()).cumsum()
    for _, seg in state.groupby(run_id):
        if seg.iloc[0] != "Neutral" and len(seg) >= 3:
            print(f"  {seg.index[0].date()} .. {seg.index[-1].date()}  "
                  f"{len(seg):>4}d  {seg.iloc[0]}")

    fails = _sanity(labels)

    # Two sweeps: each axis is driven by its own params. lookback/z_thresh move
    # only the vol axis; ma_win/slope_win move only the direction axis. Sweeping
    # one against the other's column would print identical rows (misleading).
    def cell(l2, col):
        return f"{l2[col].sum():>5}/{len(episodes(l2[col])):<3}"

    print("\nVOL-AXIS SENSITIVITY (days/episodes of high_vol)")
    print(f"{'lookback':>8} {'z':>5} | {'high_vol':>12}")
    for lb in (15, 20, 30):
        for z in (0.4, 0.5, 0.6):
            l2 = label_regimes(closes, lookback=lb, z_thresh=z)
            print(f"{lb:>8} {z:>5} | {cell(l2, 'high_vol'):>12}")

    print("\nDIRECTION-AXIS SENSITIVITY (days/episodes of stable_bull | stable_bear)")
    print(f"{'ma_win':>7} {'slope':>6} | {'st_bull':>12} {'st_bear':>12}")
    for ma in (150, 200, 250):
        for sl in (10, 20, 40):
            l2 = label_regimes(closes, ma_win=ma, slope_win=sl)
            print(f"{ma:>7} {sl:>6} | {cell(l2, 'stable_bull'):>12} "
                  f"{cell(l2, 'stable_bear'):>12}")

    print("\nNOTE lagged view (shift +1 day) is the executable one; Step 2 of the "
          "spec decides GO/NO-GO on it. Episodes, not days, are the sample unit.")
    if fails:
        for f in fails:
            print(f"SANITY FAIL: {f}")
        return 1
    print("SANITY PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
