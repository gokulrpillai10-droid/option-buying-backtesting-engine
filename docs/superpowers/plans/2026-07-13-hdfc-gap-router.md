# HDFCBANK Gap Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a HDFCBANK intraday cash-equity strategy that routes each morning's gap into a fade (gap-fill) or momentum (gap-and-go) trade using gap size, NIFTY alignment, and the first-15-minute drive, and backtest it out-of-sample net of costs.

**Architecture:** A pure `gap_features` module (decision logic) + a `NiftyGapProvider` (per-date NIFTY gap from an in-repo snapshot) feed a `GapRouterEquity` strategy that plugs into the existing `EquityBacktest` engine unchanged. Two checkpoints per day (09:15 open, 09:30) classify into fade/go/no-trade; exits are level targets + opening-range stops + EOD square-off.

**Tech Stack:** Python, pandas, pytest. Reuses `engine/equity/` (backtest, context, model, costs, position) and `engine/strategy/` (base, exit).

## Global Constraints

- Cash equity only; intraday (MIS), square off by EOD. No engine (`engine/`) changes.
- Backtest reads only in-repo cached data (offline, reproducible) — never the sibling `tradealgo` repo at backtest time.
- Costs via `EquityCostModel` + `EquityKnobs.slippage_pct` (0.05%/side). No invented cost numbers.
- One trade per day, one position at a time (engine enforces one order/bar).
- Run the router on 1-min bars: `EquityKnobs(timeframe="1min")`.
- Starting thresholds (fit on train, validate OOS): `gap_thr=0.0015`, `big_thr=0.004`, `nifty_min=0.001`, `drive_thr=0.003`.
- Router feature/classification logic lives ONLY in `engine/equity/gap_features.py` (DRY — analysis scripts and the strategy both import it).

---

## File Structure

- Create `engine/equity/gap_features.py` — pure router logic (thresholds, feature calc, decisions). No pandas, no I/O.
- Create `engine/equity/nifty_gap.py` — `NiftyGapProvider`, loads the in-repo NIFTY gap snapshot.
- Create `analysis/snapshot_nifty_gap.py` — builds `data_index/NIFTY/gap.parquet` from the NIFTY 5-min source (pure `build_gap_table` + thin main).
- Create `data_index/NIFTY/gap.parquet` — checked-in per-date NIFTY gap snapshot (produced by the script).
- Create `strategies/gap_router_equity.py` — `GapRouterEquity` strategy.
- Create `analysis/gap_router_backtest.py` — OOS train/test backtest runner with threshold sweep.
- Modify `analysis/hdfc_gapandgo.py` — import from `gap_features` instead of inline (DRY closeout).
- Test `tests/unit/test_gap_features.py`, `tests/unit/test_nifty_gap.py`, `tests/unit/test_gap_router_equity.py`.

---

### Task 1: gap_features pure module

**Files:**
- Create: `engine/equity/gap_features.py`
- Test: `tests/unit/test_gap_features.py`
- Modify: `analysis/hdfc_gapandgo.py` (DRY closeout, final steps)

**Interfaces:**
- Produces:
  - `Thresholds(gap_thr=0.0015, big_thr=0.004, nifty_min=0.001, drive_thr=0.003)` (frozen dataclass)
  - `Decision(branch: str, side: Optional[str])` where `branch ∈ {"fade","go","none","defer"}`, `side ∈ {"long","short",None}`
  - `compute_gap(open_: float, prev_close: float) -> float`
  - `signed_drive(px: float, open_: float, gap: float) -> float`
  - `aligned(gap: float, ngap: Optional[float], thr: Thresholds) -> bool`
  - `fade_side(gap: float) -> str`, `go_side(gap: float) -> str`
  - `decide_open(gap: float, ngap: Optional[float], thr: Thresholds) -> Decision`
  - `decide_0930(gap: float, ngap: Optional[float], drive: float, thr: Thresholds) -> Decision`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_gap_features.py
