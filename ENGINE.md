# Nifty Options Backtesting Engine

Documentation of the backtesting engine in this project (`/Users/gokul/Desktop/build strategies `).

## Overview

A from-scratch, **option-buying** backtesting engine for NIFTY (NSE index) intraday options, covering Jan 2020 – Feb 2026. It was built test-first (TDD, pytest) with a single governing requirement: **accuracy is the #1 priority — the backtest must be trustworthy, not merely plausible.**

The engine's defining property: it reproduces a known reference strategy (G12-CE) **582 / 582 trades exact, trade-by-trade**, over 2020–2026 on real data — not an aggregate-tolerance match, an *identity*. Everything downstream inherits that trust.

Unlike documentation-only or hand-tuned frameworks, every accuracy claim here is backed by a golden test in `tests/`.

### One architectural pattern (not two)

There is exactly **one** way to write a strategy: subclass `Strategy`, receive a look-ahead-safe `BarContext` each bar, return `Order`/`Exit` objects. A single `Backtest` driver runs every strategy — naked single-leg, multi-leg credit/debit spreads, partial scale-outs, intrabar premium stops/targets/trails — through the same proven loop. The generic abstraction was made to work for intraday custom-exit strategies (CPR, G12, EMA, spreads all run through it), rather than falling back to per-strategy bespoke loops.

## Core Principle: spot decides *when*, the chain decides *at what price*

This is the engine's central accuracy decision.

- **Signals fire off the real INDEX spot** (`SpotSeries`), never the option-chain-implied `Forward`. Forward's intraday path-noise produces false and missed triggers — proven empirically: G12-CE *flipped sign* when signalled off Forward vs. real spot.
- **Fills come from the Data-v3 option chain** at the real per-minute option price.

So a strategy reads spot OHLC to decide direction/timing, and the engine prices the actual deep-ITM/ATM/OTM option from the chain. The two data sources are never conflated.

## Data Layer

**Location:** `<ROOT>/*/Data-v3/instrument=NIFTY50/...` where `ROOT = /Users/gokul/Desktop/tradealgo/data nifty ` (scattered across multiple `Data N` folders; the loader globs and dedupes them — no consolidation step required).

Two data sources, deliberately separate:

| Source | File pattern | Used for |
|---|---|---|
| **INDEX spot** (1-min OHLC, 2019-12 → 2026-02) | `.../INDEX/index_data.parquet` | signals + the trading-day calendar |
| **Data-v3 option chain** (per-expiry, ~328 weekly expiries, 2020-01 → 2026-03) | per-expiry parquet | fills + IV/Greeks + liquidity |

Option-chain columns: `DateTime, StrikePrice, ExpiryDate, Forward, DaysToExpiry, IV, Gamma, Vega, Call/Put {Open,High,Low,Close,Volume,OI,Delta,Theta,Rho,IsTradedStrike}`. The engine reads IV/Greeks as optional pass-through (None if a column is absent — no Black-Scholes recompute).

### Key modules

| Module | Responsibility |
|---|---|
| `engine/data/spot.py` — `SpotSeries` | Look-ahead-safe spot OHLC. `from_index()` discovers → tz-normalizes (naive IST) → dedupes by minute → **START-labels** resampled bars (a bar labelled `B` covers `[B, B+timeframe)`). O(1) `bar_at`, plus `prev_day_hlc` (CPR inputs), `close_before` (gap gates), `window` (rolling indicators). Every accessor returns data *as of* the requested minute only. |
| `engine/data/chain.py` — `Chain` | Per-minute option-chain snapshots; `snapshot_at(dt)` → traded strikes, call/put quotes with price/IV/Greeks/volume/OI/`is_traded`. |
| `engine/data/market.py` — `MarketData` | The unified facade. Picks the right expiry **from the real partitions** (never a weekday rule — the expiry weekday changed Thu→Tue ~Sep 2025) via `expiry_for(day)` with a configurable `min_dte`. `stream()` loads each expiry parquet **exactly once**. |
| `engine/data/catalog.py` / `loader.py` | Expiry discovery + parquet path resolution. |
| `engine/instruments.py` — `lot_size_for` | **Date-keyed lot size**: 75 → 65 effective 2025-12-30. The engine owns this, not the strategy. A backtest that hardcodes one value is wrong across the boundary. |

## Execution & Costs

### `engine/execution/costs.py` — `CostModel`

The single place transaction costs are computed — India F&O option card, ported verbatim from validated production code and golden-tested by hand:

