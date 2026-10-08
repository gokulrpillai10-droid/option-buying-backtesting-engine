# ADR-0001: Strategy files declare all their contract parameters

## Status
Accepted

## Context
Each strategy has parameters that describe *which contract it trades*: how many lots, what minimum DTE, whether it targets expiry day. These could live in the runner (`analysis/<name>_run.py`) or in the strategy class itself.

## Decision
All contract parameters (`LOTS`, `min_dte`, expiry-day flags) are declared as class-level constants in the strategy file, next to the signal parameters. The runner reads them and passes them to `Knobs` / `MarketData` — it does not define them.

## Consequences
- A strategy file is self-contained: reading it tells you everything about which contract it trades, not just when it trades.
- Runners become thin wrappers — they wire the strategy to the engine but own no strategy logic.
- Adding a new contract parameter (e.g. a volume floor) means adding it to the strategy class and updating the runner to forward it — two files, but the strategy is always the source of truth.
- Cost: slightly more boilerplate per strategy class.
