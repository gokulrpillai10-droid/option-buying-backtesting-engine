# HDFCBANK Cash-Equity Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an intraday cash-equity backtest capability (`engine/equity/`) and run the raw CPR-cross signal on HDFCBANK, long and short, per the approved spec `docs/superpowers/specs/2026-07-03-equity-backtest-hdfcbank-design.md`.

**Architecture:** New `engine/equity/` subpackage that never imports the options core (`Backtest`, `Position`, `Chain`, `CostModel`). It reuses only the instrument-agnostic primitives unmodified: `SpotSeries` (+ its module helper `_resample_ohlc`), `Trade`, `Result`, `Exit`/`resolve_level_exit`, and the `Strategy` base class. Data comes from Kite's historical API, cached to local per-month parquets.

**Tech Stack:** Python 3.9+, pandas ≥2, pyarrow, pytest; `kiteconnect` + `python-dotenv` only inside the (lazily-imported, non-unit-tested) real-client path.

## Global Constraints

- Project root is `/Users/gokul/Desktop/buildstrategies ` — **trailing space in the name; always quote it.**
- Run tests as: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest <path> -v`
- **Never modify any existing file** except `pyproject.toml` (Task 4 adds an optional-dependency group) — the options engine is walk-forward validated and must stay byte-identical. All other work is new files.
- The full existing suite (115 tests) must stay green after every task: `PYTHONPATH="$PWD" python3 -m pytest -q`
- Reused-as-is imports (pure data/math, allowed): `from engine.data.spot import SpotSeries, _resample_ohlc` · `from engine.position.manager import Trade` · `from engine.backtest import Result` · `from engine.strategy.base import Strategy` · `from engine.strategy.exit import Exit, resolve_level_exit`.
- Rate card, knob defaults, and rules come verbatim from the spec: brokerage `min(₹20, 0.03%)` per executed order, STT 0.025% sell-side, NSE exchange 0.00297%, SEBI ₹10/crore, stamp 0.003% buy-side, GST 18% on (brokerage+exchange+SEBI); slippage 0.05%; `fixed_capital` ₹100,000; min-bars/day 300; overnight-gap skip >15%; timeframe 5min.
- TDD: write the failing test, run it, watch it fail, implement, watch it pass, commit.
- Match existing style: module docstrings explaining *why*, terse inline comments only for constraints.

---

### Task 1: Git init + package scaffold + `EquityCostModel`

**Files:**
- Create: `.gitignore`
- Create: `engine/equity/__init__.py`
- Create: `engine/equity/costs.py`
- Test: `tests/unit/test_equity_costs.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `EquityCostModel(brokerage_pct=0.0003, brokerage_cap=20.0, stt_sell=0.00025, exchange=0.0000297, sebi=0.000001, stamp_buy=0.00003, gst=0.18)` frozen dataclass with `total(buy_value: float, sell_value: float) -> float` (note: NO `n_orders` param — equity brokerage is value-dependent per order, computed internally from the buy and sell legs).

- [ ] **Step 1: Initialize git and commit the existing project**

The project is not yet a git repo; the execution workflow needs commits.

Create `.gitignore`:

```gitignore
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
data_equity/
.DS_Store
```

```bash
cd "/Users/gokul/Desktop/buildstrategies " && git init && git add -A && git commit -m "chore: baseline — NIFTY options engine before equity subpackage"
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_equity_costs.py`:

```python
"""EquityCostModel vs a hand-calculated Zerodha MIS-equity round trip."""
import pytest

from engine.equity.costs import EquityCostModel


def test_full_round_trip_hand_calc():
    # Buy 57 sh @ 1750 = 99,750 ; sell 57 sh @ 1760 = 100,320
    m = EquityCostModel()
    buy, sell = 99_750.0, 100_320.0
    brokerage = min(20.0, 0.0003 * buy) + min(20.0, 0.0003 * sell)   # 20 + 20 = 40
    stt = 0.00025 * sell                                             # 25.08
    exch = 0.0000297 * (buy + sell)                                  # 5.9420790
    sebi = 0.000001 * (buy + sell)                                   # 0.20007
    stamp = 0.00003 * buy                                            # 2.9925
    gst = 0.18 * (brokerage + exch + sebi)                           # 8.30558682
    expected = brokerage + stt + exch + sebi + stamp + gst           # 82.52023582
    assert m.total(buy, sell) == pytest.approx(expected)
    assert m.total(buy, sell) == pytest.approx(82.52023582)


def test_small_trade_brokerage_below_cap():
    # 1 share: 0.03% of value < Rs20, so percentage brokerage applies per order.
    m = EquityCostModel()
    buy, sell = 1000.0, 1010.0
    brokerage = 0.0003 * buy + 0.0003 * sell                         # 0.603
    stt = 0.00025 * sell
    exch = 0.0000297 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.00003 * buy
    gst = 0.18 * (brokerage + exch + sebi)
    assert m.total(buy, sell) == pytest.approx(
        brokerage + stt + exch + sebi + stamp + gst)
    assert m.total(buy, sell) == pytest.approx(1.06685426)


def test_stt_is_sell_side_only():
    m = EquityCostModel()
    # Same turnover split differently: more sell value => strictly more STT.
    assert m.total(50_000.0, 150_000.0) > m.total(150_000.0, 50_000.0)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/unit/test_equity_costs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.equity'`

- [ ] **Step 4: Write minimal implementation**

Create `engine/equity/__init__.py` (empty file).

Create `engine/equity/costs.py`:

```python
"""India cash-equity intraday (MIS) cost model — Zerodha rate card.

Independent of the F&O CostModel: completely different charges (percentage
brokerage with a cap, sell-side-only STT at 0.025%, NSE cash exchange rate),
no shared logic beyond the .total(...) shape. Slippage is NOT here — the
EquityBacktest applies it adversely to fill prices, same convention as the
options engine.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EquityCostModel:
    brokerage_pct: float = 0.0003    # 0.03% of order value...
    brokerage_cap: float = 20.0      # ...capped at Rs20 per executed order
    stt_sell: float = 0.00025        # STT 0.025%, SELL side only (intraday equity)
    exchange: float = 0.0000297      # NSE cash txn charge, both sides
    sebi: float = 0.000001           # SEBI turnover fee, both sides
    stamp_buy: float = 0.00003       # stamp duty 0.003%, buy side only
    gst: float = 0.18                # GST on (brokerage + exchange + sebi)

    def total(self, buy_value: float, sell_value: float) -> float:
        """Round-trip cost for one buy order moving ``buy_value`` and one sell
        order moving ``sell_value`` (both in rupees)."""
        brokerage = (min(self.brokerage_cap, self.brokerage_pct * buy_value)
                     + min(self.brokerage_cap, self.brokerage_pct * sell_value))
        stt = self.stt_sell * sell_value
        exch = self.exchange * (buy_value + sell_value)
        sebi = self.sebi * (buy_value + sell_value)
        stamp = self.stamp_buy * buy_value
        gst = self.gst * (brokerage + exch + sebi)
        return brokerage + stt + exch + sebi + stamp + gst
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/unit/test_equity_costs.py -v`
Expected: 3 PASSED

- [ ] **Step 6: Run the full suite (regression gate)**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest -q`
Expected: 118 passed (115 existing + 3 new)

- [ ] **Step 7: Commit**

```bash
cd "/Users/gokul/Desktop/buildstrategies " && git add engine/equity tests/unit/test_equity_costs.py && git commit -m "feat(equity): EquityCostModel — Zerodha MIS cash rate card"
```

---

### Task 2: `EquityOrder` + `EquityPosition`

**Files:**
- Create: `engine/equity/model.py`
- Create: `engine/equity/position.py`
- Test: `tests/unit/test_equity_position.py`

**Interfaces:**
- Consumes: `EquityCostModel.total(buy_value, sell_value)` (Task 1); `Trade` from `engine.position.manager` (reused frozen dataclass; equity fills `side="LONG"|"SHORT"`, `strike=0`).
- Produces: `EquityOrder(direction: str, capital: float)` — a strategy's intent, sized in rupees; `EquityPosition(direction: str, entry: float, qty: int)` with `close(exit: float, cost_model: EquityCostModel, reason: str = "") -> Trade`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_equity_position.py`:

