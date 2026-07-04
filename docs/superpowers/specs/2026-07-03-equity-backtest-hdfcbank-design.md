# HDFCBANK Intraday Cash-Equity Backtest — Design

**Date:** 2026-07-03
**Status:** Approved section-by-section in brainstorming; pending final spec review.

## Goal

Add a cash-equity backtest capability to the engine and use it to test the CPR
Breakout signal on HDFCBANK intraday (long and short, MIS-style, squared off
EOD). First stock only; the walk-forward-validated NIFTY options core is never
touched.

## Locked decisions

1. **Approach B:** same repo, new `engine/equity/` subpackage. The options
   `Backtest`/`Position`/`Chain`/`CostModel` are never imported or modified by
   the equity path. Genuinely instrument-agnostic primitives are reused
   unmodified: `SpotSeries`, `Trade`, `Result`.
2. **Scope:** HDFCBANK only. General Nifty-50-any-symbol capability deferred.
3. **Data:** Kite historical API, 1-min OHLC, fetched as far back as Kite
   allows; chunked, throttled, cached to local parquet, resumable.
4. **Strategy:** port of CPR Breakout (candle crosses prior-day CPR pivot
   edge). Spot-based signal transfers directly.
5. **No IV gate in v1.** The options edge was gated on day-open ATM IV ≤ 0.12;
   HDFCBANK has no per-stock IV in the store. Test the raw unfiltered signal
   first.
6. **Exits simplified for v1:** no scale-out. Single stop (signal-bar close
   beyond the far CPR band, same mechanism as the options version) + single
   fixed price-point target (sweepable knob, calibrated empirically) + hard
   EOD square-off.
7. **Both directions:** bullish cross → long shares, bearish cross → short
   (intraday MIS).
8. **Sizing:** fixed capital per trade — `qty = floor(fixed_capital / fill_price)`.
   Default `fixed_capital = ₹100,000` (configurable), mirroring CPR's ~₹91k
   deployed-capital-per-trade framing.
9. **Costs:** new `EquityCostModel` with the standard Zerodha MIS-equity rate
   card (independent of the F&O `CostModel`): brokerage `min(₹20, 0.03%)` per
   executed order, STT 0.025% sell-side only, NSE exchange txn charges, SEBI
   fee, stamp duty 0.003% buy-side, 18% GST on fees.

## Architecture / Components

New `engine/equity/` subpackage:

| Component | File | Purpose |
|---|---|---|
| `kite_loader` | `engine/equity/kite_loader.py` | Fetch/cache/chunk/resume HDFCBANK 1-min OHLC via Kite historical API into local parquet. Symbol is a parameter, but only HDFCBANK is exercised in v1. |
| `SpotSeries` | reused as-is | Cached bars load straight in; one series is both signal feed and fill feed (no spot-vs-chain split — no derivative layer). |
| `EquityBarContext` | `engine/equity/context.py` | Lightweight context exposing only spot-forwarding methods (`spot`, `spot_bar`, `recent`, `prev_close`, `prev_day_hlc`, `opening_range`). Deliberately duplicates ~6 one-liners from `BarContext` to keep zero shared surface with the proven options context. |
| `EquityPosition` | `engine/equity/position.py` | `direction` ("long"/"short") instead of strike/side. P&L = (exit − entry)×qty long, (entry − exit)×qty short. |
| `EquityCostModel` | `engine/equity/costs.py` | Zerodha MIS-equity rate card (decision 9). |
| `EquityBacktest` | `engine/equity/backtest.py` | New driver; day loop shaped like `Backtest._run_day` (on_day_start → on_bar → fill next-open → manage → stop/target/EOD) but fills/exits read HDFCBANK's own bars. |
| `CPRBreakoutEquity` | `strategies/cpr_breakout_equity.py` | Same CPR-cross entry, unfiltered, single stop + sweepable target, no scale-out, both directions. |
| `Trade`, `Result` | reused unmodified | Existing analysis/tear-sheet code works unchanged on equity results. |

## Data flow

- **Day loop.** `EquityBacktest.run(strategy, start, end)` iterates trading
  days straight off the cached HDFCBANK series — the stock's own bars define
  its trading days (no expiry/chain stream). Per day: `on_day_start(ctx)` at
  the first bar, then per 5-min signal bar (resampled from cached 1-min data,
  START-labeled, same convention as `SpotSeries`): flat → `strategy.on_bar(ctx)`;
  in-position → `strategy.manage(ctx, pos)`.
- **Entry fill.** Order carries `direction`. Fill at the next signal bar's
  open with adverse slippage: long `open × (1 + slip)`, short `open × (1 − slip)`.
  **Slippage default 0.05%** (not the options engine's 0.25%, which was
  calibrated for option-premium spreads; HDFCBANK's cash spread is
  ~0.01–0.03% of price). Configurable knob for stress runs.
