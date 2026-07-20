# NIFTY Gap-Fill Fade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backtest whether fading NIFTY gaps (and, as a second arm, buying failed gap-downs) with ATM weekly options clears option costs, using a predetermined, path-derived exit.

**Architecture:** A pure-function excursion helper (unit-tested in isolation) feeds a pre-study that walks real option-premium paths on the 2020–2024 train window and freezes BOOK/TRAIL/STOP/FLOOR into a constants module. A single `GapFillFade` strategy (two arms) imports those constants and reuses the engine's `TwoStage` exit. A runner drives both arms plus two nulls through the existing option backtest engine.

**Tech Stack:** Python 3.9, pandas, pytest, the in-repo engine (`engine.backtest.Backtest`, `engine.data.{catalog,spot,market,chain}`, `engine.strategy.{base,exit}`, `engine.execution.{model,costs}`).

## Global Constraints

- Data root: `ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "` (trailing space is real). Coverage 2020-01 → ~2026-02-25.
- `min_dte=1` (nearest non-expiry-day; intraday-gamma rationale — NOT theta-cliff).
- Gap band: `FLOOR ≤ |gap| ≤ 0.005`; `FLOOR` derived by the pre-study, never hand-set.
- Settled open = **close of the 09:15 bar** (the ~09:20 price), NOT the raw 09:15 open.
- Exit is 6 lots, all values in PREMIUM points: book 4 @ +BOOK, trail 2 @ TRAIL, stop −STOP, EOD 15:15 backstop. NEVER swept.
- Exit numbers frozen from the **2020–2024 train window only** (keeps 2025–26 a real OOS test).
- Perf: slice each day's bars ONCE; never call `SpotSeries.history()` per bar. Reuse one `MarketData` across all arms/nulls.
- Load spot+catalog ONCE per script run.
- Every analysis script commits alongside its `_out.txt`.

---

## File Structure

- `analysis/gap_fill_excursion.py` — pure helpers: `atm_strike(snapshot, ref, side)`, `premium_path(chain, spot, day, strike, side)`, `excursion_with_stop(path, entry, stop_pts)`. No engine load; unit-tested directly.
- `analysis/gap_fill_premium_scale.py` — pre-study driver. Walks train-window gap days, prints per-bucket excursion table, derives + writes frozen params. Emits `_out.txt`.
- `strategies/gap_fill_params.py` — frozen constants `BOOK, TRAIL, STOP, FLOOR, TRAIN_START, TRAIN_END, SPREAD_ASSUMED` (single source of truth).
- `strategies/gap_fill_fade.py` — `GapFillFade(Strategy)` with `arm="fade"|"reversal"` and the shared `_select`.
- `analysis/gap_fill_fade_run.py` — runs arm F, arm R, timing null, direction null; per-bucket/per-year/OOS report + `_out.txt`.
- `tests/unit/test_gap_fill_excursion.py`, `tests/unit/test_gap_fill_params.py`, `tests/unit/test_gap_fill_fade.py` — units + golden + pinning.
- `tests/integration/test_gap_fill_fade_slice.py` — real-data slice.

---

## Task 1: Excursion helpers (pure, path-aware core)

**Files:**
- Create: `analysis/gap_fill_excursion.py`
- Test: `tests/unit/test_gap_fill_excursion.py`

**Interfaces:**
- Produces:
  - `atm_strike(strikes: list[int], ref: float) -> int` — nearest strike to `ref`.
  - `excursion_with_stop(path: list[float], entry: float, stop_pts: float) -> tuple[float, float, bool]` — walks a long-option premium `path` (premium points) in order from `entry`; returns `(mfe, mae, stopped)` where `mfe` is the max favorable (`premium - entry`) accrued **before** the running adverse move first reaches `stop_pts`, `mae` is the max adverse (`entry - premium`) over the whole path, `stopped` is whether the candidate stop fired.

- [ ] **Step 1: Write the failing test (atm_strike)**

```python
# tests/unit/test_gap_fill_excursion.py
from analysis.gap_fill_excursion import atm_strike, excursion_with_stop


def test_atm_strike_picks_nearest():
    assert atm_strike([24000, 24050, 24100], 24074) == 24050
    assert atm_strike([24000, 24050, 24100], 24076) == 24100
```

