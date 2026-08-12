# Live Execution Engine — Design (WIP)

**Date:** 2026-08-12
**Status:** Brainstorm in progress. Sections 1–2 approved. Section 3 has 2 open confirmations. Section 4 (testing/scope) not yet presented.
**Branch:** gap-fill-fade

## Goal

A **live/paper order-execution engine** that runs any of this repo's `Strategy`
subclasses against **live Zerodha Kite** market data, sized for a **₹50–70k**
account, with a **daily loss limit kill-switch** as the core guardrail against
overtrading. Rollout is **paper first** (live data, simulated fills), then flip
one flag to trade real money.

## Locked decisions

- **Live execution** (backtest → live bridge), NOT a backtest-internal change.
- **Strategy-agnostic** live runner reusing the existing `Strategy` /
  `BarContext` / `Order` / `Exit` / `CostModel` / `Position` / `resolve_exit`
  core **unchanged**. Starts on CPRBreakout.
- **Paper first.** `mode: paper|live` swaps **only** the `OrderGateway`;
  feed, bars, risk, sizing, logging are identical between modes.
- **Daily loss limit = hard MTM flatten + halt.**
  - Loss counted = `realized + open_mtm`; MTM priced **adverse (sell-side)** so
    the switch trips slightly early, never late. *(approved)*
  - Breach → immediately square off open position, set `halted=true` for the
    session, persist.
  - Checked **every poll (~3–5s)**, not every 5-min bar.
  - **Halt is sticky across restarts** — reloading day-state cannot un-halt a
    halted day. *(approved)*
- **Data feed = Kite REST polling** (`kite.quote()`/LTP, ~3–5s) behind a
  `LiveFeed` interface; a WebSocket `TickerFeed` can replace it later with no
  runner change. Direct `kiteconnect` via `engine/equity/kite_loader.py`
  `make_kite()`; Kite MCP stays untouched (broken upstream).

## Section 1 — Architecture (APPROVED)

Reuse the trusted backtest core; add a live-only `engine/live/` swap-layer.

| Component | Responsibility |
|---|---|
| `LiveFeed` + `PollingFeed` | Poll Kite for NIFTY index + working strike set. Always real data, both modes. |
| `LiveSpot` | Accumulate index 1-min bars; same accessor surface as `SpotSeries` (`bar_at`, `price_at`, `prev_day_hlc`, `window`, `close_before`). Prior-day HLC via Kite historical once. |
| `LiveChain` | `snapshot_at(now)` → as-of strikes/quotes (price/vol/OI/is_traded); same surface as backtest `Chain`. |
| `OrderGateway` | The only paper/live difference. `PaperGateway` (sim fills) / `KiteGateway` (real orders). |
| `PositionBook` | Live position state + realized P&L + open MTM; persisted. |
| `RiskManager` | Daily loss limit: `realized + open_mtm ≤ −limit` → flatten + halt. |
| `LiveRunner` | Wall-clock loop mirroring `Runner.run`. |
| Day-state JSON | `live_state/YYYY-MM-DD.json`: positions, realized P&L, `halted`, order log. Crash/restart safe. |

**Per-bar flow:** feed poll → update `LiveSpot`+`LiveChain` → `BarContext` →
`RiskManager.check` → if flat & not halted: `on_bar` → sizing guard →
`OrderGateway.enter` → `PositionBook` → later bars: MTM + `manage`/SL/trail/
square-off → `OrderGateway.exit` → realized updated → persist.

## Section 2 — Risk manager & state (APPROVED)

- Track per poll: `realized` (closed trades, net via `CostModel`), `open_mtm`
  (`(sell_price − entry)×qty − exit_cost_est`, sell-side price), `day_pnl`.
- Breach → square off now, `halted=true`, persist; halted ignores `on_bar`
  orders until next `on_day_start`.
- Single JSON day-state file, written after every state change.
- **Startup loads today's state first.** Halted stays halted. Open position
  reconciled against `kite.positions()` in live mode (warn loudly on mismatch);
  paper mode trusts the file.
- Proposed defaults (tune in review): `daily_loss_limit=₹2,500`,
  `capital=60,000`, `lots_per_signal=1`, `max_deploy_pct=0.80`,
  `square_off=15:15`, `entry_cutoff=14:45`, `slippage=0.25%`.

## Section 3 — Order gateway & sizing (PROPOSED — 2 confirms open)

- `OrderGateway`: `enter(order, ctx) -> Fill|Rejection`, `exit(position, level) -> Fill`.
- **`PaperGateway`** (default): fill at next observed live quote ± 0.25% slip;
  same liquidity gates (`is_traded`, min vol/OI) → real `Rejection` reasons;
  costs via shared `CostModel`. Paper P&L directly comparable to backtest.
- **`KiteGateway`** (real): `kite.place_order` MIS, status-poll to
  COMPLETE/REJECTED, real avg-price `Fill`. Auth via `make_kite()`; auth fail =
  hard stop before loop. **Buy-to-open only** (refuses `action="sell"` opens).
- **Sizing guard** (both modes, pre-entry): reject if
  `entry_premium×qty > capital×max_deploy_pct` → `Rejection("exceeds_capital")`.
  Downsizes CPR to what 50–70k fits and logs it rather than overleveraging.
- Scope/YAGNI: single position at a time; one strategy per process; intraday
  only; single-leg buys live.

**OPEN CONFIRMATIONS:**
1. Live entries as **market orders** (configurable to limit later)?
2. Live gateway **buy-to-open only** (refuse short-open legs) for now?

## Section 4 — Testing & scope (NOT YET PRESENTED)

Planned:
- `PaperGateway` + replayed/recorded feed → deterministic tests.
- `RiskManager` unit tests: breach flattens; halt persists across restart;
  sizing guard rejects oversize.
- **Parity/golden test:** replay a historical day through the live loop and
  compare trades to the backtest for the same day — extends the engine's
  "identity" trust philosophy to the live path.
- CLI entry: `python -m engine.live.run --mode paper --config live.yaml`.

## Next step after approval

Invoke `writing-plans` to produce the implementation plan. Do NOT start coding
until Section 3 confirms + Section 4 are approved and the full spec is reviewed.