from engine.equity.gap_features import (
    Thresholds, Decision, compute_gap, signed_drive, aligned,
    fade_side, go_side, decide_open, decide_0930)

THR = Thresholds()

def test_compute_gap_and_drive():
    assert compute_gap(810.0, 800.0) == 0.0125
    # drive is signed by gap direction: price up on an up-gap = positive
    assert signed_drive(815.0, 810.0, gap=0.0125) > 0
    # price up on a DOWN-gap = reverting = negative drive
    assert signed_drive(806.0, 800.0, gap=-0.01) < 0

def test_aligned():
    assert aligned(0.01, 0.005, THR) is True          # same sign, big enough
    assert aligned(0.01, 0.0005, THR) is False         # NIFTY gap under nifty_min
    assert aligned(0.01, -0.005, THR) is False         # opposite sign
    assert aligned(0.01, None, THR) is False           # missing NIFTY

def test_decide_open_no_gap_is_none():
    assert decide_open(0.001, None, THR) == Decision("none", None)

def test_decide_open_small_unaligned_fades_at_open():
    # gap up 0.25% (< big 0.4%), NIFTY not aligned -> fade short now
    assert decide_open(0.0025, None, THR) == Decision("fade", "short")
    # gap down mirror -> fade long
    assert decide_open(-0.0025, None, THR) == Decision("fade", "long")

def test_decide_open_big_or_aligned_defers():
    assert decide_open(0.01, None, THR).branch == "defer"        # big gap
    assert decide_open(0.002, 0.005, THR).branch == "defer"      # small but aligned

def test_decide_0930_reverting_fades():
    # deferred up-gap, price reverted by 09:30 (drive<=0) -> fade short
    assert decide_0930(0.01, None, drive=-0.004, thr=THR) == Decision("fade", "short")

def test_decide_0930_driving_and_big_goes():
    assert decide_0930(0.01, None, drive=0.006, thr=THR) == Decision("go", "long")