```python
"""EquityPosition.close in all four quadrants (long/short x profit/loss)."""
import pytest

from engine.equity.costs import EquityCostModel
from engine.equity.model import EquityOrder
from engine.equity.position import EquityPosition

M = EquityCostModel()


def test_order_carries_direction_and_capital():
    o = EquityOrder(direction="short", capital=100_000.0)
    assert o.direction == "short" and o.capital == 100_000.0


def test_long_profit():
    p = EquityPosition(direction="long", entry=1750.0, qty=57)
    t = p.close(1760.0, M, reason="target")
    assert t.side == "LONG" and t.strike == 0 and t.qty == 57
    assert t.entry == 1750.0 and t.exit == 1760.0
    assert t.gross == pytest.approx((1760.0 - 1750.0) * 57)          # +570
    expected_cost = M.total(buy_value=1750.0 * 57, sell_value=1760.0 * 57)
    assert t.cost == pytest.approx(expected_cost)
    assert t.net == pytest.approx(t.gross - t.cost)
    assert t.reason == "target"


def test_long_loss():
    p = EquityPosition(direction="long", entry=1750.0, qty=57)
    t = p.close(1740.0, M)
    assert t.gross == pytest.approx(-570.0)


def test_short_profit():
    # Short: sell at entry, buy back at exit; buy_value uses the EXIT leg.
    p = EquityPosition(direction="short", entry=1750.0, qty=57)
    t = p.close(1740.0, M, reason="target")
    assert t.side == "SHORT"
    assert t.gross == pytest.approx((1750.0 - 1740.0) * 57)          # +570
    expected_cost = M.total(buy_value=1740.0 * 57, sell_value=1750.0 * 57)
    assert t.cost == pytest.approx(expected_cost)
    assert t.net == pytest.approx(t.gross - t.cost)


def test_short_loss():
    p = EquityPosition(direction="short", entry=1750.0, qty=57)
    t = p.close(1760.0, M)
    assert t.gross == pytest.approx(-570.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/unit/test_equity_position.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.equity.model'`

- [ ] **Step 3: Write minimal implementation**

Create `engine/equity/model.py`:

```python
"""A strategy's intent to trade shares — sized in rupees, not units.

Mirrors the options Order but for cash equity: ``direction`` replaces
side/strike, and size is fixed CAPITAL per trade (the engine converts to a
share qty at the fill price, so capital-at-risk stays constant as the stock's
price level drifts over a multi-year backtest).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EquityOrder:
    direction: str      # "long" (buy shares) or "short" (intraday short-sell)
    capital: float      # rupees to deploy; qty = floor(capital / fill_price)
```

Create `engine/equity/position.py`:

```python
"""Open equity positions and the costed Trades they close into.

Reuses the engine's frozen ``Trade`` dataclass unmodified (side carries
"LONG"/"SHORT", strike is 0 — no derivative layer), so all existing Result
metrics and tear-sheet code work unchanged on equity results.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.equity.costs import EquityCostModel
from engine.position.manager import Trade


@dataclass(frozen=True)
class EquityPosition:
    direction: str      # "long" or "short"
    entry: float        # fill price (already slipped by the driver)
    qty: int

    def close(self, exit: float, cost_model: EquityCostModel,
              reason: str = "") -> Trade:
        """Close the full position at ``exit`` -> a fully-costed Trade.
        Long: buy at entry, sell at exit. Short: sell at entry, buy at exit."""
        if self.direction == "long":
            gross = (exit - self.entry) * self.qty
            buy_value, sell_value = self.entry * self.qty, exit * self.qty
        else:
            gross = (self.entry - exit) * self.qty
            buy_value, sell_value = exit * self.qty, self.entry * self.qty
        cost = cost_model.total(buy_value=buy_value, sell_value=sell_value)
        return Trade(side=self.direction.upper(), strike=0, qty=self.qty,
                     entry=self.entry, exit=exit,
                     gross=gross, cost=cost, net=gross - cost, reason=reason)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/unit/test_equity_position.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Full suite + commit**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest -q` — expected 123 passed.

```bash
cd "/Users/gokul/Desktop/buildstrategies " && git add engine/equity tests/unit/test_equity_position.py && git commit -m "feat(equity): EquityOrder + EquityPosition (direction-aware P&L into reused Trade)"
```

---

### Task 3: `EquityBarContext` + shared synthetic-spot test helpers

**Files:**
- Create: `engine/equity/context.py`
- Create: `tests/equity_helpers.py`
- Test: `tests/unit/test_equity_context.py`

**Interfaces:**
- Consumes: `SpotSeries` (reused as-is: `price_at`, `bar_at`, `history`, `window`, `close_before`, `prev_day_hlc`).
- Produces: `EquityBarContext(now: datetime, spot: SpotSeries)` with `.now`, `.spot`, `.spot_bar`, `.spot_history()`, `.recent(n)`, `.prev_close()`, `.prev_day_hlc()`, `.opening_range(end_time) -> (high, low) | None`. Also test helpers `bar(dt, o, h=None, l=None, c=None) -> dict`, `synth_day(day, base, overrides=None, start="09:15", minutes=375) -> list[dict]`, `frame(rows) -> pd.DataFrame` in `tests/equity_helpers.py`.

- [ ] **Step 1: Write the test helpers**

Create `tests/equity_helpers.py`:

```python
"""Synthetic 1-min equity frames for the equity tests.

``synth_day`` builds a full flat NSE session (09:15..15:29 = 375 minutes) at a
base price with per-minute OHLC overrides — small, deterministic, and enough
to exercise resampling, intrabar exits, and day-gate rules.
"""
from datetime import datetime, timedelta

import pandas as pd


def bar(dt, o, h=None, l=None, c=None):
    return {"DateTime": pd.Timestamp(dt), "Open": float(o),
            "High": float(h if h is not None else o),
            "Low": float(l if l is not None else o),
            "Close": float(c if c is not None else o)}


def synth_day(day, base, overrides=None, start="09:15", minutes=375):
    """A full session at ``base``; ``overrides`` = {"HH:MM": (o, h, l, c)}."""
    h0, m0 = (int(x) for x in start.split(":"))
    t0 = datetime(day.year, day.month, day.day, h0, m0)
    ov = overrides or {}
    rows = []
    for i in range(minutes):
        dt = t0 + timedelta(minutes=i)
        key = dt.strftime("%H:%M")
        if key in ov:
            o, h, l, c = ov[key]
            rows.append(bar(dt, o, h, l, c))
        else:
            rows.append(bar(dt, base))
    return rows


def frame(rows):
    return pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"])
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_equity_context.py`:

```python
"""EquityBarContext: spot-forwarding accessors + look-ahead safety."""
from datetime import date, datetime, time

import pytest

from engine.data.spot import SpotSeries, _resample_ohlc
from engine.equity.context import EquityBarContext
from tests.equity_helpers import frame, synth_day


@pytest.fixture
def spot_5m():
    """Two sessions, 5-min bars. Day 1 carries a known H/L/C for CPR-style
    prev-day reads; day 2 is flat 1700 with one marked bar."""
    rows = (synth_day(date(2024, 1, 1), 1700.0,
                      overrides={"10:00": (1700, 1720, 1700, 1715),   # day HIGH 1720
                                 "11:00": (1700, 1700, 1690, 1695),   # day LOW 1690
                                 "15:29": (1710, 1710, 1710, 1710)})  # day CLOSE 1710
            + synth_day(date(2024, 1, 2), 1700.0,
                        overrides={"09:17": (1700, 1712, 1698, 1705)}))
    return SpotSeries(_resample_ohlc(frame(rows), "5min", "left"))


def test_now_spot_and_bar(spot_5m):
    now = datetime(2024, 1, 2, 9, 15)
    ctx = EquityBarContext(now=now, spot=spot_5m)
    assert ctx.now == now
    b = ctx.spot_bar                     # 5-min bar 09:15 contains the 09:17 override
    assert b.dt == now
    assert b.high == 1712.0 and b.low == 1698.0
    assert ctx.spot == b.close


def test_prev_day_hlc_and_prev_close(spot_5m):
    ctx = EquityBarContext(now=datetime(2024, 1, 2, 9, 15), spot=spot_5m)
    assert ctx.prev_day_hlc() == (1720.0, 1690.0, 1710.0)
    assert ctx.prev_close() == 1710.0


def test_recent_and_history_no_lookahead(spot_5m):
    now = datetime(2024, 1, 2, 9, 25)
    ctx = EquityBarContext(now=now, spot=spot_5m)
    r = ctx.recent(3)
    assert len(r) == 3 and r[-1].dt == now
    assert all(b.dt <= now for b in ctx.spot_history())


def test_opening_range(spot_5m):
    ctx = EquityBarContext(now=datetime(2024, 1, 2, 10, 0), spot=spot_5m)
    hi, lo = ctx.opening_range(time(9, 30))
    assert hi == 1712.0 and lo == 1698.0          # from the 09:15 bar only

def test_opening_range_never_past_now(spot_5m):
    # now is 09:20 but the requested window extends to 10:00 -> clamps at now
    ctx = EquityBarContext(now=datetime(2024, 1, 2, 9, 20), spot=spot_5m)
    hi, lo = ctx.opening_range(time(10, 0))
    assert hi == 1712.0 and lo == 1698.0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/unit/test_equity_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.equity.context'`

