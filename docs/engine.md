# Engine Reference

Complete reference for the NIFTY options backtesting engine (`engine/`). Written against the codebase as of 2026-09-25.

---

## Architecture overview

```
engine/
├── backtest.py          — options backtest driver
├── instruments.py       — NIFTY lot-size schedule + LotTable
├── indicators.py        — EMA, RSI, Stochastic, Williams %R
├── data/
│   ├── catalog.py       — per-expiry parquet index + LotTable loader
│   ├── chain.py         — OptionChainSnapshot + OptionQuote
│   ├── market.py        — MarketData facade (expiry selection, calendar, streaming)
│   ├── spot.py          — SpotSeries (look-ahead-safe 1-min/5-min OHLC)
│   ├── loader.py        — raw parquet loader utilities
│   └── integrity.py     — scoping invariants (hard-fail guards)
├── execution/
│   ├── model.py         — Order (strategy intent)
│   └── costs.py         — CostModel (India F&O transaction costs)
├── strategy/
│   ├── base.py          — Strategy base class + BarContext
│   └── exit.py          — Exit, Bracket, TwoStage primitives
├── position/
│   └── manager.py       — Position, SpreadPosition, SpreadLeg, Trade
├── equity/
│   ├── backtest.py      — cash-equity intraday driver
│   ├── context.py       — EquityBarContext
│   ├── costs.py         — EquityCostModel
│   ├── model.py         — equity Order/Trade types
│   ├── position.py      — EquityPosition
│   ├── gap_features.py  — gap-feature extraction
│   ├── nifty_gap.py     — NIFTY gap strategy helpers
│   └── kite_loader.py   — Kite CSV ingest
├── regime/
│   ├── classifier.py    — two-axis regime labeler (MA200 + vol)
│   ├── features.py      — vol-surface features (skew, term, accel, VRP)
│   ├── breadth.py       — NIFTY-50 breadth signals
│   ├── contract.py      — regime type definitions
│   └── journal.py       — regime episode log
├── signals/             — (reserved, not yet populated)
├── metrics/             — (reserved, not yet populated)
└── live/
    └── fyers_gateway.py — Fyers v3 order gateway
```

---

## Options backtest driver

**`engine/backtest.py` — `Backtest`**

### Per-day lifecycle

```
on_day_start(ctx)
  └─ for each signal bar (5-min default):
       if flat:  on_bar(ctx) → [Order]
                   └─ fill at B + timeframe (next open)
       if open:  manage(ctx, pos) → Exit | None
                   └─ intrabar resolution on 1-min option bars
  └─ EOD: square off any open position at last option bar close
```

### Knobs (friction/fidelity dials)

| Knob | Default | What it does |
|---|---|---|
| `slippage_pct` | `0.0` | Flat adverse fill: entry × (1+s), exit × (1−s) |
| `spread_rs` | `0.0` | Fixed ₹ bid-ask spread per fill |
| `spread_otm_tiers` | `()` | OTM-distance-based spread: `((max_otm_pts, spread_rs), ...)` sorted ascending. Overrides `spread_rs` when set. |
| `slippage_range_k` | `0.0` | Extra slip = k × (bar high − low) / mid. Fast bars cost more. |
| `thin_exit_mult` | `1.0` | Multiply slip when exit-bar volume < `min_volume_mult × qty`. |
| `fill_price` | `'open'` | Fill at next-bar open (`'open'`) or close (`'close'`). |
| `min_volume_mult` | `0.0` | Entry liquidity floor: reject if signal-bar option volume < mult × qty. `0` = off. |
| `intrabar` | `'mixed'` | Intrabar event ordering. Two independent dimensions — see truth table below. Run `'optimistic'` vs `'pessimistic'` to bracket the full error band. |
| `timeframe` | `'5min'` | Signal bar width. |

**`intrabar` truth table** — the three modes affect two independent mechanisms, not one:

