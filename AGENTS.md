# NIFTY Intraday Option-Buying Backtest Engine

A code-first backtest engine for intraday NIFTY options. Signals fire on the **real
index spot**; fills come from **real Data-v3 option parquets**. Built TDD-first and
validated by trade-by-trade reconciliation (582/582 legs vs a reference strategy over
5 years). This document covers the **engine** and the **data/builder layer** — not the
individual strategies.

> Two hard rules the whole engine is built around:
> 1. **Anti-look-ahead** — a strategy can only ever read data `<= now`. Signal on bar
>    `t`, fill at `t + timeframe` (the next bar's open). This is structural, not advisory.
> 2. **Honesty** — orders are never silently dropped (they return a Fill *or* a typed
>    Rejection); costs and slippage are always applied; report NET, not flattering ratios.

---

## Data / Builder layer (`engine/data/`, `engine/execution/`, `engine/instruments.py`)

### The store
Real NIFTY Data-v3 store (~84 GB), scattered across many `Data N` folders, Hive-partitioned:

```
<root>/<Data N>/Data-v3/instrument=NIFTY50/OPTIONS/expiry=YYYY-MM-DD/options_data.parquet
<root>/<Data N>/Data-v3/instrument=NIFTY50/INDEX/index_data.parquet
```

- **Root path:** `/Users/gokul/Desktop/tradealgo/data nifty ` — **note the trailing space** (the
  `build strategies ` working dir also has one). Always quote these paths.
- **Options:** per-minute long format, one row per `(DateTime, StrikePrice)`, both call and
  put columns on the same row. Streamed one expiry-parquet at a time (never all in memory).
- **Index:** per-minute NIFTY50 spot OHLC, ~578k rows spanning **2019-12 → ~2026-02**
  (the spot calendar ends ~Feb 2026 — there is no live/recent data in the store).

### `Catalog` (`catalog.py`)
Walks every `Data N` folder once, maps `expiry -> parquet path` (first occurrence wins for
dupes). `expiries()`, `path_for(expiry)`.

### `SpotSeries` (`spot.py`) — the signal underlying
Look-ahead-safe spot OHLC. Strategies signal on this (the real index), **never** on the
option-derived Forward (whose intraday path-noise makes false triggers).
- `SpotSeries.from_index(root, timeframe="5min", label="left")` — discover INDEX parquets,
  tz-normalize to naive IST, dedupe by `DateTime`, resample **START-labelled** (a bar `B`
  covers `[B, B+timeframe)`; matches the `+timeframe` fill contract).
- `bar_at(dt)` (O(1)), `price_at(dt)` (close at-or-before), `history(dt)` (bars `<= dt`),
  `timestamps()`, `close_before(dt)` (prior session close — gap gates),
  `prev_day_hlc(dt)` → `(high, low, close)` of the prior session (e.g. CPR / pivot inputs).

### `Chain` / `OptionChainSnapshot` / `OptionQuote` (`chain.py`)
- `Chain(df).snapshot_at(dt)` → snapshot at one minute.
- `snapshot.strikes()`, `snapshot.call(strike)` / `snapshot.put(strike)` → `OptionQuote`.
- `OptionQuote` fields: `strike, open, high, low, close, volume, oi, delta, is_traded`,
  plus optional pass-through Greeks `iv, gamma, vega, theta, rho` (chain-level IV/Gamma/Vega;
  per-leg Theta/Rho). Optional Greeks are `None` when the source column is absent/NaN — never
  recomputed.

### `MarketData` (`market.py`)
`MarketData(catalog, spot, min_dte=3, reader=pd.read_parquet)` ties spot + chains together.
- `trading_days(start, end)` — from the spot calendar.
- `expiry_for(day)` — nearest expiry with `(expiry - day).days >= min_dte`. **`min_dte`
  materially changes results and reconciliation** (e.g. a DTE≥0 reference needs `min_dte=0`).
- `stream(start, end)` — batched iterator yielding `(expiry, {day: Chain})`, loading each
  expiry parquet exactly once (skips missing expiries).

### `instruments.py` — lot size
`lot_size_for(date)` via `NIFTY_LOT_SCHEDULE` (date-keyed): **75** until **2025-12-30**, then
**65**. Size is engine-owned — strategies express intent in `lots`, never in raw qty.

### `CostModel` (`execution/costs.py`) — India F&O costs
`total(buy_value, sell_value, n_orders)` = brokerage (₹20/order) + STT (0.1% sell) + exchange
(0.03503% both sides) + SEBI + stamp (buy) + GST (18% on brokerage+exch+sebi). **Slippage is
NOT here** — it's applied adversely to the fill price by the backtest (see Knobs).

---

## Engine layer (`engine/strategy/`, `engine/position/`, `engine/backtest.py`)

### Strategy interface (`strategy/base.py`)
Subclass `Strategy` and implement the hooks (all optional except `on_bar`):

```python
class MyStrategy(Strategy):
    def on_day_start(self, ctx): ...      # reset intraday state (engine is intraday-only)
    def on_bar(self, ctx) -> list[Order]: # when FLAT: return Order(s) to enter, or []
    def manage(self, ctx, position):      # when IN a position: return an Exit or None (hold)
    def on_eod(self, ctx) -> list: ...    # optional end-of-day hook
```

`BarContext` is the **only** interface a strategy gets each minute (it truncates to `<= now`,
so future bars are structurally unreadable):
- `ctx.now`, `ctx.spot`, `ctx.spot_bar`, `ctx.spot_history()`
- `ctx.prev_close()`, `ctx.prev_day_hlc()`
- `ctx.opening_range(end_time)`, `ctx.snapshot()`, `ctx.atm_strike(side)`

### Orders & fills (`execution/model.py`)
`Order(side="CALL"|"PUT", strike, lots=1, qty=None)`. Entry sequence: signal at `t` (chain read
as-of `t`) → tradeability + liquidity gate → **fill at `t+timeframe` open** → adverse slippage.

### Exits (`strategy/exit.py`) — the accuracy-critical mechanics
`manage()` returns one of:
- `Exit.market(reason, fraction=1.0)` — discretionary; fills at the next 1-min option open.
- `Exit.at(level, trigger, reason, fraction=1.0)` — a resting **level** order resolved intrabar
  on option bars. `trigger="fall"` = stop, `"rise"` = target. Honest gap-through: if the bar
  opens past the level, fill at the (worse-for-stop / better-for-target) open. Supports partial
  `fraction` → **partial scale-out at a price target**.
- `Exit.bracket(stop, target, stop_reason, target_reason, stop_fraction=1.0, target_fraction=1.0)`
  — a premium stop + target resolved together intrabar (stop checked first, conservative).
  **Per-side fractions** allow e.g. a full-size stop but a partial book at target.
- `Exit.trail(gap, reason, fraction=1.0)` — trailing stop on the option **premium**: exit when
  premium falls `gap` points below its running peak (peak tracked intrabar from entry).

`fraction < 1.0` books part of the position and returns the remainder (carry-forward) — the
basis for multi-stage scale-outs.

### Position & Trade (`position/manager.py`)
`Position(side, strike, entry, qty)` with `close(exit, cost_model, qty=None, ...)`.
`Trade` (frozen): `side, strike, qty, entry, exit, gross, cost, net, reason, date, entry_dt,
exit_dt`.

### Backtest driver (`backtest.py`)
`Backtest(market, cost_model, knobs).run(strategy, start, end) -> Result`.
Per day: `on_day_start` → iterate spot bars → when flat, `on_bar` may emit an Order (filled at
`b+timeframe`) → while in a position, `manage` may return an Exit → EOD square-off at the day's
last option bar.

**`Knobs` (the sensitivity dials):**
- `slippage_pct` (default 0.0) — adverse % applied to every fill (entry `×(1+s)`, exit `×(1-s)`).
- `timeframe` (default `"5min"`) — signal/fill bar size.
- `fill_price` (default `"open"`) — `"open"` (principled next-open) or `"close"` (matches some
  legacy scripts).

**`Result` metrics:** `net()`, `win_rate()`, `profit_factor()`, `tail_excl(n)` (net minus the
top-n winners — tail-dependence check), `by_year()`, `t_stat()`.

---

## Conventions & gotchas (read before trusting a number)

- **Trust NET.** `win_rate()` and `t_stat()` are **leg-level** — a scale-out logs the book leg
  and the trail leg as separate "trades," so a winner can count as 2 legs and a stop as 1. This
  inflates win-rate / t-stat. Always reconcile against `net()`.
- **Slippage = spread proxy, not market impact.** Order sizes here are tiny (a few lots = a
  rounding-error fraction of strike volume/OI), so impact ≈ 0. `slippage_pct` stands in for the
  **bid-ask spread** you cross — quantity-independent. Liquid near-ATM strikes ⇒ ~1-5 ticks
  (~0.05-0.25%); illiquid strikes ⇒ wider. Calibrate it to the strikes actually traded.
- **`min_dte` drives expiry selection** and is the usual cause of reconciliation mismatches.
- **START-labelled bars + fill at `b+timeframe`** is the canonical timing; don't reintroduce
  look-ahead by reading `now+1`.
- **Paths have trailing spaces** (`data nifty ` / `build strategies `) — always quote them.
- **Spot ends ~Feb 2026** — backtests can't run past the spot calendar.

---

## Running & testing

```bash
# from the project root (quote the trailing-space path)
PYTHONPATH="$PWD" python3 -m pytest -q          # full suite (TDD: red → green → refactor)
PYTHONPATH="$PWD" python3 analysis/<runner>.py  # an analysis/backtest runner
```

- **Tests:** `tests/unit/` (per-module), `tests/golden/` (end-to-end driver), `tests/conftest.py`
  (synthetic chain fixtures mirroring the real Data-v3 schema). New behaviour is TDD'd: write the
  failing test, watch it fail, then implement.
- **Analysis runners** live in `analysis/` and load the store once (it's large — avoid re-loading;
  run long jobs in the background).

## Minimal strategy + run skeleton

```python
from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit

class Example(Strategy):
    def on_day_start(self, ctx):
        self.done = False
    def on_bar(self, ctx):
        if not self.done and <entry condition on ctx.spot_bar>:
            self.done = True
            return [Order(side="CALL", strike=ctx.atm_strike("CALL"), lots=1)]
        return []
    def manage(self, ctx, pos):
        return Exit.trail(12, "ptrail")   # e.g. trail the premium by 12 pts

# Backtest(MarketData(Catalog(ROOT), SpotSeries.from_index(ROOT)), CostModel(),
#          Knobs(slippage_pct=0.0025)).run(Example(), start, end)
```