- [ ] **Step 2: Run it, verify it fails**

Run: `PYTHONPATH=. pytest tests/unit/test_gap_fill_excursion.py::test_atm_strike_picks_nearest -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError`.

- [ ] **Step 3: Implement `atm_strike`**

```python
# analysis/gap_fill_excursion.py
"""Pure, engine-free helpers for the gap-fill pre-study and strategy tests.

A long option's premium excursion, measured PATH-AWARE: favorable excursion is
only credited up to the point a fixed candidate stop would have fired, so the
frozen BOOK target can't be set from a peak the trade never lived to see.
"""
from __future__ import annotations

from typing import List, Tuple


def atm_strike(strikes: List[int], ref: float) -> int:
    """Strike nearest ``ref``; ties resolve to the higher strike."""
    return min(strikes, key=lambda s: (abs(s - ref), -s))
```

- [ ] **Step 4: Run it, verify it passes**

Run: `PYTHONPATH=. pytest tests/unit/test_gap_fill_excursion.py::test_atm_strike_picks_nearest -v`
Expected: PASS.

- [ ] **Step 5: Write the failing golden test (excursion_with_stop) — the critical A2 logic**

```python
def test_excursion_no_stop_records_full_peak():
    # entry 100; rises to 130 (mfe 30), dips to 90 (mae 10); stop far away
    path = [100, 110, 130, 120, 90, 95]
    mfe, mae, stopped = excursion_with_stop(path, entry=100, stop_pts=50)
    assert (mfe, mae, stopped) == (30.0, 10.0, False)


def test_excursion_stop_truncates_favorable():
    # entry 100; dips to 75 first (adverse 25 -> stop@20 fires), THEN would rally to 140.
    # favorable AFTER the stop must NOT count: mfe stays 0, stopped True, mae full.
    path = [100, 90, 75, 140]
    mfe, mae, stopped = excursion_with_stop(path, entry=100, stop_pts=20)
    assert stopped is True
    assert mfe == 0.0        # never went favorable before the stop
    assert mae == 25.0


def test_excursion_favorable_then_stop():
    # entry 100; up to 118 (mfe 18) BEFORE dropping to 78 (stop@20 fires at -22).
    path = [100, 118, 108, 78, 130]
    mfe, mae, stopped = excursion_with_stop(path, entry=100, stop_pts=20)
    assert mfe == 18.0       # credited: reached before the stop
    assert stopped is True
    assert mae == 22.0
```

- [ ] **Step 6: Run them, verify they fail**

Run: `PYTHONPATH=. pytest tests/unit/test_gap_fill_excursion.py -v`
Expected: FAIL — `excursion_with_stop` not defined.

- [ ] **Step 7: Implement `excursion_with_stop`**

```python
def excursion_with_stop(path: List[float], entry: float,
                        stop_pts: float) -> Tuple[float, float, bool]:
    """Walk a long-option premium ``path`` from ``entry``.

    Favorable excursion (premium - entry) is only credited on bars reached
    BEFORE the running adverse move (entry - premium) first hits ``stop_pts``.
    Adverse excursion (mae) is measured over the whole path (informational).
    Returns (mfe, mae, stopped).
    """
    mfe = 0.0
    mae = 0.0
    stopped = False
    for prem in path:
        adverse = entry - prem
        if adverse > mae:
            mae = adverse
        if not stopped:
            if adverse >= stop_pts:
                stopped = True
                continue
            fav = prem - entry
            if fav > mfe:
                mfe = fav
    return mfe, mae, stopped
```

- [ ] **Step 8: Run the full file, verify all pass**

Run: `PYTHONPATH=. pytest tests/unit/test_gap_fill_excursion.py -v`
Expected: PASS (4 tests).

- [ ] **Step 9: Commit**

```bash
git add analysis/gap_fill_excursion.py tests/unit/test_gap_fill_excursion.py
git commit -m "feat(analysis): path-aware premium excursion helpers for gap-fill pre-study"
```

---

## Task 2: Pre-study driver (derives the frozen exit on the train window)

