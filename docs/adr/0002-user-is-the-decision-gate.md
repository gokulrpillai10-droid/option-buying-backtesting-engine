# ADR-0002: The evaluation battery reports; the user decides GO/NO-GO

## Status
Accepted

## Context
The `evaluate()` battery prints hard gates (PF ≥ 1.2, |t| ≥ 3, net > 0) and a binary "EDGE VERDICT: GO / NO-GO." This implies the algorithm makes the deployment decision.

## Decision
The evaluation battery is a **reporting tool**, not a decision gate. The printed gates are reference thresholds that flag statistical weakness — they do not determine whether a strategy is traded. The user reads the full report (including per-year breakdown, exit distribution, net-excl-top5) and makes the GO/NO-GO call manually.

## Consequences
- A strategy can be traded despite failing a statistical gate if the user has a domain reason (e.g. the strategy is a hedge, or the sample is structurally limited).
- A strategy that passes all gates can still be rejected if the per-year pattern looks regime-dependent.
- The report must therefore expose *all* relevant information, not hide it behind a single verdict line.
- The "EDGE VERDICT" label in the output is retained as a convenience summary but is explicitly not authoritative.
