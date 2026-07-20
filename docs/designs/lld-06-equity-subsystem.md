# LLD-06 — Equity Subsystem

> Modules: `engine/equity/backtest.py`, `context.py`, `position.py`, `model.py`,
> `costs.py`, `kite_loader.py`, `gap_features.py`, `nifty_gap.py`.
> Parent: [High-Level Design](./high-level-design.md).

A parallel cash-equity intraday track for NSE stocks (HDFCBANK, ADANIENT,
TATASTEEL). It mirrors the *shape* of the options driver but reads the stock's
own bars — no chain, no premium layer. It reuses only instrument-agnostic pieces.

## 1. What is shared vs. independent

```mermaid
flowchart TB
    subgraph Shared["Reused from the core engine"]
        SP["SpotSeries + _resample_ohlc"]
        TR["Trade (frozen dataclass)"]
        RES["Result (metrics)"]
        RLE["resolve_level_exit (gap-through)"]
    end
    subgraph Equity["engine/equity/ (independent)"]
        EBT["EquityBacktest"]
        ECX["EquityBarContext"]
        EPO["EquityPosition"]
        EOR["EquityOrder"]
        ECM["EquityCostModel"]
        EKL["kite_loader"]
        EGF["gap_features / nifty_gap"]
    end
    EBT --> SP
    EBT --> TR
    EBT --> RES
    EBT --> RLE
```

The options `Backtest` is **never imported**. The equity context deliberately
duplicates ~6 one-line spot-forwarding methods rather than making the options
`BarContext`'s `chain` optional — zero shared mutable surface with the proven
options path (a deliberate zero-risk micro-decision from the 2026-07-03 spec).

## 2. `EquityBacktest` — the driver

Same `on_day_start → on_bar → fill next-open → manage → level/market/EOD` shape as
`engine/backtest.py._run_day`, but fills and exits read the stock's own 1-min and
resampled bars.

```mermaid
flowchart TB
    CTOR["__init__(one_min_df, cost_model, knobs)"] --> BUILD["build: m1_by_day, m1 SpotSeries,<br/>resampled signal SpotSeries, bars_by_day"]
    BUILD --> RUN["run(strategy, start, end)"]
    RUN --> DAY{"for each day"}
    DAY --> GATE1{"< min_bars_per_day?"}
    GATE1 -->|yes| SKIP1["skip: 'min_bars'<br/>(partial/Muhurat session)"]
    GATE1 -->|no| GATE2{"|open/prev_close - 1| > max_overnight_gap?"}
    GATE2 -->|yes| SKIP2["skip: 'overnight_gap'<br/>(unadjusted corporate action)"]
    GATE2 -->|no| LOOP["_run_day"]
    LOOP --> DAY
```

### 2.1 Day gates (data-quality guards)

Kite intraday history is **not back-adjusted**, so a bonus/split (HDFCBANK 1:1,
Aug 2025) makes prior-day levels garbage. Two gates protect against acting on bad
days, logged to `skipped_days` for visibility:

| Gate | Rule | Reason |
|---|---|---|
| `min_bars_per_day` | fewer than N of 375 1-min bars → skip | partial / Muhurat sessions |
| `max_overnight_gap` | `|open/prev_close - 1| > threshold` → skip | unadjusted corporate-action day |

Because the track is intraday-only, the only damage from a bad day is a wasted
signal day — never a corrupt overnight hold.

### 2.2 Per-day loop

```mermaid
flowchart TB
    DS["on_day_start(EquityBarContext@bar0)"] --> LOOP{"for each signal bar B"}
    LOOP --> FLAT{"pos None?"}
    FLAT -->|yes| OB["on_bar → orders"]
    OB --> ONE{"len==1? (v1: one order/bar)"}
    ONE -->|no| ERR["raise ValueError"]
    ONE -->|yes| FB["fill bar = B + tf*(1+entry_delay_bars)"]
    FB --> QTY["qty = o.qty or floor(capital / fill)"]
    QTY --> OPEN["EquityPosition(direction, entry, qty)"]
    OPEN --> LOOP
    FLAT -->|no| MG["manage → Exit"]
    MG --> KIND{"ex.kind"}
    KIND -->|market| M["fill at next signal bar open"]
    KIND -->|level| L["walk 1-min bars, resolve_level_exit"]
    M --> LOOP
    L --> LOOP
    LOOP --> EOD["EOD square-off at last 1-min close (MIS)"]
```