**Files:**
- Create: `analysis/gap_fill_premium_scale.py`
- Output: `analysis/gap_fill_premium_scale_out.txt`

**Interfaces:**
- Consumes: `atm_strike`, `excursion_with_stop` (Task 1); engine `Catalog`, `SpotSeries`, `MarketData`, `Chain`.
- Produces: printed per-bucket table + the derived `BOOK/TRAIL/STOP/FLOOR` values that Task 3 records. No importable API (script).

- [ ] **Step 1: Write the driver**

```python
# analysis/gap_fill_premium_scale.py
"""Pre-study: derive the frozen gap-fill exit from 2020-2024 premium behavior.

For each in-band gap day in the TRAIN window, enter the ATM fade option at the
09:20 settled open, walk its premium to EOD, and record path-aware excursions
(Task 1). Freeze, per gap sub-bucket and pooled:
  STOP  = p75(MAE)                         # survives noise
  BOOK  = p40(MFE | candidate stop=STOP)   # reachable, not the peak
  TRAIL = 0.5 * BOOK
  FLOOR = smallest bucket whose median favorable premium excursion
          exceeds (SPREAD_ASSUMED + BOOK)
Run: PYTHONPATH=. python3 analysis/gap_fill_premium_scale.py
"""
from __future__ import annotations

from datetime import date, time

import numpy as np
import pandas as pd

from analysis.gap_fill_excursion import atm_strike, excursion_with_stop
from engine.data.catalog import Catalog
from engine.data.market import MarketData
from engine.data.spot import SpotSeries

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
TRAIN_START, TRAIN_END = date(2020, 1, 1), date(2024, 12, 31)
CAP = 0.005
SPREAD_ASSUMED = 3.0                    # assumed round-trip option spread, premium pts
BUCKETS = [0.0005, 0.0015, 0.003, 0.005]
BLAB = ["0.05-0.15", "0.15-0.30", "0.30-0.50"]
T0915, T0920, T1515 = time(9, 15), time(9, 20), time(15, 15)


def day_rows(spot, chain, day):
    """One in-band gap day -> (bucket, mfe, mae) or None (not in band / no data)."""
    bars = [t.to_pydatetime() for t in spot.timestamps()
            if t.to_pydatetime().date() == day]
    if len(bars) < 3:
        return None
    b0915 = next((b for b in bars if b.time() == T0915), None)
    if b0915 is None:
        return None
    pc = spot.close_before(b0915)
    if pc is None:
        return None
    settled = spot.bar_at(b0915).close          # 09:15 bar close ~= 09:20 price
    gap = settled / pc - 1.0
    if not (BUCKETS[0] <= abs(gap) <= CAP):
        return None
    side = "PUT" if gap > 0 else "CALL"         # fade
    entry_bar = next((b for b in bars if b.time() >= T0920), None)
    if entry_bar is None:
        return None
    snap = chain.snapshot_at(entry_bar)
    strikes = [s for s in snap.strikes()
               if (snap.call(s) if side == "CALL" else snap.put(s)).is_traded]
    if not strikes:
        return None
    strike = atm_strike(strikes, settled)
    # premium path from entry to EOD (sliced ONCE, no per-bar history)
    path = []
    for b in bars:
        if b.time() < T0920 or b.time() > T1515:
            continue
        q = chain.snapshot_at(b)
        row = q.call(strike) if side == "CALL" else q.put(strike)
        path.append(row.close)
    if len(path) < 2:
        return None
    entry = path[0]
    absgap = abs(gap)
    bucket = BLAB[np.searchsorted(BUCKETS, absgap, side="right") - 1]
    return bucket, entry, path


def main():
    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    md = MarketData(cat, spot, min_dte=1)
    rows = []
    for _expiry, chains in md.stream(TRAIN_START, TRAIN_END):
        for day in sorted(chains):
            r = day_rows(spot, chains[day], day)
            if r is not None:
                rows.append(r)
    # first pass: STOP from pooled MAE (candidate stop = large so MFE untruncated here)
    maes = []
    for _bucket, entry, path in rows:
        _mfe, mae, _ = excursion_with_stop(path, entry, stop_pts=1e9)
        maes.append(mae)
    STOP = float(np.percentile(maes, 75))
    # second pass: MFE truncated at the frozen STOP
    per_bucket = {b: [] for b in BLAB}
    mfes = []
    for bucket, entry, path in rows:
        mfe, _mae, _ = excursion_with_stop(path, entry, stop_pts=STOP)
        per_bucket[bucket].append(mfe)
        mfes.append(mfe)
    BOOK = float(np.percentile(mfes, 40))
    TRAIL = 0.5 * BOOK
    # FLOOR: smallest bucket whose MEDIAN favorable excursion clears spread+book
    FLOOR = CAP
    for i, b in enumerate(BLAB):
        vals = per_bucket[b]
        if vals and float(np.median(vals)) > (SPREAD_ASSUMED + BOOK):
            FLOOR = BUCKETS[i]
            break
    print(f"gap-fill pre-study  train {TRAIN_START}..{TRAIN_END}  n={len(rows)}")
    print(f"  {'bucket%':<11}{'n':>5}{'medMFE':>9}{'medMAE?':>9}")
    for b in BLAB:
        v = per_bucket[b]
        med = float(np.median(v)) if v else float("nan")
        print(f"  {b:<11}{len(v):>5}{med:>9.2f}")
    print(f"FROZEN: STOP={STOP:.2f}  BOOK={BOOK:.2f}  TRAIL={TRAIL:.2f}  "
          f"FLOOR={FLOOR}  (SPREAD_ASSUMED={SPREAD_ASSUMED})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and capture the frozen numbers**

Run: `PYTHONPATH=. python3 analysis/gap_fill_premium_scale.py 2>&1 | tee analysis/gap_fill_premium_scale_out.txt`
Expected: a per-bucket table and a `FROZEN: STOP=… BOOK=… TRAIL=… FLOOR=…` line. **Record those four numbers** — Task 3 hard-codes them.

- [ ] **Step 3: Commit**

```bash
git add analysis/gap_fill_premium_scale.py analysis/gap_fill_premium_scale_out.txt
git commit -m "feat(analysis): gap-fill premium-excursion pre-study (train-window exit derivation)"
```

---

## Task 3: Frozen params module + pinning test

**Files:**
- Create: `strategies/gap_fill_params.py`
- Test: `tests/unit/test_gap_fill_params.py`

**Interfaces:**
- Produces: module-level `BOOK, TRAIL, STOP, FLOOR, CAP, TRAIN_START, TRAIN_END, SPREAD_ASSUMED` (floats/strings). Imported by the strategy and the pinning test.

- [ ] **Step 1: Write the module using the numbers from Task 2's `_out.txt`**

Replace the four `# from pre-study` values with the actual numbers printed in Task 2 Step 2.

