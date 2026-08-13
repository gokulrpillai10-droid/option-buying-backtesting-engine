# TODOS

## NIFTY ATM weekly option depth audit (gap-fill-fade)
- **What:** Validate the backtest's assumed option slippage/spread against real
  NIFTY weekly ATM bid/ask depth at 09:20 on gap days.
- **Why:** The gap-fill-fade viability hinges on "does the fade clear option
  costs." The backtest answers with an *assumed* spread; the honest confirmation
  is measured live depth — the same gate the ADANIENT gap-drive short is going
  through.
- **How:** Reuse the `analysis/fill_audit.py` record/report pattern for NIFTY
  weekly ATM (both CE and PE), capturing sweep cost for the traded lot size vs
  the backtest's slippage assumption.
- **Gate / blocked by:** Only pursue if the gap-fill-fade backtest is a GO. If
  the backtest is NO-GO on the assumed (optimistic) costs, real costs only make
  it worse — no audit needed.
- **Source:** /plan-eng-review 2026-07-20 (spec:
  docs/superpowers/specs/2026-07-20-nifty-gap-fill-fade-design.md).

## Regime detector: decide adaptive weight loop for v1
- **What:** Decide whether to ship the outcome-weighted adaptive loop in v1 or cut it.
- **Why:** It only activates after ~40 resolved samples (~1.5y) and then fits 6
  weights on few autocorrelated outcomes — high complexity, low near-term value,
  overfit risk. Base fixed weights carry v1 regardless.
- **How:** Ship fixed weights + the outcome journal (logging only); gate the
  weight-nudging behind a proven-base-signal flag, or cut entirely for v1.
- **Source:** /plan-eng-review 2026-08-13 (outside-voice #3; spec:
  docs/superpowers/specs/2026-08-13-regime-buysell-detector-design.md).

## Regime detector: breadth — PIT membership vs proxy
- **What:** Decide breadth input: hand-curated NIFTY50 point-in-time membership
  vs a stable large-cap proxy basket.
- **Why:** Hand-curating historical index membership is error-prone and a
  survivorship trap, feeding only a ~7% module. Effort-to-impact is poor.
- **How:** Prefer a fixed liquid large-cap basket for v1; only build PIT
  membership if breadth proves it earns its weight in validation.
- **Source:** /plan-eng-review 2026-08-13 (outside-voice #5).