- Brokerage ₹20/order (flat), STT 0.1% on **sell-side** premium, exchange txn 0.03503% (both sides), SEBI 0.0001%, stamp 0.003% (buy side), GST 18% on (brokerage + exchange + SEBI).
- All rates are configurable, so a "stress" preset can dial them up.

**Slippage is not in the cost model** — it is applied adversely to the *fill price* inside the driver (`fill × (1 + slip)` on entry/buy, `fill × (1 − slip)` on exit/sell). Validated realistic value: **0.25%** (justified by a liquidity audit — the deep-ITM legs trade ~3.9M median strike day-volume; a 6-lot order ≈ 0.01% of volume, so market impact ≈ 0).

### Fill contract (the look-ahead guarantee)

A signal on bar `B` fills at **`B + timeframe`** (the next bar's option open by default). This is enforced structurally, not by convention. `Knobs.fill_price` selects `'open'` (principled next-open) or `'close'` (matches the legacy G12 scripts' convention, used only for reconciliation).

### Intrabar exit resolution (gap-through honesty)

Premium stops/targets/trails resolve against the option's **1-min high/low**, not just its close — so the engine *models* the overshoot that close-only samplers miss:

- **Stop** (`fall`): triggers when the option low ≤ level. Fill = level, unless the bar *opened* already below it → fill = the worse **open** (gap-down).
- **Target** (`rise`): mirror, fill = the better open on a gap-up.
- **Trail**: premium peak tracked intrabar from entry; exit when premium falls `gap` points below peak (gap-through to the worse open).

1-min granularity caps fidelity, and that cap is acknowledged rather than hidden.

## Position Layer — `engine/position/manager.py`

- **`Position`** — a long-premium holding. `close()` → a fully-costed `Trade`; P&L is `(exit − entry) × qty` for both calls and puts (buying either profits when *its own* premium rises). Supports **partial closes** (`qty=`) for scale-outs — the driver books a fraction and keeps the remainder.
- **`SpreadPosition` / `SpreadLeg`** — multi-leg (vertical) positions. Combined P&L = Σ per-leg, where a BUY leg profits on a premium rise and a SELL leg on a fall. All-or-none fill; each leg costed independently. This gives the engine first-class **credit/debit spread** support (so premium-*selling* strategies are testable on the same trusted engine, not just buying).

## Strategy API — `engine/strategy/`

A strategy is a `Strategy` subclass with these hooks:

```python
class Strategy:
    def on_day_start(self, ctx): ...      # reset intraday state (engine is intraday-only)
    def on_bar(self, ctx) -> [Order]: ... # required: emit entry order(s) when flat
    def manage(self, ctx, position) -> Exit | None: ...  # exits for an open position
```

The strategy's **only** view of the world is `BarContext`, which is constructed with the full day but immediately truncates to `<= now` — so a strategy *physically cannot* read future bars. Surface:

`ctx.now`, `ctx.spot`, `ctx.spot_bar`, `ctx.recent(n)`, `ctx.prev_close()`, `ctx.prev_day_hlc()`, `ctx.snapshot()`, `ctx.atm_strike(side)` (nearest *traded* strike).

### Exit value types — `engine/strategy/exit.py`

`manage()` returns one of:

| Exit | Meaning |
|---|---|
| `Exit.market(reason, fraction)` | discretionary → next-open fill |
| `Exit.at(level, trigger, fraction)` | resting stop (`'fall'`) / target (`'rise'`), intrabar, gap-through |
| `Exit.bracket(stop, target, ...)` | premium stop **and** target together; stop checked first (conservative); per-side fractions |
| `Exit.trail(gap, fraction)` | premium trailing stop off the intrabar peak |

`fraction < 1.0` books a partial and rides the rest — partial scale-out is a first-class engine feature.

## The Driver — `engine/backtest.py`

`Backtest(market, cost_model, knobs).run(strategy, start, end) -> Result`

Per trading day:

1. `on_day_start(ctx)` — reset.
2. Iterate the signal-timeframe spot bars. When **flat**, `on_bar(ctx)` may return Order(s) → fill at `B + timeframe`. Single-leg buys go through `Position`; anything with a SELL leg or >1 leg goes through `SpreadPosition`.
3. **Liquidity floor** (`Knobs.min_volume_mult`): reject the entry if the signal-bar option volume < `mult × order qty` (0 = off). Validated floor = **10×**; it *removes net-losing thin trades* rather than hurting the edge.
4. While **open**, `manage(ctx, pos)` resolves the exit (market / level / bracket / trail) via the shared resolvers above.
5. **EOD square-off** at the day's last actual option bar — the engine is strictly intraday, never holds overnight.