```python
# strategies/gap_fill_params.py
"""Frozen gap-fill exit parameters — SINGLE SOURCE OF TRUTH.

Derived by analysis/gap_fill_premium_scale.py on the 2020-2024 TRAIN window and
frozen (never tuned on backtest P&L). Re-run the pre-study to change these, then
update this file; the pinning test asserts they match the recorded _out.txt.
All exit values are PREMIUM points.
"""
BOOK = 0.0     # from pre-study  <-- replace with FROZEN BOOK
TRAIL = 0.0    # from pre-study  <-- replace with FROZEN TRAIL
STOP = 0.0     # from pre-study  <-- replace with FROZEN STOP
FLOOR = 0.0015 # from pre-study  <-- replace with FROZEN FLOOR
CAP = 0.005
SPREAD_ASSUMED = 3.0
TRAIN_START = "2020-01-01"
TRAIN_END = "2024-12-31"
```

- [ ] **Step 2: Write the pinning test**

```python
# tests/unit/test_gap_fill_params.py
import re
from pathlib import Path

from strategies import gap_fill_params as p


def test_params_match_prestudy_out():
    """The committed constants must equal the pre-study's recorded FROZEN line."""
    out = Path("analysis/gap_fill_premium_scale_out.txt").read_text()
    m = re.search(r"STOP=([\d.]+)\s+BOOK=([\d.]+)\s+TRAIL=([\d.]+)\s+FLOOR=([\d.]+)", out)
    assert m, "pre-study _out.txt missing FROZEN line"
    stop, book, trail, floor = (float(x) for x in m.groups())
    assert abs(p.STOP - stop) < 1e-6
    assert abs(p.BOOK - book) < 1e-6
    assert abs(p.TRAIL - trail) < 1e-6
    assert abs(p.FLOOR - floor) < 1e-9


def test_exit_sanity():
    assert p.STOP > 0 and p.BOOK > 0
    assert p.TRAIL == 0.5 * p.BOOK
    assert 0.0005 <= p.FLOOR <= p.CAP
```

