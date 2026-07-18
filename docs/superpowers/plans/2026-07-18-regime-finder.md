# NIFTY Regime Finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the NIFTY regime calendar 2019→present (episodes, counts, sanity checks, sensitivity) per Step 1 of `docs/superpowers/specs/2026-07-18-regime-finder-design.md`.

**Architecture:** One analysis script (`analysis/regime_finder.py`) holding the ported-and-fixed rule-based labeler as pure functions (testable), plus a `main()` that loads `analysis/nifty_daily.csv`, prints the calendar/counts/sanity/sensitivity report, and exits 1 on sanity failure. Unit tests use synthetic series. Output committed as `analysis/regime_finder_out.txt` per repo convention.

**Tech Stack:** Python 3.9, pandas, numpy, pytest (existing `tests/unit/`).

## Global Constraints

- Input file: `analysis/nifty_daily.csv` (columns `date,open,high,low,close`; 2019-01-02 → present). Never `daily_underlying.csv` (COVID-in-warm-up + bad 2023-03-01 row — see spec).
- Labeler params (spec): lookback=20, z_win=90, z_thresh=0.5, persistence 4-of-5 (current day counts as one of the 5).
- Warm-up must be an explicit truncation (the notebook's `dropna()` was a no-op); zero rolling-std must yield "no signal", never inf.
- All label columns are plain `bool`; `lagged()` is the executable view (shift +1 day).
- Sanity failure → print `SANITY FAIL ...` and `sys.exit(1)`.
- Run tests: `python3 -m pytest tests/unit/test_regime_finder.py -v` from repo root.
- Script run: `PYTHONPATH=. python3 analysis/regime_finder.py | tee analysis/regime_finder_out.txt`.

---

### Task 1: Labeler core (`persistence`, `label_regimes`)

**Files:**
- Create: `analysis/regime_finder.py`
- Test: `tests/unit/test_regime_finder.py`

**Interfaces:**
- Consumes: nothing (pure pandas).
- Produces:
  - `persistence(raw: pd.Series, need: int = 4, win: int = 5) -> pd.Series` — bool: raw is True today AND ≥`need` of last `win` days (window includes today).
  - `label_regimes(closes: pd.Series, lookback: int = 20, z_win: int = 90, z_thresh: float = 0.5, persist_need: int = 4, persist_win: int = 5) -> pd.DataFrame` — bool columns `bull, bear, high_vol, low_vol, stable_bull, stable_bear, stable_low_vol`; index truncated to the first row where both z-scores have full windows (warm-up cut ≈ `lookback + z_win` rows).

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the regime labeler (analysis/regime_finder.py).

Synthetic-series design: the labels are z-scores RELATIVE to a 90-day
baseline, so a steady trend scores z~0. To force a label, a series must
CHANGE character: flat-then-rally -> bull, flat-then-crash -> bear,
quiet-then-wild -> high_vol.
"""
import numpy as np
import pandas as pd
import pytest

from analysis.regime_finder import persistence, label_regimes

IDX = lambda n: pd.date_range("2019-01-01", periods=n, freq="B")


def _series(vals):
    return pd.Series(vals, index=IDX(len(vals)))


def make_closes(n_flat=250, n_move=80, drift=0.0, vol=0.002, seed=7, start=100.0):
    rng = np.random.default_rng(seed)
    flat = rng.normal(0.0, 0.002, n_flat)
    move = rng.normal(drift, vol, n_move)
    rets = np.concatenate([flat, move])
    return _series(start * np.cumprod(1.0 + rets))


def test_persistence_four_of_five_passes():
    raw = _series([True, True, True, True, False, True]).astype(bool)
    out = persistence(raw, need=4, win=5)
    assert bool(out.iloc[5])  # T,T,T,F,T window has 4 Trues and today is True


def test_persistence_three_of_five_fails():
    raw = _series([True, True, False, False, True, True]).astype(bool)
    out = persistence(raw, need=4, win=5)
    assert not bool(out.iloc[5])  # only 3 of last 5


def test_persistence_requires_today_true():
    raw = _series([True, True, True, True, True, False]).astype(bool)
    out = persistence(raw, need=4, win=5)
    assert not bool(out.iloc[5])  # 4-of-5 satisfied but today is False


def test_warmup_truncated():
    closes = make_closes()
    labels = label_regimes(closes)
    # first valid row needs lookback (20) + z_win (90) observations
    assert labels.index[0] >= closes.index[109]
    assert labels.index[0] <= closes.index[130]  # not over-truncated
    assert labels.notna().all().all()


def test_flat_series_no_crash_no_labels():
    closes = _series(np.full(400, 100.0))  # literally constant: rolling stds are 0
    labels = label_regimes(closes)
    assert not labels[["bull", "bear", "high_vol", "low_vol"]].any().any()
    assert labels.dtypes.eq(bool).all()


def test_rally_after_flat_labels_bull():
    closes = make_closes(drift=0.006, vol=0.004)
    labels = label_regimes(closes)
    move = labels.iloc[-60:]  # inside the rally segment
    assert move["bull"].sum() > 0
    assert move["stable_bull"].sum() > 0
    assert move["stable_bear"].sum() == 0


def test_crash_after_flat_labels_bear_and_high_vol():
    closes = make_closes(drift=-0.008, vol=0.02)
    labels = label_regimes(closes)
    move = labels.iloc[-60:]
    assert move["bear"].sum() > 0
    assert move["stable_bear"].sum() > 0
    assert move["high_vol"].sum() > 0
    assert move["stable_bull"].sum() == 0


def test_vol_spike_alone_labels_high_vol():
    closes = make_closes(drift=0.0, vol=0.025)
    labels = label_regimes(closes)
    assert labels.iloc[-60:]["high_vol"].sum() > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_regime_finder.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'analysis.regime_finder'` (or ImportError for the names).

- [ ] **Step 3: Write the implementation**

Create `analysis/regime_finder.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_regime_finder.py -v`
Expected: 9 passed. If a synthetic-series test is flaky on thresholds, tune the test's `drift`/`vol` magnitudes (labels are z-relative; the *move* segments must contrast with the flat segment) — do not weaken the labeler.

- [ ] **Step 5: Commit**

```bash
git add analysis/regime_finder.py tests/unit/test_regime_finder.py
git commit -m "feat(analysis): regime labeler core — notebook port with warm-up + zero-std fixes"
```

---

### Task 2: Episode/calendar helpers (`episodes`, `combined_state`, `lagged`)

**Files:**
- Modify: `analysis/regime_finder.py` (append below `label_regimes`)
- Test: `tests/unit/test_regime_finder.py` (append)

**Interfaces:**
- Consumes: `label_regimes` output DataFrame (bool columns as defined in Task 1).
- Produces:
  - `episodes(mask: pd.Series) -> list[tuple]` — `(start: Timestamp, end: Timestamp, days: int)` per contiguous True run.
  - `combined_state(labels: pd.DataFrame) -> pd.Series` — one exclusive daily state: `HighVol_Bear | HighVol_Bull | LowVol_Bear | LowVol_Bull | Neutral` (high-vol takes priority, mirroring the notebook's four-regime strategy).
  - `lagged(labels: pd.DataFrame) -> pd.DataFrame` — labels shifted +1 day (the executable view), first row False.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_regime_finder.py`)

```python
from analysis.regime_finder import episodes, combined_state, lagged


def test_episodes_finds_contiguous_runs():
    mask = _series([False, True, True, False, True]).astype(bool)
    eps = episodes(mask)
    assert len(eps) == 2
    (s1, e1, d1), (s2, e2, d2) = eps
    assert (s1, e1, d1) == (mask.index[1], mask.index[2], 2)
    assert (s2, e2, d2) == (mask.index[4], mask.index[4], 1)


def test_episodes_empty_mask():
    assert episodes(_series([False, False]).astype(bool)) == []


def test_combined_state_priority_high_vol_wins():
    labels = pd.DataFrame(
        {
            "high_vol": [True, False, False, False],
            "stable_low_vol": [True, True, True, False],
            "stable_bull": [True, True, False, False],
            "stable_bear": [False, False, True, False],
        },
        index=IDX(4),
    )
    s = combined_state(labels)
    assert list(s) == ["HighVol_Bull", "LowVol_Bull", "LowVol_Bear", "Neutral"]


def test_lagged_shifts_one_day():
    labels = label_regimes(make_closes(drift=-0.008, vol=0.02))
    lag = lagged(labels)
    assert not lag.iloc[0].any()  # day one: nothing known yet
    assert (lag["bear"].iloc[1:].values == labels["bear"].iloc[:-1].values).all()
    assert lag.dtypes.eq(bool).all()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_regime_finder.py -v`
Expected: 4 new tests fail with ImportError/AttributeError; Task 1's 9 still pass.

- [ ] **Step 3: Write the implementation** (append to `analysis/regime_finder.py`)

```python
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
    return labels.shift(1).fillna(False).astype(bool)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_regime_finder.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add analysis/regime_finder.py tests/unit/test_regime_finder.py
git commit -m "feat(analysis): regime episodes, combined state, lagged view"
```

---

### Task 3: Report `main()` — calendar, counts, sanity, sensitivity — run on real data

**Files:**
- Modify: `analysis/regime_finder.py` (append `main()` + `__main__` guard)
- Create (generated): `analysis/regime_finder_out.txt`

**Interfaces:**
- Consumes: `label_regimes`, `episodes`, `combined_state`, `lagged` (Tasks 1–2); `analysis/nifty_daily.csv`.
- Produces: the committed report; exit code 1 on sanity failure (0 otherwise).

- [ ] **Step 1: Write `main()`** (append to `analysis/regime_finder.py`)

```python
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
          f"stable_bull {sb:.0%} (need >=10%), stable_bear {sbear:.0%} (need <=5%)")
    if sb < 0.10:
        fails.append(f"2023-24 stable_bull share {sb:.0%} < 10%")
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

    print("\nSENSITIVITY (days/episodes of high_vol | stable_bull | stable_bear)")
    print(f"{'lookback':>8} {'z':>5} | {'high_vol':>12} {'st_bull':>12} {'st_bear':>12}")
    for lb in (15, 20, 30):
        for z in (0.4, 0.5, 0.6):
            l2 = label_regimes(closes, lookback=lb, z_thresh=z)
            cells = []
            for col in ("high_vol", "stable_bull", "stable_bear"):
                cells.append(f"{l2[col].sum():>5}/{len(episodes(l2[col])):<3}")
            print(f"{lb:>8} {z:>5} | " + " ".join(f"{c:>12}" for c in cells))

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
```

- [ ] **Step 2: Run the full test suite (regression)**

Run: `python3 -m pytest tests/unit/test_regime_finder.py -v`
Expected: 13 passed (main() adds no importable behavior change).

- [ ] **Step 3: Run on real data**

Run: `PYTHONPATH=. python3 analysis/regime_finder.py | tee analysis/regime_finder_out.txt; echo "exit=$?"`
Expected: report prints; last lines `SANITY PASS` and `exit=0`. Eyeball the calendar: COVID (2020-02→05) must appear as HighVol_Bear runs; 2023–24 should show LowVol_Bull/Neutral stretches; episode counts should be single-digit-to-low-double-digit, not hundreds (hundreds ⇒ labels are whipsawing ⇒ investigate before committing).

If `SANITY FAIL`: debug the labeler against the spec (do NOT loosen thresholds to pass; the thresholds encode "the labeler must recover known history").

- [ ] **Step 4: Commit**

```bash
git add analysis/regime_finder.py analysis/regime_finder_out.txt
git commit -m "feat(analysis): NIFTY regime calendar 2019-2026 — episodes, sanity, sensitivity"
```

---

## Self-Review (done at write time)

- **Spec coverage:** calendar ✔ (Task 3), episode counts ✔ (Task 3), sanity check ✔ (`_sanity`, exits 1), sensitivity ✔ (3×3 grid), lagged view ✔ (Task 2 + note), warm-up fix ✔ (Task 1), zero-std fix ✔ (Task 1), tests ✔ (Tasks 1–2 cover every listed case; persistence boundary = 3 tests).
- **Placeholders:** none — all code inline.
- **Type consistency:** `label_regimes` → bool DataFrame consumed by `episodes` (Series), `combined_state` (DataFrame), `lagged` (DataFrame); names match across tasks.