Constraints in v1: **one order per bar** and **no partial exits**
(`ex.fraction != 1.0` raises). `entry_delay_bars` fills N extra strategy bars late
for latency stress.

## 3. Fills — direction-aware adverse slippage

Cash has a direction, not a side. Slippage is applied adversely to the fill price
by direction:

```mermaid
flowchart LR
    subgraph Entry
        LE["long: raw * (1 + slip)"]
        SE["short: raw * (1 - slip)"]
    end
    subgraph Exit
        LX["long: raw * (1 - slip)"]
        SX["short: raw * (1 + slip)"]
    end
```

`_eff_slip` adds optional range-scaled friction: `slip += k * (high-low)/mid` at
the fill bar (`slippage_range_k`, default 0 = flat historical). Base slip default
is **0.0005** (~HDFCBANK cash spread) — NOT the options 0.25%.

## 4. `EquityOrder` / `EquityPosition` / `EquityCostModel`

```mermaid
classDiagram
    class EquityOrder {
        direction: str  long|short
        capital: float  = 0.0
        qty: Optional~int~
    }
    class EquityPosition {
        direction: str
        entry: float
        qty: int
        +close(exit, cost_model, reason) Trade
    }
    class EquityCostModel {
        brokerage_pct=0.0003, brokerage_cap=20
        stt_sell=0.00025, exchange=0.0000297
        sebi, stamp_buy, gst
        +total(buy_value, sell_value) float
    }
    EquityPosition ..> Trade : close() → (side LONG/SHORT, strike 0)
    EquityPosition ..> EquityCostModel
```