### `Knobs` — the sensitivity dials (the reframe)

```python
Knobs(slippage_pct=0.0, timeframe="5min", fill_price="open", min_volume_mult=0.0)
```

A single backtest number is not trustworthy (the same G12 config swung +₹135k vs +₹49k on bar-construction alone). The engine is therefore *parameterizable* over fill-timing, slippage, signal timeframe, and liquidity floor, and the real deliverable is a **sensitivity sweep** across these knobs.

### `Result` — metrics

`net`, `win_rate`, `profit_factor`, `tail_excl(n)` (net with top-n winners removed — tail-dependence check), `by_year`, `t_stat` (significance of mean per-trade return on premium deployed), plus a `carry_forwards` data-quality counter.

## Validation Posture

This is where the engine differs most from a typical framework:

- **Trade-by-trade reconciliation, not tolerance.** G12-CE reproduces 582/582 exact (strike + reason + entry within slippage) over 2020–2026. The root-cause hunt that got there (a `min_dte` mismatch caused the initial 56/124) is documented.
- **Walk-forward, not in-sample tuning.** The deployable CPR threshold (IV ≤ 0.12) was chosen on prior years only and validated out-of-sample — it survived honest OOS selection rather than being hand-picked on the full sample.
- **Fill audits + liquidity floor.** Every entry of the deployable config was audited for real fillability (OI, signal-minute volume); the ≥10× floor is wired into the driver and verified native-equals-post-hoc to the rupee.
- **Falsification.** Candidate filters (adaptive IV-percentile) were tested *to disprove them* and retired when they didn't beat the simple absolute threshold.

## Strategies Built (`strategies/`)

| Strategy | Verdict |
|---|---|
| **`cpr_breakout`** | ✅ The validated winner. Deep-ITM CPR breakout, +50 target / 12-pt trail / far-edge stop / IV ≤ 0.12. At 6 lots: **+₹868k / 6.1y, PF 1.70, Sharpe 1.42, every year green**, walk-forward validated, fill-audited, volume-floored. The Stage-4 live-pilot candidate. |
| `g12_ce` | Calibration reference — the 582/582 reconciliation. Not for deployment. |
| `ema_crossover` | Conclusively **no directional edge** when buying (4 variants tested; t −3 to −26). The EMA 9/21 crossover is a coin-flip entry; bracket geometry cannot rescue it. |
| `gap_down_put` | Falsified — real downside *index* edge (PF 1.36) dies as long puts (delta dilution + vega crush). |
| `orb_spread` | Falsified — loses in every knob corner; the "CPR+ORB regime portfolio" idea is dead. |
| `atr_roc_buy` | High-vol naked OTM momentum; 5-min ≈ breakeven, 15-min loses. OTM premium is the ceiling. |

Recurring lesson across all of these: **for NIFTY intraday option *buying*, absolute premium cost (low IV) is the binding edge filter; most signals have no edge once friction is modeled honestly.** CPR in the low-IV regime is the one real thread.

## Known Limitations & Design Decisions (acknowledged, not hidden)

- **1-min fidelity cap.** Intrabar exits resolve on 1-min option bars; sub-minute path is unmodeled (but the gap-through logic captures overshoot, unlike close-only samplers).
- **No India VIX in the store.** Day-open ATM IV is used as the volatility/VIX proxy (the store has per-strike IV but no index VIX).
- **NIFTY index has no volume.** The store carries OHLC only for the index; true volume-weighted VWAP is impossible, so VWAP-style strategies use an honest anchored equal-weighted proxy. Per-strike Call/Put volume *is* present and is used for the liquidity floor.
- **Not a git repository.** No version history; this document and the memory files at `~/.claude/projects/.../memory/` are the record of decisions and rejected variants.
- **Lot-size schedule is configurable but currently encodes only the 75→65 (2025-12-30) boundary** the validated scripts used; extend `NIFTY_LOT_SCHEDULE` for the full multi-year history if needed.

## Test Suite

Run: `cd "/Users/gokul/Desktop/build strategies " && PYTHONPATH="$PWD" python3 -m pytest -q`

115 tests green across data/execution/position/strategy/driver layers, including the golden reconciliation and the liquidity-floor goldens. To add a new strategy: subclass `Strategy`, then
`Backtest(MarketData(Catalog(ROOT), SpotSeries.from_index(ROOT), min_dte=N), CostModel(), Knobs(...)).run(strat, start, end)`.