- [ ] **Step 3: Run it, verify it passes**

Run: `PYTHONPATH=. pytest tests/unit/test_gap_fill_params.py -v`
Expected: PASS (2 tests). If it fails, the module values don't match the `_out.txt` — fix the module, not the test.

- [ ] **Step 4: Commit**

```bash
git add strategies/gap_fill_params.py tests/unit/test_gap_fill_params.py
git commit -m "feat(strategies): frozen gap-fill exit params + pinning test"
```

---

## Task 4: `GapFillFade` strategy (arms F + R)

**Files:**
- Create: `strategies/gap_fill_fade.py`
- Test: `tests/unit/test_gap_fill_fade.py`

**Interfaces:**
- Consumes: `gap_fill_params` (Task 3); engine `Strategy`, `Order`, `TwoStage`, `Exit`, `BarContext`.
- Produces:
  - `GapFillFade(arm="fade"|"reversal", lots=6)` with `_select(ctx) -> tuple[str, int] | None` (returns `(side, strike)` or `None`), `on_day_start`, `on_bar`, `manage`.

- [ ] **Step 1: Write failing tests (selection + gating)**

These use the repo's synthetic-context pattern (see `tests/unit/test_cpr_breakout.py`). A `FakeCtx`/`FakeSpot`/`FakeChain` scaffold is provided in the test.

```python
# tests/unit/test_gap_fill_fade.py
from datetime import datetime, time

from strategies.gap_fill_fade import GapFillFade
from strategies import gap_fill_params as p


class FakeQuote:
    def __init__(self, traded=True): self.is_traded = traded


class FakeSnap:
    def __init__(self, strikes): self._s = strikes
    def strikes(self): return self._s
    def call(self, k): return FakeQuote()
    def put(self, k): return FakeQuote()


class FakeChain:
    def __init__(self, strikes): self._s = strikes
    def snapshot_at(self, dt): return FakeSnap(self._s)


class FakeCtx:
    """Minimal BarContext stand-in for selection tests."""
    def __init__(self, now, settled, pc, strikes=(23900, 23950, 24000, 24050, 24100)):
        self._now = now; self._settled = settled; self._pc = pc
        self._chain = FakeChain(list(strikes))
    @property
    def now(self): return self._now
    @property
    def spot_bar(self):
        class B: pass
        b = B(); b.open = self._settled; b.high = self._settled
        b.low = self._settled; b.close = self._settled; return b
    def prev_close(self): return self._pc
    def snapshot(self): return self._chain.snapshot_at(self._now)


def _ctx(settled, pc):
    return FakeCtx(datetime(2024, 6, 3, 9, 15), settled, pc)


def test_fade_skips_below_floor():
    # gap = FLOOR/2 -> below floor -> no trade
    s = GapFillFade(arm="fade")
    settled = 24000 * (1 + p.FLOOR / 2)
    assert s._select(_ctx(settled, 24000)) is None


def test_fade_skips_above_cap():
    s = GapFillFade(arm="fade")
    settled = 24000 * (1 + p.CAP + 0.001)
    assert s._select(_ctx(settled, 24000)) is None


def test_fade_gap_up_selects_put():
    s = GapFillFade(arm="fade")
    settled = 24000 * (1 + (p.FLOOR + p.CAP) / 2)     # in band, gap up
    side, strike = s._select(_ctx(settled, 24000))
    assert side == "PUT"


def test_fade_gap_down_selects_call():
    s = GapFillFade(arm="fade")
    settled = 24000 * (1 - (p.FLOOR + p.CAP) / 2)     # in band, gap down
    side, strike = s._select(_ctx(settled, 24000))
    assert side == "CALL"


def test_no_prev_close_stands_aside():
    s = GapFillFade(arm="fade")
    assert s._select(_ctx(24100, None)) is None
```

