---
name: backtest-strategy
description: Backtest a user-supplied trading strategy on our data and return a data-driven GO/NO-GO verdict. Use whenever the user hands over a strategy (rules/conditions) to test, validate, or backtest against NIFTY options or NSE equity history. The edge is discovered by the backtest on our data, never asserted by brainstorming.
---

# Backtest a strategy

**Core rule:** the edge is discovered by the **backtest on our data**, NEVER asserted
by brainstorming. `/office-hours` and `/plan-ceo-review` shape *new product ideas* —
they have no role in validating a *given* strategy. Follow this flow exactly.

## 1. Understand the strategy's CONDITIONS
Read the user's strategy and extract, explicitly:
- **instrument** — NIFTY options (which side / strike selection)? equity (which symbol)?
- **timeframe** — default 5min.
- **entry** — the exact trigger condition(s).
- **exit** — stop / target / trail / time / EOD.
- **sizing** — lots (options) or capital (equity); **filters** — IV, volume, gap.

Write these back to the user as a conditions checklist and confirm before coding.
Ambiguity here = a wrong backtest, so resolve it now.

## 2. Envelope check — can the engine express it?
The engine models: **intraday only**; **long-premium options + covered spreads**
(net-short is refused); **cash equity** long / intraday-short; **next-bar fills**.
If the strategy needs multi-day/positional holds, net-short options, or anything
outside this, **STOP** and tell the user it needs a new engine capability first.
Do not fake it — an out-of-envelope strategy is not a NO-GO, it's un-testable as-is.

## 3. Scaffold the triplet
```
PYTHONPATH=. python3 analysis/scaffold_strategy.py <snake_name> [--equity]
```
Creates `strategies/<name>.py` + `tests/unit/test_<name>.py` + `analysis/<name>_run.py`
(the runner is pre-wired to the shared verdict). The files run as-is.

## 4. Encode the conditions — TDD
Write the **unit test first** (the conditions fire on a synthetic bar — see
`tests/golden/test_backtest_driver.py` for the fixture pattern), then fill
`on_bar` / `manage`. Signals come from `ctx` (look-ahead safe: `ctx.spot`,
`ctx.recent(n)`, `ctx.prev_day_hlc()`, `ctx.atm_strike(side)`). Size with
`lots=` / `capital=` — **never hardcode lot size** (the engine applies the
authoritative per-expiry lot).

## 5. Backtest on OUR data
```
PYTHONPATH=. python3 analysis/<name>_run.py > analysis/<name>_run_out.txt
```
The engine auto-applies per-expiry lots, the India cost card, look-ahead guards,
and integrity hard-fails. If it dies on an integrity invariant, the data/scoping
is wrong — investigate, never pass `strict=False` to silence it.

## 6. Find the edge FROM THE DATA
The runner prints `analysis.evaluate.evaluate(res).report()` — the standard
battery run on **every** strategy identically: net / PF / Sharpe / t-stat / maxDD,
block-bootstrap `p(net<=0)`, split-half, per-year, tail-excl. For strategy-specific
**nulls** (always-long / always-short / random / block-shuffle), build those
Results and pass their nets to `evaluate.compare_to_nulls(res.net(), nulls)` — a GO
must beat **every** null.

## 7. Verdict (the report classifies it)
- **GO** — every gate passed AND beats all nulls → candidate. Next: stress test
  (`Knobs(intrabar="pessimistic")` + slippage tiers), walk-forward, then a live
  fill audit before any deploy. Never deploy off one backtest.
- **MARGINAL / NO-GO** — document why, quoting the failing gates the report names.
  A documented NO-GO is a real deliverable here, not a dead end.

## 8. Commit
Commit `strategies/<name>.py` + its test + `analysis/<name>_run.py` + `_out.txt`
together (project convention: analysis scripts commit alongside their `_out.txt`),
with an honest GO/NO-GO note in the message.