- **Sizing** defaults to fixed **capital** per trade (`qty = floor(capital / fill)`)
  so capital-at-risk stays constant as the stock's price level drifts over a
  multi-year backtest. A fixed `qty` overrides it (rupee risk then drifts — the
  caller's choice).
- **`EquityCostModel`** is the Zerodha cash-MIS card: percentage brokerage with a
  ₹20 cap per order, sell-side-only STT at 0.025%, NSE cash exchange rate. It
  shares only the `.total(...)` shape with the F&O `CostModel`, no logic.
- **`EquityPosition.close`** books long (buy entry, sell exit) or short (sell
  entry, buy exit) into the shared `Trade`.

## 5. Data ingestion — `kite_loader`

The only component that touches the Kite network API. Everything downstream reads
its cached parquet output (deterministic, offline-reproducible). The backtest
never calls Kite.

```mermaid
flowchart TB
    FM["fetch_months(fetch_fn, symbol, token, start, end)"] --> MR["month_range: calendar-month windows"]
    MR --> EACH{"for each month lo..hi"}
    EACH --> SKIP{"cached AND finalized (hi < today)<br/>AND coverage >= hi?"}
    SKIP -->|yes| NEXT["skip"]
    SKIP -->|no| FETCH["_fetch_retry (exp backoff, throttle)"]
    FETCH --> CLEAN["_normalize + clean<br/>(dedupe, drop insane bars)"]
    CLEAN --> EMPTY{"0 bars?"}
    EMPTY -->|yes| WARN["warn, DO NOT write<br/>(never freeze a gap)"]
    EMPTY -->|no| WRITE["write {symbol}/{YYYY-MM}.parquet<br/>+ update _coverage.json (atomic)"]
    WRITE --> NEXT
    NEXT --> EACH
```

Design points:

- **Chunked per calendar month** (≤31 days) — Kite caps minute candles at ~60
  days/request and rate-limits ~3 req/s. Throttle + exponential-backoff retry.
- **Resumable** via a `_coverage.json` manifest recording the end-date each month
  was fetched *through*. A finalized month with complete coverage is skipped; the
  **current month is always refetched** (it may have gained days). A month first
  cached mid-month (partial) is re-fetched once it ends, so no permanent hole.
- **Never persist an empty fetch** — an empty result is a transient API failure;
  writing it would cement a gap. Leave it retryable.
- **`clean`** is the integrity pass: dedupe/sort by `DateTime`, drop non-positive
  prices and bars where `High < max(O,C)` or `Low > min(O,C)`.
- **`load_cached`** concatenates + cleans all cached months into the 1-min frame
  the `EquityBacktest` (and `SpotSeries`) consume.
- Credentials come from the tradealgo `.env`; `kiteconnect`/`dotenv` are imported
  lazily inside `make_kite`, so tests and the backtest need neither installed.

## 6. Gap router — `gap_features` + `nifty_gap`

Pure gap-classification logic (no pandas, no I/O), shared by
`strategies/gap_router_equity.py`, `analysis/hdfc_gapandgo.py`, and the unit tests
so the classification can never drift.

```mermaid
flowchart TB
    OPEN["09:15 decide_open(gap, ngap, thr)"] --> Z{"|gap| <= gap_thr?"}
    Z -->|yes| NONE1["Decision('none')"]
    Z -->|no| SMALL{"small (<= big_thr) AND not NIFTY-aligned?"}
    SMALL -->|yes| FADE1["Decision('fade', against gap)"]
    SMALL -->|no| DEFER["Decision('defer') → wait for 09:30"]

    DEFER --> D930["09:30 decide_0930(gap, ngap, drive, thr)"]
    D930 --> DR{"drive <= 0? (reverting)"}
    DR -->|yes| FADE2["Decision('fade', against gap)"]
    DR -->|no| BIG{"drive >= drive_thr AND (big OR NIFTY-aligned)?"}
    BIG -->|yes| GO["Decision('go', with gap)"]
    BIG -->|no| NONE2["Decision('none')"]
```

- **Two checkpoints**: `decide_open` (09:15) fades small unconfirmed gaps
  immediately and defers the rest; `decide_0930` resolves deferred gaps —
  reverting → fade, driving hard and (big or NIFTY-aligned) → go, else stand
  aside.
- **Helpers**: `compute_gap`, `signed_drive` (drive measured in the gap's
  direction), `aligned` (NIFTY gapped the same way and enough), `fade_side` /
  `go_side`.
- **`NiftyGapProvider`** (`nifty_gap.py`) isolates the one external input — the
  per-date NIFTY gap from `data_index/NIFTY/gap.parquet`. Missing dates return
  `None`, which the router treats as "NIFTY not aligned."

## 7. `EquityKnobs`

```python
EquityKnobs(slippage_pct=0.0005, timeframe="5min",
            min_bars_per_day=300, max_overnight_gap=0.15,
            slippage_range_k=0.0, entry_delay_bars=0)
```

| Knob | Meaning |
|---|---|
| `slippage_pct` | base adverse slip (~cash spread, not options 0.25%) |
| `timeframe` | signal-bar size / fill offset |
| `min_bars_per_day` | skip partial/Muhurat sessions |
| `max_overnight_gap` | skip unadjusted corporate-action days |
| `slippage_range_k` | range-scaled friction stress (0 = off) |
| `entry_delay_bars` | latency stress: fill N extra bars late |

## 8. Design rationale

| Decision | Why |
|---|---|
| Never import the options driver | zero risk to the proven path; parallel evolution |
| Duplicate ~6 context methods | no optional `chain`, no shared mutable surface |
| One `SpotSeries` = signal + fill feed | cash has no derivative layer |
| Capital sizing by default | constant risk across multi-year price drift |
| Independent `EquityCostModel` | cash MIS charges differ entirely from F&O |
| Cache-then-backtest split | deterministic, offline-reproducible; API isolated |
| Never write an empty fetch | a transient failure must not freeze a data gap |
| Pure gap logic in one module | classification can't drift across callers |
