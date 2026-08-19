"""NIFTY50 breadth module — DEFERRED (7% weight).

Breadth (% of constituents above their MA, adv/decl) needs a point-in-time
NIFTY50 universe — a survivorship-correct add/remove history — plus per-name
history from Kite. Per the design's open decision (spec lines 213, 237), that
PIT curation is low value for a 7% module, so it is deferred: either a stable
large-cap proxy basket or a hand-curated `configs/nifty50_membership.csv` +
`active_on(date)` can be wired in here later.

Until then `breadth_score` returns None; the classifier drops a None module and
renormalizes the remaining five weights over themselves, so breadth's 0.07 is
redistributed pro-rata rather than dragging the composite toward zero.
"""


def breadth_score(*_args, **_kwargs):
    """Deferred — always None (excluded from the composite). See module docstring."""
    return None
