# Strategy: Gap-Down Continuation Put (candidate v1)

**Status:** candidate downside edge. INDEX-point simulation, no costs, idealised
fills. Must be re-validated on real option prices + India F&O costs via the engine.

## The question that started it
"What do the 224 down-days (>150 pts) have in common, so I can capture 25-50 pts?"

## The shared signature (daily structure of the 224 down-days)
| Trait | Value |
|---|---|
| Gapped DOWN at the open | **78%** |
| Closed RED from the open | **94%** |
| Closed in bottom 25% of the day's range | **73%** |
| Most damage came AFTER the open (room to trade) | 70% |
| From the open, fell 25+ pts / 50+ pts | 99% / 97% |
| Median bounce against an early short (open->high) | only 20 pts |

Read: big down-days are **gap-down trend days**, not V-bounces. The gap is the tell.

## The tradeable rule (tested on ALL days, no look-ahead)
`analysis/gap_down_continuation.py`:
- **Gate:** open gaps down >=20 pts vs prior close (don't fight the drift).
- **Trigger:** price breaks the opening-range (09:15-09:29) low by 2 pts, before 11:30.
- **Capture:** book half at +25, target +50 on the rest then trail 20; stop ~30-40 pts.

### Results (full 2020-2026, 347 signal-days, index points, no costs)
| Stop | Win | Avg/trade | Total pts | PF |
|---|---|---|---|---|
| 30 | 40% | +6.4 | +2,217 | **1.36** |
| 40 | 46% | +7.0 | +2,429 | 1.33 |
| 50 | 50% | +5.3 | +1,836 | 1.22 |

- Reached **+25 in 71%**, **+50 in 57%** of trades — the targets are realistic.
- Tighter stops win on PF because booking +25 locks profit before the bounce.

### Gap-size matters (non-linear)
| Gap | n | Avg/trade | PF |
|---|---|---|---|
| 20-50 | 139 | +5.8 | 1.25 |
| **50-100** | 122 | +0.5 | **1.02** (gap-fill dead zone) |
| **>100** | 86 | +11.2 | **1.49** |

## Why this works when breakout-puts didn't
The earlier put attempts ignored regime and got V-bounced. This **only shorts when
the market has already declared weakness (gap down)** — exactly the regime gate the
ORB study said was missing. Same drift asymmetry, now used *with* us not against us.

## Caveats to resolve in the engine
- **Index points != option points.** A ~0.5-delta put captures roughly half these
  index points in premium; the engine must simulate real put fills.
- **IV tailwind:** gap-down panic usually expands IV, which helps long puts on vega
  -- the option P&L may beat the linear index sim. Verify.
- No costs/slippage/liquidity gating yet. The 25/50/trail scaling needs the engine's
  position manager to execute faithfully.

## Config
`configs/gap_down_put.yaml`.