- [ ] **Step 4: Write minimal implementation**

Create `engine/equity/context.py`:

```python
"""The ONLY interface an equity strategy sees each bar.

Deliberately duplicates ~6 one-line spot-forwarding methods from the options
BarContext instead of making its ``chain`` parameter optional — zero shared
mutable surface with the proven options context (a micro-decision from the
2026-07-03 spec: strictly the zero-risk option). Since cash equity has no
derivative layer, ONE SpotSeries serves as both signal feed and fill feed.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Optional, Tuple


class EquityBarContext:
    def __init__(self, now: datetime, spot):
        self._now = now
        self._spot = spot

    @property
    def now(self) -> datetime:
        return self._now

    @property
    def spot(self) -> float:
        """Current price (close of the as-of-now bar)."""
        return self._spot.price_at(self._now)

    @property
    def spot_bar(self):
        """Current OHLC bar."""
        return self._spot.bar_at(self._now)

    def spot_history(self):
        """All bars up to and including now (no future)."""
        return self._spot.history(self._now)

    def recent(self, n: int):
        """The up-to-``n`` most recent bars ending at now (rolling indicators)."""
        return self._spot.window(self._now, n)

    def prev_close(self) -> Optional[float]:
        """Prior session's closing price (gap gates)."""
        return self._spot.close_before(self._now)

    def prev_day_hlc(self):
        """Prior session's (high, low, close) — the CPR inputs."""
        return self._spot.prev_day_hlc(self._now)

    def opening_range(self, end_time: time) -> Optional[Tuple[float, float]]:
        """(high, low) of today's bars from open up to ``end_time`` (and never
        past ``now``). None before the first bar of the day."""
        today = [b for b in self._spot.history(self._now)
                 if b.dt.date() == self._now.date() and b.dt.time() <= end_time]
        if not today:
            return None
        return max(b.high for b in today), min(b.low for b in today)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/unit/test_equity_context.py -v`
Expected: 5 PASSED

- [ ] **Step 6: Full suite + commit**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest -q` — expected 128 passed.

```bash
cd "/Users/gokul/Desktop/buildstrategies " && git add engine/equity/context.py tests/equity_helpers.py tests/unit/test_equity_context.py && git commit -m "feat(equity): EquityBarContext — spot-only context, zero shared surface with options"
```

---

### Task 4: Kite loader (fetch / cache / resume / clean)

**Files:**
- Create: `engine/equity/kite_loader.py`
- Modify: `pyproject.toml` (add optional-dependency group only — nothing else)
- Test: `tests/unit/test_kite_loader.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `INSTRUMENT_TOKENS = {"HDFCBANK": 341249}` (NSE instrument token)
  - `month_range(start: date, end: date) -> list[tuple[date, date]]`
  - `clean(df) -> tuple[pd.DataFrame, int]` (clean frame, n dropped)
  - `fetch_months(fetch_fn, symbol, token, start, end, cache_dir=DEFAULT_CACHE, throttle=0.35, max_retries=5, sleep=time.sleep, today=None) -> tuple[int, int]` (written, skipped); `fetch_fn(token, from_dt, to_dt, interval) -> list[dict]` is `kite.historical_data`-compatible
  - `load_cached(symbol, cache_dir=DEFAULT_CACHE) -> pd.DataFrame` (columns `DateTime, Open, High, Low, Close`, 1-min, clean — ready for `SpotSeries`)
  - `make_kite()` — real client from `/Users/gokul/Desktop/tradealgo/.env` (lazy imports; not unit-tested)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_kite_loader.py`:

```python
"""Kite loader: month chunking, resumability, retry/backoff, integrity clean.
All tests use a fake fetch_fn + injected sleep — no network, no kiteconnect."""
from datetime import date, datetime

import pandas as pd
import pytest

from engine.equity.kite_loader import (clean, fetch_months, load_cached,
                                       month_range)


def recs(day, n=2, base=1700.0):
    """Kite-style records: tz-aware 'date' + lowercase ohlc."""
    return [{"date": pd.Timestamp(datetime(day.year, day.month, day.day, 9, 15 + i),
                                  tz="Asia/Kolkata"),
             "open": base, "high": base + 1, "low": base - 1, "close": base,
             "volume": 1000}
            for i in range(n)]


def test_month_range_clips_to_bounds():
    assert month_range(date(2024, 1, 15), date(2024, 3, 10)) == [
        (date(2024, 1, 15), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 10)),
    ]


def test_fetch_writes_month_files_and_throttles(tmp_path):
    calls, sleeps = [], []
    def fn(token, frm, to, interval):
        calls.append((token, frm.date(), to.date(), interval))
        return recs(frm.date())
    written, skipped = fetch_months(fn, "HDFCBANK", 341249,
                                    date(2024, 1, 1), date(2024, 2, 29),
                                    cache_dir=str(tmp_path),
                                    sleep=sleeps.append, today=date(2024, 6, 1))
    assert written == 2 and skipped == 0
    assert (tmp_path / "HDFCBANK" / "2024-01.parquet").exists()
    assert (tmp_path / "HDFCBANK" / "2024-02.parquet").exists()
    assert all(c[3] == "minute" for c in calls)
    assert sleeps.count(0.35) == 2                      # throttle after each fetch


def test_resume_skips_complete_cached_months(tmp_path):
    def fn(token, frm, to, interval):
        return recs(frm.date())
    fetch_months(fn, "HDFCBANK", 341249, date(2024, 1, 1), date(2024, 1, 31),
                 cache_dir=str(tmp_path), sleep=lambda s: None,
                 today=date(2024, 6, 1))
    calls = []
    def fn2(token, frm, to, interval):
        calls.append(frm.date())
        return recs(frm.date())
    written, skipped = fetch_months(fn2, "HDFCBANK", 341249,
                                    date(2024, 1, 1), date(2024, 2, 29),
                                    cache_dir=str(tmp_path),
                                    sleep=lambda s: None, today=date(2024, 6, 1))
    assert written == 1 and skipped == 1
    assert calls == [date(2024, 2, 1)]                  # January not refetched


def test_current_month_is_always_refetched(tmp_path):
    def fn(token, frm, to, interval):
        return recs(frm.date())
    # Cache June as if fetched mid-month, then fetch again "today" = June 20.
    fetch_months(fn, "HDFCBANK", 341249, date(2024, 6, 1), date(2024, 6, 15),
                 cache_dir=str(tmp_path), sleep=lambda s: None,
                 today=date(2024, 6, 20))
    written, skipped = fetch_months(fn, "HDFCBANK", 341249,
                                    date(2024, 6, 1), date(2024, 6, 20),
                                    cache_dir=str(tmp_path),
                                    sleep=lambda s: None, today=date(2024, 6, 20))
    assert written == 1 and skipped == 0                # overwritten, not skipped


def test_retry_backoff_then_success(tmp_path):
    sleeps, attempts = [], {"n": 0}
    def flaky(token, frm, to, interval):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("kite 429")
        return recs(frm.date())
    written, _ = fetch_months(flaky, "HDFCBANK", 341249,
                              date(2024, 1, 1), date(2024, 1, 31),
                              cache_dir=str(tmp_path), sleep=sleeps.append,
                              today=date(2024, 6, 1))
    assert written == 1 and attempts["n"] == 3
    assert sleeps[:2] == [1, 2]                          # 2**0, 2**1 backoff