def test_decide_0930_ambiguous_is_no_trade():
    # positive drive but below drive_thr -> no trade
    assert decide_0930(0.01, None, drive=0.001, thr=THR) == Decision("none", None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_gap_features.py -v`
Expected: FAIL with `ModuleNotFoundError: engine.equity.gap_features`

- [ ] **Step 3: Write the module**

```python
# engine/equity/gap_features.py
"""Pure gap-router decision logic — no pandas, no I/O.

Shared by strategies/gap_router_equity.py, analysis/hdfc_gapandgo.py, and the
unit tests so the classification can never drift (same role sr_core.py plays
for the S/R tools). Two checkpoints: decide_open (09:15) and decide_0930.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Thresholds:
    gap_thr: float = 0.0015     # min |gap| to act
    big_thr: float = 0.004      # "big" gap
    nifty_min: float = 0.001    # NIFTY must actually gap to count as aligned
    drive_thr: float = 0.003    # first-15-min drive to call momentum


@dataclass(frozen=True)
class Decision:
    branch: str                 # "fade" | "go" | "none" | "defer"
    side: Optional[str]         # "long" | "short" | None


def compute_gap(open_: float, prev_close: float) -> float:
    return open_ / prev_close - 1.0


def signed_drive(px: float, open_: float, gap: float) -> float:
    """(px/open - 1) measured in the gap's direction: + = extending the gap,
    - = reverting toward prior close."""
    s = 1.0 if gap > 0 else -1.0
    return (px / open_ - 1.0) * s


def aligned(gap: float, ngap: Optional[float], thr: Thresholds) -> bool:
    if ngap is None:
        return False
    same_dir = (gap > 0) == (ngap > 0)
    return same_dir and abs(ngap) >= thr.nifty_min


def fade_side(gap: float) -> str:
    return "short" if gap > 0 else "long"      # against the gap


def go_side(gap: float) -> str:
    return "long" if gap > 0 else "short"      # with the gap


def decide_open(gap: float, ngap: Optional[float], thr: Thresholds) -> Decision:
    """09:15 checkpoint. FADE small unconfirmed gaps immediately; defer the
    rest to 09:30; NO-TRADE when there's no real gap."""
    if abs(gap) <= thr.gap_thr:
        return Decision("none", None)
    small = abs(gap) <= thr.big_thr
    if small and not aligned(gap, ngap, thr):
        return Decision("fade", fade_side(gap))
    return Decision("defer", None)


def decide_0930(gap: float, ngap: Optional[float], drive: float,
                thr: Thresholds) -> Decision:
    """09:30 checkpoint for deferred gaps: reverting -> fade, driving hard and
    (big or NIFTY-aligned) -> go, otherwise stand aside."""
    if drive <= 0:
        return Decision("fade", fade_side(gap))
    big_or_aligned = abs(gap) > thr.big_thr or aligned(gap, ngap, thr)
    if drive >= thr.drive_thr and big_or_aligned:
        return Decision("go", go_side(gap))
    return Decision("none", None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_gap_features.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: DRY closeout — route hdfc_gapandgo.py through the module**

In `analysis/hdfc_gapandgo.py`, route only the calcs that are byte-for-byte identical to the module through it. Add near the top:

```python
from engine.equity.gap_features import compute_gap, signed_drive
```

Replace `S["gap"] = S["open"] / S["pc"] - 1` with `S["gap"] = S["open"].combine(S["pc"], compute_gap)`, and `S["r15"] = (S["p0930"] / S["open"] - 1) * S["dir"]` with `S["r15"] = S.apply(lambda r: signed_drive(r["p0930"], r["open"], r["gap"]), axis=1)`.

Do NOT change `nifty_same` — it is the analysis's raw sign-match descriptor and is deliberately looser than the strategy's `aligned()` (which adds a `nifty_min` floor). Leave `away15` inline (it measures distance from prior close, a different quantity than `signed_drive`). Keep all printed output identical.

- [ ] **Step 6: Verify the analysis script still runs identically**

Run: `PYTHONPATH=. python3 analysis/hdfc_gapandgo.py`
Expected: same summary numbers as before the refactor (gap days 1311, fill 65.1%, go 34.9%). If any number moved, the refactor changed behavior — revert and reconcile.

- [ ] **Step 7: Commit**

```bash
git add engine/equity/gap_features.py tests/unit/test_gap_features.py analysis/hdfc_gapandgo.py
git commit -m "feat(equity): gap_features — pure gap-router decision logic (DRY)"
```

---

### Task 2: NIFTY gap snapshot store

**Files:**
- Create: `analysis/snapshot_nifty_gap.py`
- Create: `data_index/NIFTY/gap.parquet` (produced by running the script)
- Test: `tests/unit/test_nifty_gap.py` (the `build_gap_table` portion)

**Interfaces:**
- Produces: `build_gap_table(nifty_df: pd.DataFrame) -> pd.DataFrame` with columns `["date","ngap"]` (one row per session, `ngap = day_open / prev_day_close - 1`, first session dropped).
- Source columns consumed: `nifty_df` has `date` (datetime, 5-min bars), `open`, `close`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_nifty_gap.py  (build_gap_table half)
import pandas as pd
from analysis.snapshot_nifty_gap import build_gap_table

def test_build_gap_table_computes_daily_gap():
    df = pd.DataFrame({
        "date": pd.to_datetime([
            "2024-01-01 09:15", "2024-01-01 15:29",   # day1 open 100, close 110
            "2024-01-02 09:15", "2024-01-02 15:29",   # day2 open 121, close 130
        ]),
        "open":  [100.0, 108.0, 121.0, 128.0],
        "close": [101.0, 110.0, 122.0, 130.0],
    })
    out = build_gap_table(df)
    # first day dropped (no prior close); day2 gap = 121/110 - 1
    assert list(out["date"].astype(str)) == ["2024-01-02"]
    assert out["ngap"].iloc[0] == 121.0 / 110.0 - 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_nifty_gap.py::test_build_gap_table_computes_daily_gap -v`
Expected: FAIL with `ModuleNotFoundError: analysis.snapshot_nifty_gap`

- [ ] **Step 3: Write the script**

```python
# analysis/snapshot_nifty_gap.py
"""Snapshot per-date NIFTY gap into data_index/NIFTY/gap.parquet.

Keeps the gap-router backtest offline/reproducible: it reads this in-repo
snapshot, never the sibling tradealgo CSV at backtest time. Re-run to pull
forward when new NIFTY data lands.
"""
from __future__ import annotations

import os

import pandas as pd

NIFTY_CSV = "/Users/gokul/Desktop/tradealgo/nifty_5min_5yr.csv"
STORE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data_index", "NIFTY", "gap.parquet")


def build_gap_table(nifty_df: pd.DataFrame) -> pd.DataFrame:
    """Per-session open/prev-close -> gap. First session dropped (no prior)."""
    df = nifty_df.copy()
    df["day"] = pd.to_datetime(df["date"]).dt.normalize()
    daily = df.groupby("day").agg(nopen=("open", "first"),
                                  nclose=("close", "last"))
    daily["pc"] = daily["nclose"].shift(1)
    daily = daily.dropna(subset=["pc"])
    out = pd.DataFrame({
        "date": daily.index.date,
        "ngap": (daily["nopen"] / daily["pc"] - 1.0).values,
    })
    return out.reset_index(drop=True)


def main() -> None:
    nifty = pd.read_csv(NIFTY_CSV, parse_dates=["date"])
    out = build_gap_table(nifty)
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    out.to_parquet(STORE, index=False)
    print(f"wrote {len(out)} sessions -> {STORE}  "
          f"({out['date'].min()} -> {out['date'].max()})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_nifty_gap.py::test_build_gap_table_computes_daily_gap -v`
Expected: PASS

- [ ] **Step 5: Build the real snapshot**

Run: `PYTHONPATH=. python3 analysis/snapshot_nifty_gap.py`
Expected: `wrote ~1600 sessions -> .../data_index/NIFTY/gap.parquet (2019-... -> 2026-07-10)`

- [ ] **Step 6: Commit**

```bash
git add analysis/snapshot_nifty_gap.py tests/unit/test_nifty_gap.py data_index/NIFTY/gap.parquet
git commit -m "feat(equity): NIFTY per-date gap snapshot + builder"
```

---

### Task 3: NiftyGapProvider

**Files:**
- Create: `engine/equity/nifty_gap.py`
- Test: `tests/unit/test_nifty_gap.py` (append provider tests)

**Interfaces:**
- Consumes: `data_index/NIFTY/gap.parquet` (columns `date`, `ngap`).
- Produces: `NiftyGapProvider(store_path=DEFAULT_STORE)` with `gap_for(d: datetime.date) -> Optional[float]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_nifty_gap.py  (append)
from datetime import date
import pandas as pd
from engine.equity.nifty_gap import NiftyGapProvider

def test_provider_lookup(tmp_path):
    p = tmp_path / "gap.parquet"
    pd.DataFrame({"date": [date(2024, 1, 2)], "ngap": [0.0123]}).to_parquet(p)
    prov = NiftyGapProvider(store_path=str(p))
    assert prov.gap_for(date(2024, 1, 2)) == 0.0123
    assert prov.gap_for(date(2024, 1, 3)) is None   # missing -> None (not aligned)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_nifty_gap.py::test_provider_lookup -v`
Expected: FAIL with `ModuleNotFoundError: engine.equity.nifty_gap`

- [ ] **Step 3: Write the provider**

```python
# engine/equity/nifty_gap.py
"""Per-date NIFTY gap from the in-repo snapshot (data_index/NIFTY/gap.parquet).

Isolates the gap-router's one external input behind a tiny lookup. Missing
dates return None -> the router treats that as 'NIFTY not aligned'.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Optional

import pandas as pd

DEFAULT_STORE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data_index", "NIFTY", "gap.parquet")


class NiftyGapProvider:
    def __init__(self, store_path: str = DEFAULT_STORE):
        df = pd.read_parquet(store_path)
        self._gap = {pd.Timestamp(d).date(): float(g)
                     for d, g in zip(df["date"], df["ngap"])}

    def gap_for(self, d: date) -> Optional[float]:
        return self._gap.get(d)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_nifty_gap.py -v`
Expected: PASS (all tests in file)

- [ ] **Step 5: Commit**

```bash
git add engine/equity/nifty_gap.py tests/unit/test_nifty_gap.py
git commit -m "feat(equity): NiftyGapProvider — in-repo gap lookup"
```

---

### Task 4: GapRouterEquity strategy

**Files:**
- Create: `strategies/gap_router_equity.py`
- Test: `tests/unit/test_gap_router_equity.py`

**Interfaces:**
- Consumes: `gap_features` (Task 1), `NiftyGapProvider`-shaped object with `gap_for(date)` (Task 3), `EquityBarContext`, `EquityOrder`, `Exit`, `Strategy`.
- Produces: `GapRouterEquity(nifty, thresholds=None, capital=100_000.0, or_end=time(9,30), square=time(15,15))` with `on_day_start(ctx)`, `on_bar(ctx) -> list[EquityOrder]`, `manage(ctx, pos) -> Optional[Exit]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_gap_router_equity.py
from datetime import date, datetime, time

from engine.data.spot import SpotSeries, _resample_ohlc
from engine.equity.context import EquityBarContext
from engine.equity.position import EquityPosition
from strategies.gap_router_equity import GapRouterEquity
from tests.equity_helpers import frame, synth_day

D1, D2 = date(2024, 1, 1), date(2024, 1, 2)


class FakeNifty:
    def __init__(self, value=None):
        self.value = value
    def gap_for(self, d):
        return self.value


def series(day2_open, day2_overrides=None):
    # Day 1 flat at 800 -> prev_close 800. Day 2 opens at day2_open.
    d2 = {"09:15": (day2_open, day2_open, day2_open, day2_open)}
    if day2_overrides:
        d2.update(day2_overrides)
    rows = synth_day(D1, 800.0) + synth_day(D2, day2_open, overrides=d2)
    return SpotSeries(_resample_ohlc(frame(rows), "1min", "left"))


def day2_bars(spot):
    return [t for t in spot.timestamps() if t.to_pydatetime().date() == D2]


def run(spot, strat):
    fires = []
    bars = day2_bars(spot)
    strat.on_day_start(EquityBarContext(now=bars[0].to_pydatetime(), spot=spot))
    for b in bars:
        for o in strat.on_bar(EquityBarContext(now=b.to_pydatetime(), spot=spot)):
            fires.append((b.to_pydatetime(), o.direction))
    return fires


def test_small_unaligned_gap_up_fades_short_at_open():
    # open 802 = +0.25% gap, NIFTY missing -> fade short on the first bar
    spot = series(802.0)
    fires = run(spot, GapRouterEquity(FakeNifty(None)))
    assert fires == [(datetime(2024, 1, 2, 9, 15), "short")]


def test_big_aligned_driving_gap_goes_long_at_0930():
    # open 810 (+1.25% big), NIFTY aligned +1%, price drives to 815 by 09:30
    spot = series(810.0, {"09:30": (815.0, 815.0, 815.0, 815.0)})
    fires = run(spot, GapRouterEquity(FakeNifty(0.01)))
    assert fires == [(datetime(2024, 1, 2, 9, 30), "long")]


def test_big_gap_reverting_fades_short_at_0930():
    # open 810 up-gap but reverts to 806 by 09:30 (drive<0) -> fade short
    spot = series(810.0, {"09:30": (806.0, 806.0, 806.0, 806.0)})
    fires = run(spot, GapRouterEquity(FakeNifty(0.01)))
    assert fires == [(datetime(2024, 1, 2, 9, 30), "short")]


def test_ambiguous_defer_no_trade():
    # open 810 big, NIFTY not aligned, weak +drive (811) below drive_thr -> none
    spot = series(810.0, {"09:30": (811.0, 811.0, 811.0, 811.0)})
    assert run(spot, GapRouterEquity(FakeNifty(None))) == []


def test_no_gap_no_trade():
    spot = series(800.4)                       # +0.05% < gap_thr
    assert run(spot, GapRouterEquity(FakeNifty(None))) == []


def test_one_trade_per_day_guard():
    # even if on_bar is called every bar, only the first fade order is emitted
    spot = series(802.0)
    strat = GapRouterEquity(FakeNifty(None))
    assert len(run(spot, strat)) == 1


def test_manage_fade_short_target_is_prior_close():
    spot = series(802.0)
    strat = GapRouterEquity(FakeNifty(None))
    run(spot, strat)                            # fade short at open
    pos = EquityPosition(direction="short", entry=802.0, qty=100)
    # before 09:30: no OR stop, resting target at prior close (802 fade -> 800)
    ex = strat.manage(EquityBarContext(now=datetime(2024, 1, 2, 9, 20), spot=spot), pos)
    assert ex.kind == "level" and ex.trigger == "fall" and ex.level == 800.0


def test_manage_square_off():
    spot = series(802.0)
    strat = GapRouterEquity(FakeNifty(None))
    run(spot, strat)
    pos = EquityPosition(direction="short", entry=802.0, qty=100)
    ex = strat.manage(EquityBarContext(now=datetime(2024, 1, 2, 15, 15), spot=spot), pos)
    assert ex.kind == "market" and ex.reason == "eod"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_gap_router_equity.py -v`
Expected: FAIL with `ModuleNotFoundError: strategies.gap_router_equity`

- [ ] **Step 3: Write the strategy**

```python
# strategies/gap_router_equity.py
"""Two-checkpoint gap router on HDFCBANK cash equity.

09:15: small unconfirmed gaps fade toward prior close immediately; big/aligned
gaps defer. 09:30: deferred gaps that reverted fade; those still driving hard
(and big or NIFTY-aligned) go with the gap. One trade/day, square off at EOD.

Exits: FADE targets prior close (resting level) with a stop when a bar CLOSES
beyond the opening-range extreme (active only once the 09:15-09:30 range is
complete). GO holds to EOD with the same opening-range stop, no fixed target.
"""
from datetime import time

from engine.equity.gap_features import (Thresholds, compute_gap, signed_drive,
                                        decide_open, decide_0930)
from engine.equity.model import EquityOrder
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit


class GapRouterEquity(Strategy):
    def __init__(self, nifty, thresholds=None, capital=100_000.0,
                 or_end=time(9, 30), square=time(15, 15)):
        self.nifty = nifty
        self.thr = thresholds or Thresholds()
        self.CAPITAL = capital
        self.OR_END, self.SQUARE = or_end, square

    def on_day_start(self, ctx):
        self.active = False
        self.traded = False          # one trade/day guard
        self.decided_0930 = False
        self.pending = None          # ("fade"/"go", side) to emit next on_bar
        self.branch = self.side = None
        self.pc = ctx.prev_close()
        if self.pc is None:
            return
        self.open = ctx.spot_bar.open
        self.gap = compute_gap(self.open, self.pc)
        self.ngap = self.nifty.gap_for(ctx.now.date())
        self.active = True
        d = decide_open(self.gap, self.ngap, self.thr)
        if d.branch == "none":
            self.active = False
        elif d.branch == "fade":
            self.pending = ("fade", d.side)      # open fade -> emit at first bar
        # "defer" -> handled at 09:30 in on_bar

    def on_bar(self, ctx):
        if not self.active or self.traded:
            return []
        if self.pending is not None:
            branch, side = self.pending
            self.pending = None
            return self._enter(branch, side)
        if not self.decided_0930 and ctx.now.time() >= self.OR_END:
            self.decided_0930 = True
            drive = signed_drive(ctx.spot_bar.close, self.open, self.gap)
            d = decide_0930(self.gap, self.ngap, drive, self.thr)
            if d.branch == "none":
                self.active = False
                return []
            return self._enter(d.branch, d.side)
        return []

    def _enter(self, branch, side):
        self.traded = True
        self.branch, self.side = branch, side
        return [EquityOrder(direction=side, capital=self.CAPITAL)]

    def manage(self, ctx, pos):
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        # opening-range stop is live only once the 09:15-OR_END range is complete
        orng = ctx.opening_range(self.OR_END) if ctx.now.time() >= self.OR_END else None
        close = ctx.spot_bar.close
        if orng is not None:
            or_high, or_low = orng
            if pos.direction == "long" and close < or_low:
                return Exit.market("or_stop")
            if pos.direction == "short" and close > or_high:
                return Exit.market("or_stop")
        if self.branch == "fade":
            # target = prior close (the gap fill)
            if pos.direction == "long":
                return Exit.at(self.pc, "rise", "fill")
            return Exit.at(self.pc, "fall", "fill")
        return None                              # GO: hold to EOD / stop only
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_gap_router_equity.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add strategies/gap_router_equity.py tests/unit/test_gap_router_equity.py
git commit -m "feat(equity): GapRouterEquity — two-checkpoint gap fade/go router"
```

---

### Task 5: OOS backtest runner

**Files:**
- Create: `analysis/gap_router_backtest.py`

**Interfaces:**
- Consumes: `load_cached("HDFCBANK")`, `EquityBacktest`, `EquityKnobs`, `EquityCostModel`, `NiftyGapProvider`, `GapRouterEquity`, `Thresholds`, and `Result` metrics (`net()`, `profit_factor()`, `win_rate()`, `by_year()`, `tail_excl(n)`).

- [ ] **Step 1: Write the runner**

```python
# analysis/gap_router_backtest.py
"""HDFCBANK Gap Router backtest — OOS train/test, net of Zerodha MIS costs.

Thresholds are swept on the TRAIN split (2020-2023) then FROZEN and applied to
the TEST split (2024-2026) so the reported test edge is honest out-of-sample.
Runs on 1-min bars (thin fade edge -> minimal entry lag). Reads only in-repo
cached data (HDFCBANK parquet + data_index/NIFTY snapshot).
"""
from datetime import date

from engine.equity.backtest import EquityBacktest, EquityKnobs
from engine.equity.costs import EquityCostModel
from engine.equity.gap_features import Thresholds
from engine.equity.kite_loader import load_cached
from engine.equity.nifty_gap import NiftyGapProvider
from strategies.gap_router_equity import GapRouterEquity

TRAIN_END = date(2023, 12, 31)
TEST_START = date(2024, 1, 1)


def report(tag, r):
    print(f"  {tag:<14} trades={len(r.trades):4d}  net={r.net():>12,.0f}  "
          f"PF={r.profit_factor():5.2f}  win={r.win_rate():5.1%}  "
          f"tail-5={r.tail_excl(5):>12,.0f}")


def main():
    df = load_cached("HDFCBANK")
    nifty = NiftyGapProvider()
    bt = EquityBacktest(df, EquityCostModel(), EquityKnobs(timeframe="1min"))
    print(f"bars={len(df):,}  {df['DateTime'].min().date()} -> "
          f"{df['DateTime'].max().date()}\n")

    # --- sweep drive_thr / big_thr on TRAIN, pick best net ---
    best, best_net = None, float("-inf")
    for big in (0.003, 0.004, 0.005):
        for drive in (0.002, 0.003, 0.004):
            thr = Thresholds(big_thr=big, drive_thr=drive)
            r = bt.run(GapRouterEquity(nifty, thresholds=thr), end=TRAIN_END)
            if r.net() > best_net:
                best, best_net = thr, r.net()
    print(f"best train thresholds: big_thr={best.big_thr} drive_thr={best.drive_thr}\n")

    print("TRAIN (2020-2023, fitted):")
    report("all", bt.run(GapRouterEquity(nifty, thresholds=best), end=TRAIN_END))
    print("\nTEST (2024-2026, frozen OOS):")
    r_test = bt.run(GapRouterEquity(nifty, thresholds=best), start=TEST_START)
    report("all", r_test)
    print(f"\n  test by_year: "
          f"{{{', '.join(f'{y}: {v:,.0f}' for y, v in sorted(r_test.by_year().items()))}}}")
    print(f"\nskipped days: {len(bt.skipped_days)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the backtest end-to-end**

Run: `PYTHONPATH=. python3 analysis/gap_router_backtest.py`
Expected: prints train fitted metrics + frozen OOS test metrics + by-year. Verify it runs with no error and the TEST split shows a positive `net` after costs. If TEST net is negative or PF < 1.0, the edge does not survive costs OOS — record that as the finding (the strategy is not shippable as-is) rather than tuning on the test split.

- [ ] **Step 3: Commit**

```bash
git add analysis/gap_router_backtest.py
git commit -m "feat(equity): Gap Router OOS backtest runner (train-fit, frozen test)"
```

---

## Verification (whole plan)

- [x] Run the full unit suite: `pytest tests/unit/test_gap_features.py tests/unit/test_nifty_gap.py tests/unit/test_gap_router_equity.py -v` — all green (20 passed; full suite 179 passed).
- [x] Confirm no `engine/` files changed: `git diff --name-only main...HEAD | grep '^engine/' ` shows only the two NEW files (`gap_features.py`, `nifty_gap.py`), no modifications to existing engine files.
- [x] Record the OOS test result (net, PF, win, by-year) — that number is the go/no-go on whether the router is worth trading. **See Results below.**

## Results (2026-07-13) — VERDICT: NO-GO

Backtest executed per Task 5 (1-min bars, `EquityCostModel` + 0.05%/side slippage,
₹100k/trade). Thresholds swept on TRAIN only (best: `big_thr=0.005`,
`drive_thr=0.004`), frozen for TEST. Reproduce: `PYTHONPATH=. python3
analysis/gap_router_backtest.py` (independently re-run at review, byte-identical).

| Split | Trades | Net | PF | Win % | tail-5 |
|---|---|---|---|---|---|
| TRAIN 2020–2023 (fitted) | 652 | **−₹119,026** | 0.49 | 33.0% | −₹135,097 |
| TEST 2024–2026 (frozen OOS) | 437 | **−₹64,420** | 0.43 | 30.2% | −₹71,917 |

Test by-year: 2024 −26,541 / 2025 −30,407 / 2026 −7,473. No combination in the
3×3 train grid was profitable — the sweep selected the least-negative.

**Diagnosis (unverified hypothesis):** the signal analysis (56% directional win,
70% gap-fill) measured hold-to-fill/EOD with **no stop**; the strategy's
opening-range stop drops the win rate to 33% — consistent with the stop exiting
mean-reversion trades at max adversity before the fill completes. Note the 09:30
bar is included in BOTH the OR range and the 09:30 decision (`<=` boundary in
`context.opening_range`, mirrored by the strategy's `_track_or`).

**Follow-up (train split only; TEST stays frozen):** ablation on the exit
structure — no stop / wider stop / per-branch (fade vs go) attribution in the
runner. Also carried: test for `decide_0930`'s aligned-disjunct; wall-clock
`OR_END=09:30` diverges from spec §6 on late-start sessions (~6 Muhurat sessions
in 6 yrs — immaterial, fold into ablation); engine-level `SpotSeries.history()`
O(N) `iterrows()` scan (out of scope here; strategy caches OR incrementally
instead — see `_track_or` comment, commit 2ed98fa; plan's Task 4 listing shows
the pre-fix `ctx.opening_range()` version).

**What the branch delivers:** reusable `gap_features` (pure router core),
`NiftyGapProvider` + offline snapshot pattern, an honest train/frozen-test
harness, 20 tests — and a recorded negative result: this exit structure does
not trade the gap edge profitably net of costs.
