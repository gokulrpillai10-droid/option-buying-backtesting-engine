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