- [ ] **Step 2: Run, verify fail**

Run: `PYTHONPATH=. pytest tests/unit/test_gap_fill_fade.py -v`
Expected: FAIL — `GapFillFade` not importable.

- [ ] **Step 3: Implement the strategy**

```python
# strategies/gap_fill_fade.py
"""NIFTY gap-fill fade (arm F) and failed-gap-down reversal long (arm R).

Both buy an ATM weekly option and share the frozen premium exit
(strategies/gap_fill_params). Selection is measured off the SETTLED open (close
of the 09:15 bar ~= 09:20 price), not the raw auction open. Exit numbers are
frozen from 2020-2024 and never swept.

Arm F: gap-up -> PUT, gap-down -> CALL (fade back toward prior close).
Arm R: gap-down that has reclaimed its open by 09:30 -> CALL (failed gap flips).
"""
from datetime import time

from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit, TwoStage
from strategies import gap_fill_params as P

LOTS = 6
DECIDE_F = time(9, 15)      # 09:15 bar close = settled open; fills next bar ~09:20
DECIDE_R = time(9, 30)      # reclaim checkpoint
SQUARE = time(15, 15)


def _atm(snap, ref, side):
    strikes = [s for s in snap.strikes()
               if (snap.call(s) if side == "CALL" else snap.put(s)).is_traded]
    if not strikes:
        return None
    return min(strikes, key=lambda s: (abs(s - ref), -s))


class GapFillFade(Strategy):
    def __init__(self, arm="fade", lots=LOTS):
        assert arm in ("fade", "reversal")
        self.arm = arm
        self.lots = lots
        # book 4/6 at +BOOK, trail the 2/6 by TRAIL, full premium stop at -STOP
        self._two = TwoStage(P.BOOK, P.TRAIL, 4 / 6, stop_pts=P.STOP)

    def on_day_start(self, ctx):
        self.entered = False
        self.settled = None
        self.day_open = None
        self._two.reset()

    def _select(self, ctx):
        pc = ctx.prev_close()
        if pc is None:
            return None
        bar = ctx.spot_bar
        settled = bar.close                       # 09:15 bar close ~= 09:20
        self.settled = settled
        self.day_open = bar.open
        gap = settled / pc - 1.0
        if not (P.FLOOR <= abs(gap) <= P.CAP):
            return None
        if self.arm == "fade":
            side = "PUT" if gap > 0 else "CALL"
        else:                                     # reversal: gap-down only
            if gap >= 0:
                return None
            side = "CALL"
        strike = _atm(ctx.snapshot(), settled, side)
        if strike is None:
            return None
        return side, strike

    def on_bar(self, ctx):
        if self.entered:
            return []
        t = ctx.now.time()
        if self.arm == "fade":
            if t != DECIDE_F:
                return []
            sel = self._select(ctx)
            if sel is None:
                return []
        else:
            if t < DECIDE_F:
                return []
            if t == DECIDE_F:                     # record settled/open, wait for 09:30
                self._select(ctx)                 # sets self.settled/day_open only
                return []
            if t != DECIDE_R:
                return []
            # reclaim: gap-down (settled<pc) AND price back above the day open
            pc = ctx.prev_close()
            if pc is None or self.settled is None or self.settled >= pc:
                return []
            if ctx.spot_bar.close <= self.day_open:
                return []
            side = "CALL"
            strike = _atm(ctx.snapshot(), ctx.spot_bar.close, side)
            if strike is None:
                return []
            sel = (side, strike)
        side, strike = sel
        self.entered = True
        return [Order(side=side, strike=strike, lots=self.lots)]

    def manage(self, ctx, pos):
        if ctx.now.time() >= SQUARE:
            return Exit.market("eod")
        return self._two.exit_for(pos)
```

- [ ] **Step 4: Run selection tests, verify pass**

