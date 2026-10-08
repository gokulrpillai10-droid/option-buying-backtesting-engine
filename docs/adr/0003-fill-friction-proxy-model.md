# ADR-0003: Bid-ask spread modeled via proxy — no real bid/ask in data

## Status
Accepted

## Context
The options chain parquet contains OHLCV + OI + Delta per strike. There are no bid/ask columns. The current `slippage_pct=0.0025` (0.25%) is 5–60× smaller than the real NIFTY ATM bid-ask spread (₹2–15 depending on time of day, volatility, and moneyness). This is the primary cause of over-optimistic backtest results.

## Decision
Two-tier fill model:

1. **Worst-case validation** — fixed ₹ spread table by moneyness applied on every run under `Knobs(intrabar="pessimistic")`. A strategy must survive this tier to be considered a GO candidate.

2. **Production estimate** — `slippage_pct` (% of premium) plus `slippage_range_k` (bar high-low range penalty for fast markets) plus `thin_exit_mult` (volume penalty for illiquid bars). This tier is used for the standard backtest run.

## Consequences
- Every strategy is now stress-tested at worst-case friction before any deployment decision.
- The gap between worst-case and production runs is the "friction error band" — a strategy with a narrow band is robust; a wide band means the edge depends on getting good fills.
- If bid/ask data becomes available (e.g. from a live fill audit), the proxy model should be replaced with actuals.
- The fixed spread table values (ATM ₹8, 1-OTM ₹5, 2+ OTM ₹2 per side) are estimates pending a live fill audit to calibrate them.