| Mode | `Bracket.resolve()` — tie when both stop and target are inside the bar | Trail / two_stage — when does this bar's high fold into the running peak? |
|---|---|---|
| `'optimistic'` | target wins | **before** the low check (same bar) |
| `'mixed'` | **stop wins** | **before** the low check (same bar) |
| `'pessimistic'` | **stop wins** | **after** the low check (prior bars' peak only) |

`'mixed'` is a deliberate hybrid: conservative on bracket ties, optimistic on trail peak updates. For bracket exits, `'mixed'` and `'pessimistic'` are identical (`optimistic=False` passed to `Bracket.resolve()`). For trail exits, `'mixed'` and `'optimistic'` are identical (`pess=False` in the peak-update branch). The only way to be fully conservative across both mechanisms is `'pessimistic'`.

### Result

```python
result.net()           # total net P&L (Rs)
result.win_rate()      # fraction of trades with net > 0
result.profit_factor() # gross wins / gross losses
result.tail_excl(n)    # net with top-n winners removed
result.by_year()       # {year: net Rs}
result.t_stat()        # t-statistic of mean per-trade return on premium deployed
result.trades          # List[Trade]
result.missed_fills    # count of orders that couldn't be filled
result.missed_fill_log # [{"day":, "reason":, "strike":, "side":}]
result.carry_forwards  # count of option-price gaps carried forward
```

### Position types

**Single-leg** (`Position`): long call or long put. Partial scale-outs via `fraction`. All P&L is `(exit − entry) × qty` — premium direction only.

**Multi-leg** (`SpreadPosition`): vertical spreads. Each leg is a `SpreadLeg(action, side, strike, entry, qty)`. Naked-short guard enforced at open: if net sold qty exceeds bought qty on any side, the engine raises `ValueError` immediately. Long spreads (debit verticals) are the only modeled structure.

### ITM exercise STT

When a long option is still open at expiry, the engine computes incremental exercise STT (0.15% of intrinsic value at 15:30 settlement) and adds it as `extra_cost` on the EOD close trade.

---

## Knobs — deployed CPR config (reference)

```python
Knobs(fill_price="open", slippage_pct=0.0025)
```

For stress testing:

```python
Knobs(
    fill_price="open",
    slippage_pct=0.0025,
    spread_otm_tiers=((50, 8.0), (150, 5.0)),   # ATM Rs8, mid Rs5
    intrabar="pessimistic",
    min_volume_mult=10.0,
    thin_exit_mult=2.0,
    slippage_range_k=0.5,
)
```

---

## Exit primitives

All returned from `strategy.manage(ctx, pos)`. Resolved by the engine intrabar on 1-min option bars.

### `Exit.market(reason, fraction=1.0)`
Fill at the next 1-min option bar open. Full or partial (`fraction < 1`).

### `Exit.at(level, trigger, reason, fraction)`
Resting premium-level order. `trigger='fall'` = stop (sell when premium drops to level). `trigger='rise'` = target.
- Gap-through: if bar opens past the level, fill is bar open (not the level).
- Returns `(fill_price, hit)` via `resolve_level_exit`.

### `Exit.spot_at(level, trigger, reason, fraction)`
Spot-triggered exit resolved on 1-min **spot** bars. The first minute whose range crosses `level` fires the exit; the option fills at the NEXT 1-min option open (one minute of honest latency). Use instead of checking `ctx.spot` in `manage()`.

### `Exit.trail(gap, reason, fraction, arm=None)`
Premium trailing stop. Engine tracks the running peak intrabar (from entry fill). Fires when `low ≤ peak − gap`.
- `arm`: trail stays dormant until peak first reaches this premium level. Bar highs still feed the peak while dormant.
- Gap-through: if bar open < stop, fill is bar open.
- `pessimistic` mode checks the low against the **prior** bar's peak (more conservative).

### `Exit.bracket(stop, target, stop_reason, target_reason, stop_fraction, target_fraction)`
Stop + target in one object. Resolved together on each 1-min bar. When both sides are inside the bar's range:
- `intrabar='optimistic'`: target wins ties.
- `intrabar='mixed'` or `'pessimistic'`: stop wins ties (both pass `optimistic=False` to `Bracket.resolve()`).
- Per-side fractions enable partial scale-out at target with full stop.
- `bracket.consume(reason)` moves the filled side out of range, keeping the other resting.

### `TwoStage(target_pts, trail_gap, fraction, stop_pts=None)`
Engine-owned two-stage policy. Stage 1: a `Bracket` (optional `stop_pts` loss-cap + partial target). Once the scale-out books, Stage 2: trail the carried lots by `trail_gap`.

Two dispatch modes:
- **Legacy** (bar-granular): `manage()` returns `exit_for(pos)` each bar. Trail activates on the next strategy bar after scale-out.
- **Engine-owned** (in-window): `manage()` returns the `TwoStage` object itself (`kind='two_stage'`). Trail activates within the same 1-min window as scale-out, seeding peak at the scale-out fill. Higher fidelity; re-validate recorded numbers before switching.

`scaled(pos)` → `True` once stage-1 partial has booked. `reset()` on each new trade.

---

## Strategy API

```python
class MyStrategy(Strategy):
    def on_day_start(self, ctx: BarContext) -> None:
        # reset state; compute day-level filters (CPR levels, IV check, etc.)

    def on_bar(self, ctx: BarContext) -> List[Order]:
        # return [Order(...)] to enter, [] to stay flat

    def manage(self, ctx: BarContext, pos) -> Exit | None:
        # return an Exit to act; None to hold

    def on_fill(self, order: Order) -> None:
        # engine confirmed the fill (set self.entered here, not in on_bar)

    def on_order_rejected(self, order: Order, reason: str) -> None:
        # reason: "no_quote" | "volume" | "no_quote_spread"
```

### `BarContext` — what strategies see each bar

| Attribute / Method | Returns |
|---|---|
| `ctx.now` | Current bar datetime |
| `ctx.spot` | Current spot close (float) |
| `ctx.spot_bar` | `SpotBar(dt, open, high, low, close)` |
| `ctx.spot_history()` | All spot bars up to now (no future) |
| `ctx.recent(n)` | Last n spot bars as a DataFrame |
| `ctx.prev_close()` | Prior session's closing spot |
| `ctx.prev_day_hlc()` | `(high, low, close)` of prior session |
| `ctx.opening_range(end_time)` | `(high, low)` of session start → end_time |
| `ctx.snapshot()` | `OptionChainSnapshot` at now |
| `ctx.atm_strike(side)` | Nearest traded strike to spot |

### `Order` — strategy intent

```python
Order(side="CALL", strike=24000, lots=6)              # buy 6 lots
Order(side="PUT",  strike=23500, lots=6, action="sell") # short leg of a spread
Order(side="CALL", strike=24000, qty=450)              # explicit qty (skips lot-size lookup)
```

---

## Data layer

### `MarketData`

The unified facade. Selects the correct expiry per trading day from the real EXPIRIES calendar (handles the Thu→Tue change ~Sep 2025). Streams each expiry parquet exactly once.

```python
md = MarketData(catalog, spot, min_dte=3)
md.trading_days(start, end)      # eligible days (quarantined days excluded)
md.expiry_for(day)               # nearest expiry with DTE >= min_dte
md.lot_size(expiry)              # authoritative lot for that contract
md.stream(start, end)            # → Iterator[(expiry, {day: Chain})]
md.last_expiry_date()            # last available expiry in catalog
md.quarantined_between(s, e)     # count of bad-data days excluded
```

`DataFreshnessError` is raised if `end > last_expiry_date()`. This is a hard fail — set `end` to the last expiry date or refresh data.

### `SpotSeries`

Look-ahead-safe wrapper around 1-min (or resampled) spot OHLC. `.price_at(dt)` and `.bar_at(dt)` only return data ≤ dt. `SpotSeries.from_index(root, "5min")` loads the full NIFTY index from the data directory.

### `OptionChainSnapshot`

Point-in-time chain slice. Exposed via `ctx.snapshot()`.

```python
snap = ctx.snapshot()
snap.strikes()          # sorted list of available strikes
snap.call(strike)       # OptionQuote
snap.put(strike)        # OptionQuote
snap.forward            # chain-level forward price (float | None)
```

`OptionQuote` fields: `open, high, low, close, volume, oi, delta, is_traded, iv, gamma, vega, theta, rho`. Greeks are optional (None when absent from source).

### `Catalog`

Index over per-expiry parquet files. `catalog.expiries()` → list. `catalog.path_for(isodate)` → file path. `catalog.lot_table()` → `LotTable`.

### `LotTable` and lot-size schedule

NIFTY lot history is non-monotone: 75 → 50 → 25 → 75 → 65. The authoritative source is the per-expiry `LotSize` from EXPIRIES data, loaded via `LotTable`. The day-keyed schedule (`lot_size_for(day)`) is an approximation kept as a fallback.

| Effective date | Lot size |
|---|---|
| 2020-01-01 | 75 |
| 2021-05-06 | 50 |
| 2024-05-09 | 25 |
| 2025-01-02 | 75 |
| 2026-01-06 | 65 |

Known data anomaly: 2025-01-30 has `lot=25` in EXPIRIES; `LotTable` auto-corrects to 75 (isolated spike, neighbors both 75).

### Data integrity

`engine/data/integrity.py` enforces hard-fail scoping guards on chain DataFrames:
- Unique `(DateTime, StrikePrice)` per snapshot (one row per strike per minute)
- Asserted in `OptionChainSnapshot.__init__` on every snapshot

Quarantined days (written by `analysis/data_integrity.py`) are excluded from `trading_days()` and can never generate a trade. Count them with `quarantined_between()`.

---

## Cost model

**`engine/execution/costs.py` — `CostModel`**

India F&O option-buying round-trip costs:

| Charge | Rate | Applied to |
|---|---|---|
| Brokerage | Rs 20 flat | per executed order |
| STT | 0.10% | sell-side premium turnover |
| Exchange | 0.03503% | both sides |
| SEBI | 0.0001% | both sides |
| Stamp duty | 0.003% | buy side only |
| GST | 18% | on brokerage + exchange + SEBI |
| Exercise STT | 0.15% | intrinsic value at expiry (replaces sell STT) |

```python
CostModel().total(buy_value, sell_value, n_orders)  # full round-trip
CostModel().exercise_stt(intrinsic_value)           # ITM settlement cost
```

---

## Indicators

**`engine/indicators.py`**

Pure pandas functions, look-ahead-safe (rolling/ewm backward windows only).

```python
from engine.indicators import ema, rsi, stoch_k, williams_r

ema(close, span=20)
rsi(close, period=14)            # Wilder smoothing, [0, 100]
stoch_k(high, low, close,        # slow %K, [0, 100]
        period=14, smooth_k=3)
williams_r(high, low, close,     # [-100, 0]; -100 = at period low
           period=14)
```

All return `pd.Series` aligned to the input index.

---

## Equity engine

**`engine/equity/backtest.py` — `EquityBacktest`**

Same lifecycle as the options engine but fills from the stock's own 1-min bars. No chain, no premium layer.

```python
bt = EquityBacktest(one_min_df, EquityCostModel(), EquityKnobs())
result = bt.run(strategy, start, end)
bt.skipped_days  # [(date, reason)] quality log
```

### `EquityKnobs`

| Knob | Default | What it does |
|---|---|---|
| `slippage_pct` | `0.0005` | ~cash equity spread |
| `timeframe` | `'5min'` | Signal bar width |
| `min_bars_per_day` | `300` | Skip sessions with fewer than this many 1-min bars (partial/Muhurat) |
| `max_overnight_gap` | `0.15` | Skip days where overnight gap > 15% (unadjusted corporate actions) |
| `slippage_range_k` | `0.0` | Range-scaled friction (same as options engine) |
| `entry_delay_bars` | `0` | Fill N extra strategy bars late (latency stress) |

`EquityBarContext` mirrors `BarContext` but without the chain layer.

---

## Regime modules

**`engine/regime/`** — studied as research, not wired into production.

- `classifier.py`: two-axis daily labeler — absolute MA200 direction (bullish/bearish/transitional) × relative volatility (calm/normal/storm). Reuses `SpotSeries`. Used in `analysis/regime_finder.py`.
- `features.py`: vol-surface features — skew, term structure, acceleration, VRP (implied vs realized vol).
- `breadth.py`: NIFTY-50 breadth signals (advance-decline, % above MA).
- `contract.py`: `RegimeLabel` type definitions.
- `journal.py`: episode log (continuous regime runs with start/end dates).

Both regime detectors studied were NO-GO: theta/gamma labeler (only 4 stable-bear episodes in 6.5y, 32 vs 1 episode parity problem); vol-surface features (skew/term REAL but REDUNDANT with VIX).

---

## Live gateway

**`engine/live/fyers_gateway.py`**

Fyers v3 API integration for live NIFTY option order placement. Credentials from `.env` (`FYERS_APP_ID`, `FYERS_ACCESS_TOKEN`). Token expires daily — run `python3 fyers_auth.py` each morning.

Symbol construction:

```python
# Weekly: NSE:NIFTY{YY}{M_CODE}{DD}{STRIKE}{CE|PE}
# Monthly: NSE:NIFTY{YY}{MMM}{STRIKE}{CE|PE}
nifty_symbol(strike=24000, expiry=date(2025, 12, 18), option_type="CE", is_monthly=False)
# → "NSE:NIFTY25D1824000CE"
```

Month codes for weekly: `1-9` = Jan-Sep, `O` = Oct, `N` = Nov, `D` = Dec.

---

## Strategies

| File | Instrument | Status |
|---|---|---|
| `cpr_breakout.py` | NIFTY options | **Deployed candidate** — CPR breakout, deep-ITM, IV≤0.12 filter |
| `g12_ce.py` | NIFTY options | Studied |
| `ema5_itm.py` | NIFTY options | Studied |
| `fib_retracement.py` | NIFTY options | NO-GO |
| `gap_fill_fade.py` | NIFTY options | NO-GO |
| `gap_down_put.py` | NIFTY options | Studied |
| `orb_spread.py` | NIFTY options | Studied |
| `cpr_breakout_equity.py` | Equity | Studied |
| `gap_drive_short_equity.py` | Equity (ADANIENT) | Passed stress gate; pending live fill audit |
| `gap_router_equity.py` | Equity | NO-GO (OOS PF 0.43) |
| `orb_short_equity.py` | Equity | NO-GO |
| `ema_crossover.py` | Equity | Studied |
| `atr_roc_buy.py` | Equity | Studied |

---

## What the engine cannot do (current limits)

- **Overnight / multi-day carry**: engine is strictly intraday; positions square off at EOD.
- **Net-short options**: naked short legs raise `ValueError`. Only covered/debit structures.
- **Equity short**: `EquityBacktest` has no short-selling model (no borrow cost, no short position type).
- **Portfolio / multi-position**: one position open at a time per strategy instance.
- **Live execution loop**: `fyers_gateway.py` places single orders but there is no live bar-feed loop wiring it to a running strategy.