- **Sizing.** `qty = floor(fixed_capital / fill_price)` at fill time.
- **Exits.**
  - *Stop:* signal-bar close beyond the far CPR band → market exit at next
    bar's open with adverse slip.
  - *Target:* fixed price-point level, resolved intrabar on the 1-min bars
    inside each 5-min window (same pattern as the options `_opt_window` +
    `resolve_level_exit`, including gap-through → fill at the worse open).
    Direction-aware: above entry for long, below for short. Level is a
    sweepable knob.
  - *EOD:* square off at the day's last bar close with adverse slip,
    reason `"eod"`.
- **Booking.** `EquityPosition.close(...)` computes direction-aware gross
  P&L; `EquityCostModel.total(buy_value, sell_value)` nets the rate card →
  standard `Trade` appended to `Result`.

## Error handling / robustness

- **Kite fetch:** 1-min data capped at ~60 days/request, rate-limited
  (~3 req/s). Loader chunks the range, throttles, retries with exponential
  backoff on 429/5xx/network errors. Each completed chunk is written to
  parquet immediately; re-runs skip cached ranges (crash-resumable).
  Post-fetch integrity pass: sorted, de-duplicated timestamps; prices sane
  (no zero/negative, H ≥ max(O,C), L ≤ min(O,C)); bad bars dropped and logged.
- **Holidays / missing bars:** days absent from the cache simply don't run —
  no separate NSE calendar, no carry-forward logic. Minutes with no trades
  don't exist as bars; the loop operates on bars present. A configurable
  **min-bars-per-day threshold (default ~300 of 375)** skips anomalous
  sessions (partial data, Muhurat evening sessions, half-days).
- **Corporate actions:** Kite intraday history is not back-adjusted, and
  HDFCBANK had a 1:1 bonus in Aug 2025 — price halves overnight, making that
  day's prior-day HLC (and all CPR levels) 2× scale. Intraday-only means no
  P&L damage, but the signal day is invalid. **v1 rule: if
  |open / prev_close − 1| > 15%, skip the day** (no-trade, logged).
  Ratio-adjusting prior HLC is a v2 refinement.
- **Short selling:** pure P&L simulation; no margin/borrow modeling (intraday
  MIS). Only short-specific logic is direction-aware exits and slippage signs.
- **Determinism:** the backtest reads only the local cache; Kite is touched
  exclusively by the loader step. Runs are offline-reproducible.

## Testing (TDD)

Mirrors the existing `tests/unit` + `tests/golden` + `tests/fixtures` layout.

Unit:
- `test_equity_costs.py` — `EquityCostModel` vs a fully hand-calculated
  Zerodha MIS round trip, long and short: ₹20-vs-0.03% brokerage cap per leg,
  sell-side-only STT, exchange + SEBI + buy-side stamp, 18% GST on the right base.
- `test_equity_position.py` — `close` in all four quadrants (long/short ×
  profit/loss); qty and net-after-costs exact.
- `test_equity_context.py` — accessors on a synthetic frame, including
  look-ahead safety at day boundaries.
- `test_kite_loader.py` — fully mocked Kite client: 60-day chunk boundaries,
  backoff/retry on 429, resumability (second run requests only missing
  ranges), integrity pass drops malformed bars.
- `test_cpr_breakout_equity.py` — CPR-cross entries on synthetic paths
  (bullish → long, bearish → short, no-cross → flat), >15% gap skip-day rule,
  min-bars skip rule.

Golden:
- `test_equity_backtest_driver.py` — hand-constructed multi-day synthetic
  fixture end-to-end: next-open fill with slip, intrabar target on 1-min bars
  (including gap-through), close-beyond-band stop, EOD square-off,
  capital-based sizing; exact net P&L of every trade hand-verified.

Cross-cutting:
- **Signal parity:** the same synthetic spot path fed to the options
  `CPRBreakout` and `CPRBreakoutEquity` must produce identical cross
  timestamps and directions.
- **Regression:** the existing 115 tests stay green untouched;
  `engine/equity/` imports nothing from the options core.

## Scope / YAGNI

In: the Kite loader (HDFCBANK), the five `engine/equity/` components, the
ported strategy, the test suite above, and one analysis run sweeping the
price-point target knob.

Out (deferred): any-symbol generality (parameterized but not exercised),
live/paper wiring (future Stage-4-equivalent spec), IV/regime gates,
scale-out/trailing exits, margin/leverage/borrow modeling, corporate-action
back-adjustment, overnight/CNC positions, per-stock exchange calendars.