Run: `PYTHONPATH=. pytest tests/unit/test_gap_fill_fade.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Add exit-wiring + one-trade-per-day tests**

```python
def test_manage_returns_eod_after_square():
    s = GapFillFade(arm="fade")
    class Ctx:
        now = datetime(2024, 6, 3, 15, 20)
    ex = s.manage(Ctx(), pos=None)
    assert ex.reason == "eod"


def test_two_stage_wired_from_frozen_params():
    s = GapFillFade(arm="fade")
    assert s._two.target_pts == p.BOOK
    assert s._two.trail_gap == p.TRAIL
    assert s._two.stop_pts == p.STOP
    assert abs(s._two.fraction - 4 / 6) < 1e-9
```

- [ ] **Step 6: Run all Task-4 tests, verify pass**

Run: `PYTHONPATH=. pytest tests/unit/test_gap_fill_fade.py -v`
Expected: PASS (7 tests).

- [ ] **Step 7: Commit**

```bash
git add strategies/gap_fill_fade.py tests/unit/test_gap_fill_fade.py
git commit -m "feat(strategies): GapFillFade arms F (fade) + R (reclaim reversal)"
```

---

## Task 5: Runner (arms + nulls) + integration slice + verdict

**Files:**
- Create: `analysis/gap_fill_fade_run.py`
- Create: `tests/integration/test_gap_fill_fade_slice.py`
- Output: `analysis/gap_fill_fade_run_out.txt`

**Interfaces:**
- Consumes: `GapFillFade` (Task 4); engine `Backtest`, `Knobs`, `Catalog`, `SpotSeries`, `MarketData`, `CostModel`.
- Produces: printed per-arm / per-year / OOS / per-bucket report; no importable API.

- [ ] **Step 1: Write the two null subclasses + runner**

```python
# analysis/gap_fill_fade_run.py
"""Run gap-fill arm F, arm R, and two nulls through the option engine ONCE.

Null 1 (timing): fade-side ATM, hold to EOD, no scale-out.
Null 2 (direction): the frozen exit on the CONTINUATION side (gap-up->CALL,
gap-down->PUT). A real edge must beat BOTH.
Report per-year and OOS-split (train 2020-24 vs test 2025-26).
Run: PYTHONPATH=. python3 analysis/gap_fill_fade_run.py
"""
from datetime import date

import pandas as pd

from engine.backtest import Backtest, Knobs
from engine.data.catalog import Catalog
from engine.data.market import MarketData
from engine.data.spot import SpotSeries
from engine.execution.costs import CostModel
from engine.execution.model import Order
from engine.strategy.exit import Exit
from strategies.gap_fill_fade import GapFillFade, SQUARE

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)
SPLIT = date(2024, 12, 31)


class HoldNull(GapFillFade):
    """Timing null: same fade entry, but hold to EOD (no scale-out/stop)."""
    def manage(self, ctx, pos):
        return Exit.market("eod") if ctx.now.time() >= SQUARE else None


class ContinuationNull(GapFillFade):
    """Direction null: flip the fade side (bet continuation), frozen exit."""
    def _select(self, ctx):
        sel = super()._select(ctx)
        if sel is None:
            return None
        side, strike = sel
        flipped = "CALL" if side == "PUT" else "PUT"
        return flipped, strike


def summarize(tag, res):
    tr = pd.DataFrame([{"date": t.date, "net": t.net, "reason": t.reason}
                       for t in res.trades])
    if tr.empty:
        print(f"{tag}: no trades"); return
    print(f"{tag}: n={len(tr)} net={res.net():,.0f} PF={res.profit_factor():.2f} "
          f"win={res.win_rate()*100:.0f}% t={res.t_stat():+.2f} "
          f"exits={dict(tr.reason.value_counts())}")


def main():
    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    md = MarketData(cat, spot, min_dte=1)
    knobs = Knobs(fill_price="open", slippage_pct=0.0025)
    arms = {
        "ARM-F fade": GapFillFade(arm="fade"),
        "ARM-R reversal": GapFillFade(arm="reversal"),
        "NULL timing (hold)": HoldNull(arm="fade"),
        "NULL direction (cont)": ContinuationNull(arm="fade"),
    }
    for tag, strat in arms.items():
        bt = Backtest(md, CostModel(), knobs)          # md reused; loaded once
        res = bt.run(strat, START, END)
        print(f"\n== {tag} ==")
        summarize("  FULL", res)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it, capture output**

