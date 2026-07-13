# HDFCBANK Gap Router — Design Spec

**Date:** 2026-07-13
**Status:** Approved (design), pending implementation plan
**Instrument:** HDFCBANK cash equity, intraday (entry after open, exit by EOD)

## 1. Motivation & evidence

Pattern-mining on HDFCBANK 1-min bars (2020–2026, `analysis/hdfc_patterns.py` and
`analysis/hdfc_gapandgo.py`) established two facts:

1. **HDFCBANK is a fade market.** Opening-range breakouts and prior-day-high
   breaks both have negative edge; gaps fill intraday **~70% every year**
   (never < 64%). Gap-fill is the durable base-rate edge.
2. **Gap-and-go (the ~30% that never fill) is separable early.** Across 1,311
   gap days (|gap|>0.15%), go-days differ from fill-days on features known by
   09:30:

   | feature | gap-and-go (n=457) | gap-fill (n=854) |
   |---|---|---|
   | gap size | 1.14% | 0.59% |
   | early drive open→09:30 (gap dir) | +0.22% | −0.29% |
   | NIFTY gapped same direction | 90% | 78% |
   | close (gap direction) | +0.70% | −0.62% |

   P(go) rises monotonically with the first-15-min drive (14.7% when reverting
   <−0.3%, → 74.8% when driving >+0.6%). NIFTY confirmation matters: HDFC gaps
   **not** confirmed by NIFTY are only 20% go (they overwhelmingly fill).
   Combined filter (|gap|>0.4% & NIFTY-aligned & drive>0.1%): n=268, **74% go,
   +0.45% avg, 67% win**.

**Conclusion:** go vs fill is one decision, routed by three early features —
gap size, NIFTY alignment, first-15-min drive. Build one router, not two
disconnected strategies.

## 2. Architecture — unified two-checkpoint router

One strategy class `GapRouterEquity`, evaluated each session, **at most one
position/day**, exit by EOD. The two "strategies" (gap-fill fade, gap-and-go
momentum) are the two branches of the router and can never conflict.

Features:
- `gap  = open / prev_close − 1`            (known 09:15)
- `ngap = nifty_open / nifty_prev_close − 1`(known 09:15)
- `r15  = (px_0930 / open − 1) × sign(gap)` (known 09:30; + = driving in gap dir)
- *aligned* ≔ `sign(ngap)==sign(gap)` and `|ngap| > NIFTY_MIN`

### Decision logic
```
09:15 OPEN:
  |gap| ≤ GAP_THR                       → NO-TRADE (not a gap day)
  small gap (≤ BIG_THR) & not aligned   → FADE now  (against gap, toward prev_close)
  else (big gap OR aligned)             → defer to 09:30

09:30:
  r15 ≤ 0                               → FADE      (against gap, toward prev_close)
  r15 ≥ DRIVE_THR & (|gap|>BIG_THR OR aligned) → GO  (with gap)
  0 < r15 < DRIVE_THR (ambiguous)       → NO-TRADE
```

### Starting thresholds (to be re-fit on train split, see §5)
| param | start | meaning |
|---|---|---|
| `GAP_THR` | 0.15% | min gap to act |
| `BIG_THR` | 0.40% | "big" gap |
| `NIFTY_MIN` | 0.10% | NIFTY must actually gap to count as aligned |
| `DRIVE_THR` | 0.30% | first-15-min drive to call momentum |

## 3. Trade management

| | FADE branch | GO branch |
|---|---|---|
| direction | against the gap | with the gap |
| entry | 09:15 or 09:30 (per router) | 09:30 |
| target | prior close (the fill) | EOD close (capture o2c) |
| stop | opening-range extreme + buffer | opposite OR extreme |
| time exit | 15:20 EOD if neither hit | 15:20 EOD |

- **Opening range (OR)** = 09:15–09:30 high/low (holds ~43% of days → natural
  structural stop).
- GO branch uses **EOD exit** (base case) rather than a fixed extension target:
  go-days average +0.70% open-to-close in the gap direction, so holding to close
  captures the trend; a fixed target would cap it. Fixed-target variant is a
  tunable alternative.
- Target/stop buffers and multiples are parameters, defaulted, swept during
  validation.

## 4. Components & engine integration

- **`engine/equity/nifty_gap.py`** — `NiftyGapProvider`: loads the NIFTY 5-min
  CSV once (`/Users/gokul/Desktop/tradealgo/nifty_5min_5yr.csv`, path
  configurable), aggregates to per-date open/close → gap, exposes
  `gap_for(date)`. External-file dependency, isolated behind this provider.
- **`GapRouterEquity`** — new strategy class beside `CPRBreakoutEquity`, same
  interface; consumes `EquityBarContext` + injected NIFTY gap. Requires the
  engine's intrabar / intraday-time entry support (entries fire at 09:30, not
  next-open) — the exact interface is verified during planning; if the driver
  only supports next-open fills, the plan adds an intraday-entry hook.
- **`analysis/gap_router_backtest.py`** — runs the OOS backtest, prints
  per-branch + combined metrics.
- Reuses `EquityOrder` / `EquityPosition`; reads only cached data (offline,
  reproducible).

## 5. Validation

- **Train 2020–2023 → fit thresholds; test 2024–2026 out-of-sample.** The §2
  thresholds were derived on the full sample, so only the OOS split is
  trustworthy; in-sample numbers are reported but not relied on.
- **Costs ~0.10% round-trip** (intraday brokerage + STT + slippage) modeled and
  reported net. Gross edge is 0.3–0.7%, so cost is make-or-break.
- Metrics per branch and combined: trades, win %, avg net return, expectancy,
  profit factor, max drawdown, equity curve.
- **Unit tests:** feature calc (`gap`, `r15`, alignment), and router decisions
  at every boundary (fade/go/no-trade), deterministic — matching existing test
  style in `tests/unit/`.

## 6. Error handling & edge cases

- Missing NIFTY date (holiday mismatch) → treat as **not aligned** (conservative:
  biases toward fade / no-trade), never crash.
- Missing `prev_close` (first session) → NO-TRADE.
- No 09:30 bars (data hole / half day) → NO-TRADE that day.
- Short/holiday sessions → OR still = first 15 min from actual open.

## 7. Risks & caveats

- Thresholds are in-sample-derived until OOS-validated; the split in §5 is the
  guard against overfitting.
- Edge is thin vs costs — net-of-cost result is the go/no-go signal.
- Gap-and-go tail (a fade caught in a trend day) is bounded by the OR stop; size
  risk-per-trade accordingly.
- NIFTY data lives in a sibling repo file; the provider isolates that coupling.

## 8. Out of scope (YAGNI)

- Other symbols (design is HDFCBANK-specific; generalization is a later step).
- Futures/options execution (cash equity only for v1).
- Live/paper trading wiring (backtest validation only).
- Position sizing beyond fixed risk-per-trade.