def test_retry_exhausted_raises(tmp_path):
    def dead(token, frm, to, interval):
        raise ConnectionError("kite down")
    with pytest.raises(ConnectionError):
        fetch_months(dead, "HDFCBANK", 341249,
                     date(2024, 1, 1), date(2024, 1, 31),
                     cache_dir=str(tmp_path), sleep=lambda s: None,
                     max_retries=3, today=date(2024, 6, 1))


def test_clean_drops_bad_bars_and_dedupes():
    t = pd.Timestamp("2024-01-01 09:15")
    df = pd.DataFrame([
        {"DateTime": t, "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5},
        {"DateTime": t, "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5},  # dup
        {"DateTime": t + pd.Timedelta(minutes=1),
         "Open": 100.0, "High": 99.5, "Low": 99.0, "Close": 100.0},   # High < Open
        {"DateTime": t + pd.Timedelta(minutes=2),
         "Open": 0.0, "High": 1.0, "Low": 0.0, "Close": 1.0},         # zero price
        {"DateTime": t + pd.Timedelta(minutes=3),
         "Open": 100.0, "High": 101.0, "Low": 100.5, "Close": 100.6}, # Low > min(O,C)
    ])
    out, dropped = clean(df)
    assert len(out) == 1 and dropped == 3
    assert out.iloc[0]["DateTime"] == t


def test_load_cached_concats_sorted(tmp_path):
    def fn(token, frm, to, interval):
        return recs(frm.date())
    fetch_months(fn, "HDFCBANK", 341249, date(2024, 1, 1), date(2024, 2, 29),
                 cache_dir=str(tmp_path), sleep=lambda s: None,
                 today=date(2024, 6, 1))
    df = load_cached("HDFCBANK", cache_dir=str(tmp_path))
    assert list(df.columns) == ["DateTime", "Open", "High", "Low", "Close"]
    assert df["DateTime"].is_monotonic_increasing
    assert df["DateTime"].dt.tz is None                  # naive IST
    assert len(df) == 4                                  # 2 bars x 2 months
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/unit/test_kite_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.equity.kite_loader'`

- [ ] **Step 3: Write minimal implementation**

Create `engine/equity/kite_loader.py`:

```python
"""HDFCBANK 1-min OHLC via the Kite historical API — chunked, cached, resumable.

Kite caps minute candles at ~60 days/request and rate-limits (~3 req/s), so we
fetch per CALENDAR MONTH (<=31 days), throttle between requests, retry with
exponential backoff, and write each month to its own parquet the moment it
lands — a crash resumes by skipping complete cached months. The CURRENT month
is always refetched (it may have gained days since the last run). The backtest
never touches Kite: it reads only ``load_cached`` output (deterministic,
offline-reproducible).

Credentials live in the tradealgo .env (refresh the daily token there first:
``cd /Users/gokul/Desktop/tradealgo && python3 zerodha_auth.py``). kiteconnect
and dotenv are imported lazily inside ``make_kite`` so the test suite and the
backtest need neither installed.
"""
from __future__ import annotations

import calendar
import glob
import os
import time as _time
from datetime import date, datetime, timedelta
from typing import Callable, List, Optional, Tuple

import pandas as pd

TRADEALGO_ENV = "/Users/gokul/Desktop/tradealgo/.env"
INSTRUMENT_TOKENS = {"HDFCBANK": 341249}          # NSE instrument tokens
DEFAULT_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data_equity")


def month_range(start: date, end: date) -> List[Tuple[date, date]]:
    """Calendar-month windows covering [start, end], clipped to the bounds."""
    out = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        out.append((max(cur, start), min(last, end)))
        cur = last + timedelta(days=1)
    return out


def _normalize(records) -> pd.DataFrame:
    """Kite records (tz-aware 'date', lowercase ohlc) -> naive-IST OHLC frame."""
    if not records:
        return pd.DataFrame(columns=["DateTime", "Open", "High", "Low", "Close"])
    df = pd.DataFrame(records)
    dt = pd.to_datetime(df["date"])
    if getattr(dt.dt, "tz", None) is not None:
        dt = dt.dt.tz_localize(None)                 # drop tz, keep IST wall-clock
    return pd.DataFrame({"DateTime": dt,
                         "Open": df["open"].astype(float),
                         "High": df["high"].astype(float),
                         "Low": df["low"].astype(float),
                         "Close": df["close"].astype(float)})


def clean(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Integrity pass: dedupe/sort by DateTime, drop insane bars (non-positive
    prices, High < max(O,C), Low > min(O,C)). Returns (clean, n_dropped)."""
    df = df.drop_duplicates("DateTime").sort_values("DateTime").reset_index(drop=True)
    if df.empty:
        return df, 0
    ok = ((df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)
          & (df["High"] >= df[["Open", "Close"]].max(axis=1))
          & (df["Low"] <= df[["Open", "Close"]].min(axis=1)))
    return df[ok].reset_index(drop=True), int((~ok).sum())


def _fetch_retry(fetch_fn: Callable, token: int, lo: date, hi: date,
                 max_retries: int, sleep: Callable) -> list:
    frm = datetime.combine(lo, datetime.min.time())
    to = datetime.combine(hi, datetime.max.time().replace(microsecond=0))
    for attempt in range(max_retries):
        try:
            return fetch_fn(token, frm, to, "minute")
        except Exception:
            if attempt == max_retries - 1:
                raise
            sleep(2 ** attempt)


def fetch_months(fetch_fn: Callable, symbol: str, token: int,
                 start: date, end: date, cache_dir: str = DEFAULT_CACHE,
                 throttle: float = 0.35, max_retries: int = 5,
                 sleep: Callable = _time.sleep,
                 today: Optional[date] = None) -> Tuple[int, int]:
    """Fetch [start, end] month-by-month into {cache_dir}/{symbol}/{YYYY-MM}.parquet.
    Complete cached months are skipped (resumable); the current month is
    refetched. Returns (written, skipped)."""
    today = today or date.today()
    outdir = os.path.join(cache_dir, symbol)
    os.makedirs(outdir, exist_ok=True)
    written = skipped = 0
    for lo, hi in month_range(start, end):
        path = os.path.join(outdir, f"{lo:%Y-%m}.parquet")
        if os.path.exists(path) and hi < today:      # complete month already cached
            skipped += 1
            continue
        recs = _fetch_retry(fetch_fn, token, lo, hi, max_retries, sleep)
        df, _dropped = clean(_normalize(recs))
        df.to_parquet(path, index=False)
        written += 1
        sleep(throttle)
    return written, skipped


def load_cached(symbol: str, cache_dir: str = DEFAULT_CACHE) -> pd.DataFrame:
    """All cached months for ``symbol``, concatenated + cleaned — the 1-min
    frame the EquityBacktest (and SpotSeries) consumes."""
    paths = sorted(glob.glob(os.path.join(cache_dir, symbol, "*.parquet")))
    if not paths:
        raise FileNotFoundError(f"no cached months for {symbol!r} under {cache_dir!r}")
    df, _ = clean(pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True))
    return df


def make_kite():
    """Real Kite client from the tradealgo .env (network path — not unit-tested)."""
    from dotenv import load_dotenv          # lazy: only the live fetch needs these
    from kiteconnect import KiteConnect
    load_dotenv(TRADEALGO_ENV)
    kite = KiteConnect(api_key=os.environ["ZERODHA_API_KEY"])
    kite.set_access_token(os.environ["ZERODHA_ACCESS_TOKEN"])
    return kite
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/unit/test_kite_loader.py -v`
Expected: 8 PASSED

- [ ] **Step 5: Record the optional dependencies**

In `pyproject.toml`, change ONLY the `[project.optional-dependencies]` section from:

```toml
[project.optional-dependencies]
dev = ["pytest>=7"]
```

to:

```toml
[project.optional-dependencies]
dev = ["pytest>=7"]
equity-fetch = ["kiteconnect>=5", "python-dotenv>=1"]   # live Kite fetch only; backtest/tests don't need them
```

- [ ] **Step 6: Full suite + commit**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest -q` — expected 136 passed.

```bash
cd "/Users/gokul/Desktop/buildstrategies " && git add engine/equity/kite_loader.py tests/unit/test_kite_loader.py pyproject.toml && git commit -m "feat(equity): Kite 1-min loader — month-chunked, resumable, retry/backoff, integrity clean"
```

---

### Task 5: `EquityBacktest` driver + golden synthetic-day tests

**Files:**
- Create: `engine/equity/backtest.py`
- Test: `tests/golden/test_equity_backtest_driver.py`

**Interfaces:**
- Consumes: `SpotSeries`, `_resample_ohlc` (reused); `Result` from `engine.backtest`; `Trade` (via `EquityPosition.close`); `EquityBarContext` (Task 3); `EquityPosition` (Task 2); `EquityOrder` (strategies return it); `EquityCostModel` (Task 1); `resolve_level_exit` + `Exit` (reused); `Strategy` base (reused).
- Produces: `EquityKnobs(slippage_pct=0.0005, timeframe="5min", min_bars_per_day=300, max_overnight_gap=0.15)`; `EquityBacktest(one_min_df: pd.DataFrame, cost_model: EquityCostModel, knobs: EquityKnobs | None = None)` with `run(strategy, start: date | None = None, end: date | None = None) -> Result` and `.skipped_days: list[tuple[date, str]]` (reset on each `run`).

**Driver contract (mirrors the SHAPE of `Backtest._run_day`, never its code):** per day — gate on min-bars and overnight gap → `on_day_start` at the first 5-min bar → per 5-min bar: flat → `on_bar` may return `[EquityOrder]`, filled at the NEXT 5-min bar's open with adverse slip (long ×(1+s), short ×(1−s)), `qty = int(capital // fill)`; in-position → `manage` may return `Exit.market` (fills next 5-min open, adverse) or `Exit.at` level (resolved intrabar on the 1-min bars in `[b, b+tf)` via the shared `resolve_level_exit`, gap-through honest, adverse slip on the fill) → EOD square-off at the day's last 1-min close (adverse slip), reason `"eod"`. Partial exits (`fraction != 1.0`) raise `ValueError` — v1 has no scale-out.

- [ ] **Step 1: Write the failing golden test**

Create `tests/golden/test_equity_backtest_driver.py`:

```python
"""Golden end-to-end: EquityBacktest on hand-built synthetic days.

Every fill and P&L is hand-verified: next-open entry with slip, intrabar
level target on 1-min bars (incl. gap-through), market exit, EOD square-off,
capital sizing, and the two day-gate skip rules.
"""
from datetime import date, time

import pytest

from engine.equity.backtest import EquityBacktest, EquityKnobs
from engine.equity.costs import EquityCostModel
from engine.equity.model import EquityOrder
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit
from tests.equity_helpers import frame, synth_day

M = EquityCostModel()
SLIP = 0.0005
D1, D2 = date(2024, 1, 1), date(2024, 1, 2)


class OneShot(Strategy):
    """Enter at a fixed signal bar ON ONE DAY ONLY (default D2 — otherwise the
    flat setup day D1 would also trade); exits are scripted via ``manage_fn``."""
    def __init__(self, direction, signal_time, manage_fn=lambda ctx, pos: None,
                 capital=100_000.0, day=D2):
        self.direction, self.signal_time = direction, signal_time
        self.manage_fn, self.capital, self.day = manage_fn, capital, day

    def on_day_start(self, ctx):
        self.done = False

    def on_bar(self, ctx):
        if (not self.done and ctx.now.date() == self.day
                and ctx.now.time() == self.signal_time):
            self.done = True
            return [EquityOrder(direction=self.direction, capital=self.capital)]
        return []

    def manage(self, ctx, pos):
        return self.manage_fn(ctx, pos)


def two_flat_days(day2_overrides):
    """Day 1 establishes a prior session; day 2 carries the scenario."""
    return frame(synth_day(D1, 1700.0) + synth_day(D2, 1700.0,
                                                   overrides=day2_overrides))


def test_long_market_exit_hand_verified():
    # Fill minute 09:25 opens 1702; market exit signalled at 10:00 fills at
    # the 10:05 bar open 1712.
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702),
                        "10:05": (1712, 1712, 1712, 1712)})
    strat = OneShot("long", time(9, 20),
                    manage_fn=lambda ctx, pos:
                        Exit.market("out") if ctx.now.time() >= time(10, 0) else None)
    bt = EquityBacktest(df, M)
    r = bt.run(strat)
    assert len(r.trades) == 1
    t = r.trades[0]
    entry = 1702 * (1 + SLIP)                    # 1702.851
    exit_ = 1712 * (1 - SLIP)                    # 1711.144
    qty = int(100_000 // entry)                  # 58
    assert t.side == "LONG" and t.qty == qty == 58
    assert t.entry == pytest.approx(entry) and t.exit == pytest.approx(exit_)
    assert t.gross == pytest.approx((exit_ - entry) * qty)
    assert t.cost == pytest.approx(M.total(entry * qty, exit_ * qty))
    assert t.net == pytest.approx(t.gross - t.cost)
    assert t.reason == "out" and t.date == D2
    assert t.entry_dt.time() == time(9, 25) and t.exit_dt.time() == time(10, 5)


def test_long_intrabar_target_at_level():
    # Resting +10-ish target 1710: minute 10:07 (inside the 10:05 window)
    # trades high 1711 without opening past the level -> fill AT the level.
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702),
                        "10:07": (1708, 1711, 1707, 1710)})
    strat = OneShot("long", time(9, 20),
                    manage_fn=lambda ctx, pos: Exit.at(1710.0, "rise", "target"))
    r = EquityBacktest(df, M).run(strat)
    assert len(r.trades) == 1
    t = r.trades[0]
    assert t.exit == pytest.approx(1710.0 * (1 - SLIP))
    assert t.reason == "target"
    assert t.exit_dt.time() == time(10, 7)       # the 1-min bar that hit


def test_long_target_gap_through_fills_at_open():
    # Minute 10:07 OPENS at 1713, past the 1710 target -> honest fill at open.
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702),
                        "10:07": (1713, 1714, 1712, 1713)})
    strat = OneShot("long", time(9, 20),
                    manage_fn=lambda ctx, pos: Exit.at(1710.0, "rise", "target"))
    r = EquityBacktest(df, M).run(strat)
    assert r.trades[0].exit == pytest.approx(1713.0 * (1 - SLIP))


def test_short_eod_square_off():
    # Short fills DOWN at entry, EOD buys back UP at the last 1-min close.
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702),
                        "15:29": (1690, 1690, 1690, 1690)})
    strat = OneShot("short", time(9, 20))        # no exit -> rides to EOD
    r = EquityBacktest(df, M).run(strat)
    assert len(r.trades) == 1
    t = r.trades[0]
    entry = 1702 * (1 - SLIP)                    # short sells down: 1701.149
    exit_ = 1690 * (1 + SLIP)                    # buys back up:    1690.845
    qty = int(100_000 // entry)                  # 58
    assert t.side == "SHORT" and t.qty == qty
    assert t.entry == pytest.approx(entry) and t.exit == pytest.approx(exit_)
    assert t.gross == pytest.approx((entry - exit_) * qty)
    assert t.reason == "eod" and t.exit_dt.time() == time(15, 29)


def test_short_intrabar_target_falls_to_level():
    # Short target = price FALLING to 1690 ("fall" trigger).
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702),
                        "11:03": (1692, 1693, 1689, 1690)})
    strat = OneShot("short", time(9, 20),
                    manage_fn=lambda ctx, pos: Exit.at(1690.0, "fall", "target"))
    r = EquityBacktest(df, M).run(strat)
    t = r.trades[0]
    assert t.exit == pytest.approx(1690.0 * (1 + SLIP))
    assert t.reason == "target" and t.exit_dt.time() == time(11, 3)


def test_min_bars_day_skipped():
    short_day = synth_day(D2, 1700.0, minutes=100)       # Muhurat-like stub
    df = frame(synth_day(D1, 1700.0) + short_day)
    bt = EquityBacktest(df, M)
    r = bt.run(OneShot("long", time(9, 20)))
    assert [t for t in r.trades if t.date == D2] == []
    assert (D2, "min_bars") in bt.skipped_days


def test_overnight_gap_day_skipped():
    # Day 2 opens 2x day 1's close (unadjusted 1:1 bonus) -> skip.
    df = frame(synth_day(D1, 1700.0) + synth_day(D2, 850.0))
    bt = EquityBacktest(df, M)
    r = bt.run(OneShot("long", time(9, 20)))
    assert [t for t in r.trades if t.date == D2] == []
    assert (D2, "overnight_gap") in bt.skipped_days


def test_partial_exit_fraction_rejected():
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702)})
    strat = OneShot("long", time(9, 20),
                    manage_fn=lambda ctx, pos: Exit.at(1710.0, "rise", "t",
                                                       fraction=0.5))
    with pytest.raises(ValueError):
        EquityBacktest(df, M).run(strat)


def test_qty_zero_when_capital_too_small():
    df = two_flat_days({"09:25": (1702, 1702, 1702, 1702)})
    strat = OneShot("long", time(9, 20), capital=1000.0)  # < 1 share
    r = EquityBacktest(df, M).run(strat)
    assert r.trades == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/golden/test_equity_backtest_driver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.equity.backtest'`

- [ ] **Step 3: Write the implementation**

Create `engine/equity/backtest.py`:

```python
"""Cash-equity intraday backtest driver.

Mirrors the SHAPE of engine.backtest.Backtest._run_day (on_day_start -> on_bar
-> fill next-open -> manage -> level/market/EOD) but fills and exits read the
stock's OWN bars — no chain, no premium layer, and the proven options driver
is never imported. Reuses only instrument-agnostic pieces: SpotSeries (signal
+ fill feed in one), Result/Trade (pure data/metrics), and the shared
resolve_level_exit for honest intrabar level fills.

Day gates (spec 2026-07-03): skip sessions with fewer than min_bars_per_day
1-min bars (partial/Muhurat days) and days whose overnight gap exceeds
max_overnight_gap — Kite intraday history is NOT back-adjusted, so an
unadjusted bonus/split (HDFCBANK 1:1, Aug 2025) makes prior-day CPR levels
garbage; intraday-only means the only damage is a wasted signal day.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import List, Optional, Tuple

import pandas as pd

from engine.backtest import Result
from engine.data.spot import SpotSeries, _resample_ohlc
from engine.equity.context import EquityBarContext
from engine.equity.costs import EquityCostModel
from engine.equity.position import EquityPosition
from engine.position.manager import Trade
from engine.strategy.exit import resolve_level_exit


@dataclass
class EquityKnobs:
    slippage_pct: float = 0.0005      # ~HDFCBANK cash spread; NOT the options 0.25%
    timeframe: str = "5min"
    min_bars_per_day: int = 300       # of 375; skips partial/Muhurat sessions
    max_overnight_gap: float = 0.15   # skip unadjusted corporate-action days


class EquityBacktest:
    def __init__(self, one_min_df: pd.DataFrame, cost_model: EquityCostModel,
                 knobs: Optional[EquityKnobs] = None):
        self._costs = cost_model
        self._k = knobs or EquityKnobs()
        self._tf = pd.Timedelta(self._k.timeframe)
        df = one_min_df.sort_values("DateTime").reset_index(drop=True)
        self._m1_by_day = {d: sub for d, sub in df.groupby(df["DateTime"].dt.date)}
        self._m1 = SpotSeries(df)
        self._spot = SpotSeries(_resample_ohlc(df, self._k.timeframe, "left"))
        self._bars_by_day: dict = {}
        for ts in self._spot.timestamps():
            self._bars_by_day.setdefault(ts.to_pydatetime().date(), []).append(ts)
        self.skipped_days: List[Tuple[date, str]] = []   # (day, reason) quality log

    def run(self, strategy, start: Optional[date] = None,
            end: Optional[date] = None) -> Result:
        self.skipped_days = []
        result = Result()
        for day in sorted(self._m1_by_day):
            if (start and day < start) or (end and day > end):
                continue
            result.trades.extend(self._run_day(strategy, day))
        return result

    # ---- one day ----
    def _run_day(self, strategy, day: date) -> List[Trade]:
        m1 = self._m1_by_day[day]
        if len(m1) < self._k.min_bars_per_day:
            self.skipped_days.append((day, "min_bars"))
            return []
        first = m1.iloc[0]
        prev_close = self._m1.close_before(first["DateTime"])
        if (prev_close is not None
                and abs(float(first["Open"]) / prev_close - 1.0)
                > self._k.max_overnight_gap):
            self.skipped_days.append((day, "overnight_gap"))
            return []

        bars = self._bars_by_day.get(day, [])
        if not bars:
            return []
        strategy.on_day_start(EquityBarContext(now=bars[0].to_pydatetime(),
                                               spot=self._spot))
        trades: List[Trade] = []
        pos: Optional[EquityPosition] = None
        entry_dt = None
        for b in bars:
            now = b.to_pydatetime()
            ctx = EquityBarContext(now=now, spot=self._spot)
            if pos is None:
                orders = strategy.on_bar(ctx)
                if orders:
                    o = orders[0]
                    fb = self._spot.bar_at(b + self._tf)
                    if fb is not None:
                        fill = self._entry_fill(fb.open, o.direction)
                        qty = int(o.capital // fill)
                        if qty > 0:
                            pos = EquityPosition(direction=o.direction,
                                                 entry=fill, qty=qty)
                            entry_dt = fb.dt
            else:
                ex = strategy.manage(ctx, pos)
                if ex is None:
                    continue
                if ex.fraction != 1.0:
                    raise ValueError("equity v1 has no partial exits "
                                     f"(got fraction={ex.fraction})")
                if ex.kind == "market":
                    fb = self._spot.bar_at(b + self._tf)
                    if fb is not None:
                        t = pos.close(self._exit_fill(fb.open, pos.direction),
                                      self._costs, reason=ex.reason)
                        trades.append(replace(t, date=day, entry_dt=entry_dt,
                                              exit_dt=fb.dt))
                        pos = None
                elif ex.kind == "level":
                    for xdt, o_, h_, l_ in self._m1_window(m1, b, b + self._tf):
                        fill, hit = resolve_level_exit(ex, o_, h_, l_)
                        if hit:
                            t = pos.close(self._exit_fill(fill, pos.direction),
                                          self._costs, reason=ex.reason)
                            trades.append(replace(t, date=day, entry_dt=entry_dt,
                                                  exit_dt=xdt.to_pydatetime()))
                            pos = None
                            break

        if pos is not None:                              # EOD square-off (MIS)
            last = m1.iloc[-1]
            t = pos.close(self._exit_fill(float(last["Close"]), pos.direction),
                          self._costs, reason="eod")
            trades.append(replace(t, date=day, entry_dt=entry_dt,
                                  exit_dt=last["DateTime"].to_pydatetime()))
        return trades

    # ---- fills: adverse slippage by direction ----
    def _entry_fill(self, raw: float, direction: str) -> float:
        s = self._k.slippage_pct
        return raw * (1 + s) if direction == "long" else raw * (1 - s)

    def _exit_fill(self, raw: float, direction: str) -> float:
        s = self._k.slippage_pct
        return raw * (1 - s) if direction == "long" else raw * (1 + s)

    @staticmethod
    def _m1_window(m1: pd.DataFrame, start, end):
        """1-min bars (dt, open, high, low) in [start, end) — intrabar exits."""
        m = (m1["DateTime"] >= start) & (m1["DateTime"] < end)
        return [(r["DateTime"], float(r["Open"]), float(r["High"]), float(r["Low"]))
                for _, r in m1[m].iterrows()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/golden/test_equity_backtest_driver.py -v`
Expected: 9 PASSED

- [ ] **Step 5: Full suite + commit**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest -q` — expected 145 passed.

```bash
cd "/Users/gokul/Desktop/buildstrategies " && git add engine/equity/backtest.py tests/golden/test_equity_backtest_driver.py && git commit -m "feat(equity): EquityBacktest driver — next-open fills, intrabar levels, day gates, EOD"
```

---

### Task 6: `CPRBreakoutEquity` + signal-parity test

**Files:**
- Create: `strategies/cpr_breakout_equity.py`
- Test: `tests/unit/test_cpr_breakout_equity.py`

**Interfaces:**
- Consumes: `Strategy`, `Exit` (reused); `EquityOrder` (Task 2); `EquityBarContext` accessors (Task 3); `EquityPosition.direction/.entry` (Task 2). Parity reference: `strategies.cpr_breakout.CPRBreakout` (imported by the TEST only, never by the strategy).
- Produces: `CPRBreakoutEquity(target_pts=10.0, stop_mode="far", capital=100_000.0, entry_cutoff=time(14,45), square=time(15,15))` — a `Strategy` returning `[EquityOrder]`; `target_pts=None` disables the target (stop/EOD only, the raw-signal sweep baseline).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cpr_breakout_equity.py`:

```python
"""CPR-equity entries on synthetic paths + signal parity with the options CPR.

Parity is the port's key risk: the SAME spot path must produce IDENTICAL cross
timestamps and directions in strategies.cpr_breakout.CPRBreakout (options) and
CPRBreakoutEquity — proving the signal transferred faithfully.
"""
from datetime import date, datetime, time

import pytest

from engine.data.spot import SpotSeries, _resample_ohlc
from engine.equity.context import EquityBarContext
from engine.equity.position import EquityPosition
from engine.strategy.base import BarContext
from strategies.cpr_breakout import CPRBreakout
from strategies.cpr_breakout_equity import CPRBreakoutEquity
from tests.equity_helpers import frame, synth_day

D1, D2 = date(2024, 1, 1), date(2024, 1, 2)

# Day 1: H=1720, L=1690, C=1710 -> pivot 1706.667, bc 1705, tc 1708.333
# CPR band: top=1708.333, bottom=1705.0
DAY1 = {"10:00": (1700, 1720, 1700, 1715),
        "11:00": (1700, 1700, 1690, 1695),
        "15:29": (1710, 1710, 1710, 1710)}
TOP, BOTTOM = 1708.0 + 1.0 / 3.0, 1705.0


def series(day2_overrides):
    rows = synth_day(D1, 1700.0, overrides=DAY1) + synth_day(D2, 1700.0,
                                                             overrides=day2_overrides)
    return SpotSeries(_resample_ohlc(frame(rows), "5min", "left"))


def day2_bars(spot):
    return [t for t in spot.timestamps() if t.to_pydatetime().date() == D2]


def run_equity(spot, strat):
    """Drive the strategy over day 2; return [(bar_dt, direction)] of entries."""
    fires = []
    bars = day2_bars(spot)
    strat.on_day_start(EquityBarContext(now=bars[0].to_pydatetime(), spot=spot))
    for b in bars:
        orders = strat.on_bar(EquityBarContext(now=b.to_pydatetime(), spot=spot))
        for o in orders:
            fires.append((b.to_pydatetime(), o.direction))
    return fires


def run_options(spot, strat):
    """Same loop through the options CPRBreakout (no chain needed: max_iv=None)."""
    fires = []
    bars = day2_bars(spot)
    strat.on_day_start(BarContext(now=bars[0].to_pydatetime(), chain=None, spot=spot))
    for b in bars:
        orders = strat.on_bar(BarContext(now=b.to_pydatetime(), chain=None, spot=spot))
        for o in orders:
            fires.append((b.to_pydatetime(), o.side))
    return fires


UP_CROSS = {"10:32": (1706, 1712, 1706, 1711)}    # 10:30 bar: low<=top, close>top
DOWN_CROSS = {"10:32": (1707, 1707, 1701, 1703)}  # 10:30 bar: high>=bottom, close<bottom
GAP_ABOVE = {"10:31": (1712, 1715, 1711, 1714),   # whole 10:30 bar above top:
             "10:32": (1714, 1715, 1712, 1714),   # no physical cross -> no entry
             "10:33": (1713, 1714, 1712, 1713),
             "10:34": (1713, 1714, 1712, 1714)}


def test_up_cross_goes_long():
    spot = series(UP_CROSS)
    fires = run_equity(spot, CPRBreakoutEquity())
    assert fires == [(datetime(2024, 1, 2, 10, 30), "long")]


def test_down_cross_goes_short():
    spot = series(DOWN_CROSS)
    fires = run_equity(spot, CPRBreakoutEquity())
    assert fires == [(datetime(2024, 1, 2, 10, 30), "short")]


def test_gap_beyond_band_does_not_enter():
    # 10:30 bar sits entirely above the top edge (low > top): the cross
    # requirement (low <= top) must reject it, and the flat rest of the day
    # never crosses -> no entries at all.
    spot = series(GAP_ABOVE)
    assert run_equity(spot, CPRBreakoutEquity()) == []


def test_no_entry_at_or_after_cutoff():
    late = {"14:47": (1706, 1712, 1706, 1711)}    # falls in the 14:45 bar
    spot = series(late)
    assert run_equity(spot, CPRBreakoutEquity(entry_cutoff=time(14, 45))) == []


def test_one_entry_per_day():
    two = {"10:32": (1706, 1712, 1706, 1711),
           "10:36": (1704, 1704, 1704, 1704),     # dips back below
           "10:42": (1706, 1712, 1706, 1711)}     # second cross ignored
    spot = series(two)
    assert len(run_equity(spot, CPRBreakoutEquity())) == 1


@pytest.mark.parametrize("overrides,eq_dir,opt_side", [
    (UP_CROSS, "long", "CALL"),
    (DOWN_CROSS, "short", "PUT"),
])
def test_signal_parity_with_options_cpr(overrides, eq_dir, opt_side):
    spot = series(overrides)
    eq = run_equity(spot, CPRBreakoutEquity())
    opt = run_options(spot, CPRBreakout(max_iv=None, max_cpr_ratio=None))
    assert len(eq) == len(opt) == 1
    assert eq[0][0] == opt[0][0]                  # same bar timestamp
    assert (eq[0][1], opt[0][1]) == (eq_dir, opt_side)


def test_manage_cpr_far_stop_and_target_levels():
    spot = series(UP_CROSS)
    strat = CPRBreakoutEquity(target_pts=10.0)
    run_equity(spot, strat)                        # enter long; stop = bottom (far)
    pos = EquityPosition(direction="long", entry=1712.0, qty=58)

    # flat 1700 bar: above the far stop (bottom=1705)? NO — 1700 < 1705 -> stop.
    ctx = EquityBarContext(now=datetime(2024, 1, 2, 11, 0), spot=spot)
    ex = strat.manage(ctx, pos)
    assert ex.kind == "market" and ex.reason == "cpr_stop"

    # bar at 10:30 closes 1711 > bottom -> no stop; target resting at entry+10.
    ctx2 = EquityBarContext(now=datetime(2024, 1, 2, 10, 30), spot=spot)
    ex2 = strat.manage(ctx2, pos)
    assert ex2.kind == "level" and ex2.trigger == "rise"
    assert ex2.level == pytest.approx(1722.0)

    # short mirror: target is a "fall" level below entry.
    spot_dn = series(DOWN_CROSS)
    strat_dn = CPRBreakoutEquity(target_pts=10.0)
    run_equity(spot_dn, strat_dn)
    pos_dn = EquityPosition(direction="short", entry=1702.0, qty=58)
    ex3 = strat_dn.manage(EquityBarContext(now=datetime(2024, 1, 2, 10, 30),
                                           spot=spot_dn), pos_dn)
    assert ex3.kind == "level" and ex3.trigger == "fall"
    assert ex3.level == pytest.approx(1692.0)

    # square time -> EOD market exit regardless.
    ex4 = strat.manage(EquityBarContext(now=datetime(2024, 1, 2, 15, 15),
                                        spot=spot), pos)
    assert ex4.kind == "market" and ex4.reason == "eod"


def test_target_none_means_hold():
    spot = series(UP_CROSS)
    strat = CPRBreakoutEquity(target_pts=None)
    run_equity(spot, strat)
    pos = EquityPosition(direction="long", entry=1712.0, qty=58)
    ctx = EquityBarContext(now=datetime(2024, 1, 2, 10, 30), spot=spot)
    assert strat.manage(ctx, pos) is None          # no stop hit, no target -> hold
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/unit/test_cpr_breakout_equity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategies.cpr_breakout_equity'`

- [ ] **Step 3: Write the implementation**

Create `strategies/cpr_breakout_equity.py`:

```python
"""CPR (Central Pivot Range) breakout on cash equity — HDFCBANK v1.

Same prior-day CPR band and cross-through entry as strategies/cpr_breakout.py
(the walk-forward-validated options winner), ported off options: an up-cross
buys shares, a down-cross short-sells (intraday MIS). Deliberately RAW per the
2026-07-03 spec — no IV gate (no per-stock IV in the store), no scale-out or
premium trail (those were calibrated in option-premium points): single
close-beyond-band stop + single fixed price-point target + EOD square-off.

    Pivot = (H + L + C) / 3 ;  BC = (H + L) / 2 ;  TC = 2*Pivot - BC
    top = max(TC, BC) ;  bottom = min(TC, BC)

Entry (one breakout per day, 5-min): a candle CROSSES a band edge and CLOSES
beyond it — the low/high must touch the line, rejecting gaps that open already
outside the CPR.

Exits: stop_mode "far" (opposite edge, default) / "broken" (edge just crossed)
/ "none"; target_pts price points from entry (None = no target — the sweep
baseline); square-off at ``square``.
"""
from datetime import time

from engine.equity.model import EquityOrder
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit


class CPRBreakoutEquity(Strategy):
    def __init__(self, target_pts=10.0, stop_mode="far", capital=100_000.0,
                 entry_cutoff=time(14, 45), square=time(15, 15)):
        self.TARGET_PTS = target_pts   # price points; None = stop/EOD only
        self.stop_mode = stop_mode     # "far" | "broken" | "none"
        self.CAPITAL = capital         # fixed rupees per trade (spec: 100k default)
        self.ENTRY_CUTOFF, self.SQUARE = entry_cutoff, square

    def on_day_start(self, ctx):
        self.active = False
        self.entered = False
        self.stop_close = None
        hlc = ctx.prev_day_hlc()
        if hlc is None:
            return
        h, l, c = hlc
        pivot = (h + l + c) / 3.0
        bc = (h + l) / 2.0
        tc = 2.0 * pivot - bc
        self.top, self.bottom = max(tc, bc), min(tc, bc)
        self.active = True

    def on_bar(self, ctx):
        if not self.active or self.entered:
            return []
        if ctx.now.time() >= self.ENTRY_CUTOFF:
            return []
        bar = ctx.spot_bar
        direction = None
        if bar.low <= self.top and bar.close > self.top:            # crossed UP
            direction = "long"
        elif bar.high >= self.bottom and bar.close < self.bottom:   # crossed DOWN
            direction = "short"
        if direction is None:
            return []
        self.entered = True
        # close-based CPR stop level, same semantics as the options version
        if self.stop_mode == "none":
            self.stop_close = -1e18 if direction == "long" else 1e18
        elif direction == "long":
            self.stop_close = self.bottom if self.stop_mode == "far" else self.top
        else:
            self.stop_close = self.top if self.stop_mode == "far" else self.bottom
        return [EquityOrder(direction=direction, capital=self.CAPITAL)]

    def manage(self, ctx, pos):
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        close = ctx.spot_bar.close
        if pos.direction == "long" and close < self.stop_close:    # CPR-close stop
            return Exit.market("cpr_stop")
        if pos.direction == "short" and close > self.stop_close:
            return Exit.market("cpr_stop")
        if self.TARGET_PTS is None:
            return None
        if pos.direction == "long":
            return Exit.at(pos.entry + self.TARGET_PTS, "rise", "target")
        return Exit.at(pos.entry - self.TARGET_PTS, "fall", "target")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest tests/unit/test_cpr_breakout_equity.py -v`
Expected: 9 PASSED

- [ ] **Step 5: Full suite + commit**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest -q` — expected 154 passed.

```bash
cd "/Users/gokul/Desktop/buildstrategies " && git add strategies/cpr_breakout_equity.py tests/unit/test_cpr_breakout_equity.py && git commit -m "feat(equity): CPRBreakoutEquity — ported CPR cross, parity-tested vs options CPR"
```

---

### Task 7: Fetch script + target-sweep analysis runner

**Files:**
- Create: `analysis/fetch_hdfcbank.py`
- Create: `analysis/cpr_equity_run.py`

Analysis scripts are not unit-tested (repo convention — they touch network/large data); correctness lives in Tasks 1–6.

**Interfaces:**
- Consumes: `make_kite`, `fetch_months`, `load_cached`, `INSTRUMENT_TOKENS` (Task 4); `EquityBacktest`, `EquityKnobs` (Task 5); `EquityCostModel` (Task 1); `CPRBreakoutEquity` (Task 6); `Result` metrics (reused).
- Produces: runnable scripts; the sweep output is the Stage-1-style evidence for the target knob decision.

- [ ] **Step 1: Write the fetch script**

Create `analysis/fetch_hdfcbank.py`:

```python
"""One-shot HDFCBANK 1-min fetch/refresh into data_equity/ (resumable).

Prereq: refresh the daily Kite token first —
    cd /Users/gokul/Desktop/tradealgo && python3 zerodha_auth.py

Re-running skips complete cached months and refetches only the current month,
so it doubles as the daily/weekly refresh command.
"""
from datetime import date

from engine.equity.kite_loader import (INSTRUMENT_TOKENS, fetch_months,
                                       load_cached, make_kite)

START = date(2020, 1, 1)      # ~matches the NIFTY store's range

kite = make_kite()
written, skipped = fetch_months(kite.historical_data, "HDFCBANK",
                                INSTRUMENT_TOKENS["HDFCBANK"],
                                start=START, end=date.today())
print(f"months written={written} skipped={skipped}")
df = load_cached("HDFCBANK")
print(f"bars={len(df):,}  {df['DateTime'].min()} -> {df['DateTime'].max()}")
```

- [ ] **Step 2: Write the sweep runner**

Create `analysis/cpr_equity_run.py`:

```python
"""CPR-equity target sweep on HDFCBANK (spec 2026-07-03).

Raw unfiltered CPR cross, long + short, fixed Rs100k/trade, Zerodha MIS costs,
0.05% slip. Sweeps the single price-point target the same way the options
CPR's +50 premium target was found. target=None = stop/EOD only (baseline).
"""
from engine.equity.backtest import EquityBacktest
from engine.equity.costs import EquityCostModel
from engine.equity.kite_loader import load_cached
from strategies.cpr_breakout_equity import CPRBreakoutEquity

df = load_cached("HDFCBANK")
bt = EquityBacktest(df, EquityCostModel())
print(f"bars={len(df):,}  {df['DateTime'].min().date()} -> {df['DateTime'].max().date()}\n")

for tgt in (None, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0):
    r = bt.run(CPRBreakoutEquity(target_pts=tgt))
    label = "none" if tgt is None else f"{tgt:g}"
    print(f"target={label:>5}: trades={len(r.trades):4d}  net={r.net():>12,.0f}  "
          f"PF={r.profit_factor():5.2f}  win={r.win_rate():5.1%}  "
          f"tail-5={r.tail_excl(5):>12,.0f}")
    print(f"              by_year: "
          f"{{{', '.join(f'{y}: {v:,.0f}' for y, v in sorted(r.by_year().items()))}}}")

print(f"\nskipped days ({len(bt.skipped_days)}):", bt.skipped_days)
```

- [ ] **Step 3: Sanity-run the fetch (requires a fresh Kite token — coordinate with the user)**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 analysis/fetch_hdfcbank.py`
Expected: month-by-month progress, ending with a bar count and a 2020→today date range. If the token is stale, Kite raises `TokenException` — ask the user to run `python3 zerodha_auth.py` in the tradealgo folder, then re-run (the fetch resumes; nothing is lost).

- [ ] **Step 4: Run the sweep**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 analysis/cpr_equity_run.py`
Expected: one summary line + by-year breakdown per target config, then the skipped-days log (expect the Aug-2025 bonus day under "overnight_gap" and Muhurat sessions under "min_bars").

- [ ] **Step 5: Full suite + commit**

Run: `cd "/Users/gokul/Desktop/buildstrategies " && PYTHONPATH="$PWD" python3 -m pytest -q` — expected 154 passed.

```bash
cd "/Users/gokul/Desktop/buildstrategies " && git add analysis/fetch_hdfcbank.py analysis/cpr_equity_run.py && git commit -m "feat(equity): HDFCBANK fetch script + CPR target-sweep runner"
```

---

## Verification (whole plan)

1. `PYTHONPATH="$PWD" python3 -m pytest -q` — 154 passed, 0 failed (115 existing untouched).
2. `git log --oneline` — one baseline + one commit per task.
3. `grep -rn "from engine.equity" engine/ --include="*.py" | grep -v "engine/equity/"` — empty (options core never imports equity); `grep -rn "engine.backtest import\|engine.position" engine/equity/` — only the sanctioned `Result`/`Trade` imports.
4. The sweep output exists and the skipped-days log shows the Aug-2025 gap day — the analysis conversation (interpreting results, picking a target) happens with the user afterwards; it is not part of this plan.