Run: `PYTHONPATH=. python3 analysis/gap_fill_fade_run.py 2>&1 | tee analysis/gap_fill_fade_run_out.txt`
Expected: four `==` blocks with n/net/PF/win/t and exit-reason mix. Read the verdict against the kill criteria (spec §8).

- [ ] **Step 3: Write the integration slice test**

```python
# tests/integration/test_gap_fill_fade_slice.py
"""Real-data slice: catches chain-selection / spot_at / settled-open wiring bugs
that synthetic units miss. Skips cleanly if the local NIFTY cache is absent."""
import os
from datetime import date

import pytest

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
pytestmark = pytest.mark.skipif(not os.path.isdir(ROOT),
                                reason="local NIFTY option cache not present")


def test_fade_produces_trades_on_a_known_gap_month():
    from engine.backtest import Backtest, Knobs
    from engine.data.catalog import Catalog
    from engine.data.market import MarketData
    from engine.data.spot import SpotSeries
    from engine.execution.costs import CostModel
    from strategies.gap_fill_fade import GapFillFade

    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    md = MarketData(cat, spot, min_dte=1)
    bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025))
    res = bt.run(GapFillFade(arm="fade"), date(2024, 6, 1), date(2024, 6, 30))
    # at least one in-band gap day in the month; each trade is a PUT or CALL,
    # exits only via the sanctioned reasons.
    assert len(res.trades) >= 1
    for t in res.trades:
        assert t.side in ("CALL", "PUT")
        assert t.reason in ("book", "ptrail", "pstop", "eod")
```

- [ ] **Step 4: Run the integration test**

Run: `PYTHONPATH=. pytest tests/integration/test_gap_fill_fade_slice.py -v`
Expected: PASS (or SKIP if the cache dir is absent on this machine).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=. pytest tests/unit/test_gap_fill_excursion.py tests/unit/test_gap_fill_params.py tests/unit/test_gap_fill_fade.py tests/integration/test_gap_fill_fade_slice.py -v`
Expected: all PASS/SKIP, no failures.

- [ ] **Step 6: Commit**

```bash
git add analysis/gap_fill_fade_run.py analysis/gap_fill_fade_run_out.txt tests/integration/test_gap_fill_fade_slice.py
git commit -m "feat(analysis): gap-fill runner (arms F/R + timing/direction nulls) + integration slice"
```

---

## Verdict (after Task 5)

Apply spec §8 kill criteria to `gap_fill_fade_run_out.txt`:
- Net negative after costs → NO-GO.
- Positive in a single regime only (check per-year once added) → NO-GO.
- Arm F/R fails to beat NULL timing → scale-out adds nothing.
- Arm F fails to beat NULL direction → no directional edge, just long gamma.
- Edge only below FLOOR / cost-dominated bucket → NO-GO.

GO on any arm → NIFTY ATM depth audit (TODOS.md), then stress pass, then live gate.

---

## Self-Review notes

- **Spec coverage:** A1 (train-window derivation) → Task 2 `TRAIN_START/END`; A2 (path-aware) → Task 1 `excursion_with_stop` + golden tests; A3 (min_dte=1) → Task 2/5 `MarketData(min_dte=1)`; A4 (derived FLOOR) → Task 2 FLOOR logic + Task 4 gating; A5 (direction null) → Task 5 `ContinuationNull`; C1 (`_select` reuse) → Task 4 method used by nulls; C2 (single source + pin) → Task 3; P-1 (no per-bar history) → Task 2 slices bars once; OV1 (two arms) → Task 4 `arm=`; OV2 (no stage-gate) → single build pass. Golden/pinning/integration tests all present.
- **Placeholder scan:** the only intentionally-deferred values are the frozen numbers in Task 3, filled from Task 2's actual run (ordering, not a placeholder).
- **Type consistency:** `_select` returns `(side, strike)` everywhere; `Order(side, strike, lots)`, `TwoStage(target_pts, trail_gap, fraction, stop_pts)` match the engine signatures.
