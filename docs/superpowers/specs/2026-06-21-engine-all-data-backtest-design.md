# Engine: run any strategy on all accurate data — Design

**Date:** 2026-06-21
**Status:** Approved (brainstorming + engineering review + outside-voice review complete)
**Goal:** Point a code-first strategy at the engine and have it backtest across ALL
accurate data — full 5-year real NIFTY spot for signals + the full Data-v3 option
store for fills — look-ahead-safe, costed, with the user controlling entry / strike /
exit. **And** quantify how sensitive a strategy's edge is to fill-timing / bar-construction
/ slippage / DTE assumptions (because our headline finding — G12 swinging +₹135k vs
+₹49k on bar-construction alone — proves a single backtest number is not trustworthy
on its own).

---

## 1. Scope

**In scope (this build):** `SpotSeries.from_index()` (loader as a constructor),
`MarketData` (lean: expiry selection + `stream()` + cross-expiry guard), `Backtest`
driver, `Strategy.manage` + `Exit` helpers, IV/Greeks pass-through on quotes, `Position`
pnl helpers, `atm()`/`atm_offset()`, basic `Result` (net/win%/PF/t-stat/tail/by-year +
data-quality counts), **sensitivity knobs** (§5), and a **sensitivity sweep** mode.

**Reframe (outside-voice P3-1):** the engine is **parameterizable** over the unstable
choices, and the headline deliverable is a **sensitivity verdict** ("does the edge survive
±1-min fill, 1-min-vs-native-5-min bars, slippage sweeps?"), not one precise number.
Trade-by-trade reconciliation is one validation mode.

**Build order (outside-voice P3-2):** reconciliation milestone FIRST (prove the engine
reproduces G12-CE trade-by-trade); `BarContext` namespacing ergonomics come AFTER.

**NOT in scope (deferred):** nearest_delta/nearest_premium · advanced metrics
(Sharpe/Sortino/Calmar/Monte-Carlo) · spot fast-path · cross-expiry option look-back
SUPPORT (guarded only) · YAML ConfigStrategy · retrofitting legacy scripts · computed
(BS) Greeks (pass-through only).

## 2. Division of labour
- **Engine owns the mechanics (accuracy):** load all data, look-ahead safety, the fill
  contract, slippage, costs, **date-keyed lot size**, position accounting, expiry
  selection (from real partitions), multi-day driving, the sensitivity knobs.
- **User owns every decision:** entry, strike selection, exit.

## 3. Architecture
```
Backtest(market, cost_model, knobs)                              # knobs = §5
  └─ MarketData(catalog, spot)                                   # lean
  │    ├─ SpotSeries.from_index(root, timeframe, label)          # P3-2: loader = ctor
  │    ├─ trading_days(); expiry_for(day, min_dte)               # from REAL partitions
  │    └─ stream(start,end) → (expiry, day-chains) batched        # ARCH-4: load-once
  └─ per day → on_day_start(reset) → per bar:
       BarContext(spot day-view + O(1) day-index + bounded slice, # PERF-1 (corrected)
                  chain indexed once per day)                      # PERF-2
         ├─ flat?  Strategy.on_bar(ctx) → Order → ExecutionModel   # ARCH-1 fill contract
         └─ open?  Strategy.manage(ctx,pos) → Exit → ExecutionModel# ARCH-2 exit kinds
       EOD (last actual bar) → square-off → Position.close → Result
```
Components: `SpotSeries` (+`from_index`), `MarketData` (lean), `Backtest`, `Result`,
extended `ExecutionModel`/`OptionQuote`/`BarContext`/`Position`, `Strategy.manage`+`Exit`.

## 4. Locked decisions

### From engineering review
- **ARCH-1 Fill timing — ONE contract in `ExecutionModel`, with an EXACT, tested
  convention (outside-voice P1-2).** Spec the numeric rule precisely: 5-min bars are
  START-labeled; a bar labelled `B` covers `[B, B+5min)` and is CONFIRMED at `B+5min`;
  the fill is the **1-min option open at `B+5min`** (gated, slipped). A test asserts the
  exact fill timestamp for a known bar. This off-by-one-minute is the source of the
  ₹135k-vs-₹49k discrepancy, so it is pinned and tested, not assumed.
- **ARCH-2 Exit fills.** `Exit.at(level)` = resting stop/target: recognized at the bar's
  CLOSE time (never its open — no look-ahead), priced at the level when the option H/L
  crosses it; on a GAP past the level, priced at the worse of (level, bar-open). 1-min
  granularity caps fidelity — documented. `Exit.market()` = next 1-min open.
- **ARCH-3 Lifecycle.** day-by-day; `on_day_start(ctx)` clears intraday state; EOD
  square-off; cross-day data from resident spot via `ctx.day.*` / `ctx.spot.history()`.
- **ARCH-4 Streaming.** `MarketData.stream()` batched iterator; each expiry loaded once.
- **CQ-1 / P2-4 Missing price mid-position.** Carry forward last price, **suppress
  intrabar level-fills while carried-forward** (stale H/L can't be trusted — a resting
  Exit.at must not trigger on invented data), allow only the guaranteed EOD market close,
  flag those trades low-confidence, count carry-forwards on `Result`.
- **CQ-2 Context API.** namespaced `ctx.spot.*` / `ctx.chain.*` / `ctx.day.*` (AFTER
  reconciliation, per P3-2 sequencing).
- **CQ-3 Legacy.** freeze scripts; centralize metrics; reconcile ONE strategy.
- **TEST Cross-expiry option look-back.** fail-loud guard + test.
- **PERF-1 (corrected, outside-voice P1-1).** NOT "precompute everything" — only a fixed
  NAMED set is precomputed at `on_day_start` (`prev_high/low/close`, rolling N-day). For
  arbitrary `ctx.spot.history(lookback)` rules: serve a **bounded slice** off a resident,
  chronologically-built spot frame with an O(1) day-index — never a full-frame filter.
  The "precompute-anything" claim is explicitly dropped; the limit is documented.
- **PERF-2.** index each day's option frame on (DateTime, StrikePrice) once.

### Accepted accuracy fixes (outside-voice review)
- **P1-3 Reconciliation = trade-by-trade IDENTITY** (entry ts/strike/px, exit ts/px/reason),
  not aggregate-within-tolerance. Tail-dominance makes aggregate tolerance meaningless.
- **P1-4 Lot size is a date-keyed ENGINE lookup** (75 before 2025-12-30, 65 from), never
  strategy-supplied; test crosses the boundary.
- **P1-5 Expiry calendar derived from real data partitions** (the 328 actual expiries),
  never a weekday rule; test the Thu→Tue transition weeks under `DTE≥min_dte`.
- **P2-1 EOD square-off = the day's LAST AVAILABLE option bar**, not a hardcoded 15:15
  (Muhurat / early closes / missing 15:15).
- **P2-2 Normalize all timestamps to naive IST on ingest**; assert before the 7 spot
  files dedupe (mixed tz would silently misalign).
- **P2-3 Missing strike at ENTRY = explicit Rejection** (distinct from CQ-1 carry-forward,
  which is mid-position only). `quote()` of an absent strike → None/Rejection, never NaN.
- **P2-5 Liquidity-gate vs carry-forward precedence** defined explicitly.
- **P2-6 Determinism / streaming-equivalence = full-ledger HASH equality** with fixed sort
  keys + fixed reduction order (not summary-stat equality).
- **P3-3 `min_dte` must match the reconciled script's actual DTE rule** exactly, not default-3.
- **P3-4 Greeks/IV = pass-through from parquet only** (no BS recompute).
- **P3-5 Engine must not re-arm a signal post-exit beyond the strategy's per-day cap.**

## 5. Sensitivity knobs (the reframe)
`Backtest` accepts a `knobs` config that parameterizes the unstable choices:
- `fill_offset_min` (e.g. 0 / +1 / -1 minute on the fill timestamp),
- `bar_construction` (resampled-from-1min vs native-5min source),
- `slippage` (pt/side + stop-extra; sweepable),
- `min_dte`.
A `sweep(strategy, grid)` mode runs the strategy across a grid of knob settings and emits
a **sensitivity table** (net / PF / sign by setting) — the headline deliverable: does the
edge survive, or is it an artifact of one assumption set?

## 6. The Strategy / Exit API
```python
class Strategy:
    def on_day_start(self, ctx): ...
    def on_bar(self, ctx) -> list[Order]: ...
    def manage(self, ctx, pos) -> Exit | None: ...
```
`ctx.spot.price/.bar/.history(lookback)` · `ctx.chain.quote(strike,side)` (OHLC+Vol+OI+
IV+Greeks, pass-through) · `ctx.chain.atm()/atm_offset(pts)` · `ctx.day.prev_high()/
opening_range(end)` · `pos.pnl/.pnl_pct/.bars_held/.mfe/.mae/.peak` · `Exit.at(level)/
Exit.market()` + helpers `stop_pct/trailing/target/time_stop`.

## 7. Accuracy guarantees (tested)
Look-ahead audit · **streaming == single-load = full-ledger hash** · determinism (hash) ·
exact fill-timestamp · **reconciliation = G12-CE trade-by-trade identity** · honesty
(rejections, carry-forwards, guard trips, low-confidence flags all logged).

## 8. Test plan (TDD)
Unit: `SpotSeries.from_index` (dedupe, tz-normalize, resample, label convention),
`MarketData` (trading_days, expiry_for + Thu→Tue transition, stream loads-once,
cross-expiry guard raises), `ExecutionModel` (EXACT fill timestamp, Exit.at intrabar at
close-time, Exit.at gap-through, Exit.market, **lot-size boundary 2025-12-30**),
`Strategy.manage` + each `Exit` helper, missing-strike→Rejection, carry-forward suppresses
level-fills, `OptionQuote` IV/Greeks pass-through, `Position` pnl, `Result` metrics + dq
counts, **knob sweep** produces a sensitivity table.
Golden/E2E: enter→manage→exit→flat; on_day_start reset; EOD = last bar; missing-price
carry-forward (+dq, low-confidence flag); streaming==single-load (hash); look-ahead audit;
determinism (hash); **RECONCILIATION: G12-CE trade-by-trade identity** (the proof).

## 9. Failure modes (all handled + tested, none silent)
missing price mid-position → carry-forward + suppress level-fills + EOD close + flag ·
missing strike at entry → Rejection · expiry parquet missing → skip+log · cross-expiry
look-back → fail-loud guard · signal on last bar → `no_next_bar` · gap-through stop →
worse-of(level, open) at bar close · EOD with no 15:15 bar → last available bar ·
mixed-tz timestamps → normalized+asserted at ingest · lot-size boundary → date-keyed.

## 10. Implementation order (reconciliation-first)
1. `SpotSeries.from_index` (dedupe/tz/resample/label) + tests.
2. `Chain`/`OptionQuote` IV-Greeks pass-through + per-day index + tests.
3. `ExecutionModel` fill contract (exact timestamp) + exit kinds (gap-through, close-time)
   + date-keyed lot size + tests.
4. `MarketData` (expiry-from-partitions, stream load-once, cross-expiry guard) + tests.
5. `Backtest` driver (per-day reset, EOD-from-data, carry-forward rules, knobs) + tests.
6. `Result` basic metrics + dq counts + tests.
7. **RECONCILIATION: G12-CE through the engine, trade-by-trade identity.** ← milestone/proof
8. Knob `sweep` + sensitivity table.
9. `Position` pnl helpers + `Strategy.manage`/`Exit` ergonomics.
10. `BarContext` namespacing (`ctx.spot.*`/`.chain.*`/`.day.*`) — LAST (ergonomics).

Parallel lanes: (1) ‖ (2) ‖ (Exit/Position helpers) up front; the rest sequential to the
reconciliation proof.
