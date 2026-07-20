# NIFTY gap-fill fade — design spec

**Date:** 2026-07-20
**Status:** design, hardened by /plan-eng-review 2026-07-20 (11 decisions folded in)
**Depends on:** `analysis/nifty_gap_analysis.py` (daily census), `analysis/nifty_gap_path.py`
(post-open path study), engine option-backtest stack (`Catalog`, `MarketData`,
`Backtest`, `Strategy`, `TwoStage`).

## 1. Motivation & evidence

The gap census (2019→2026, Kite NSE daily) and the 5-min post-open path study
established:

- **Fill rate is monotone in gap size** — small gaps almost always return to
  prior close (0.05–0.30%: ~77–80%), large gaps rarely (>1%: ~18–21%).
  Regime-stable, the one robust structure.
- **Continuation from the open is a coin-flip** (up 47% green / down 52% red) —
  no follow-through edge (re-confirms the ORB NO-GO).
- **The one clean DIRECTIONAL edge was a REVERSAL:** a gap-down that reclaims
  its open by 09:30 keeps rising (rest-of-day +0.085%, 57% up). A gap-down
  still driving at 09:30 was a coin-flip.

**Thesis (two arms, decided at eng review OV1):**
- **Arm F — symmetric fade:** bet the gap fills back toward prior close.
- **Arm R — reclaim-reversal long:** bet a failed gap-down (reclaims open by
  09:30) continues up.

Both are monetised by buying an ATM weekly option and run head-to-head on the
same days so evidence, not a-priori choice, picks the winner.

**Stated risk up front:** every arm is LONG premium on a coin-flip-direction
underlying, so theta + spread may dominate. The backtest is built to make that
verdict unambiguous (net vs gross, per bucket, vs two nulls). If it dies, it
dies clearly on costs, not on a fitted exit.

## 2. Strategy: `GapFillFade(Strategy)` (`strategies/gap_fill_fade.py`)

### Shared selection — `_select(ctx)` (reused by strategy, pre-study, and the
### runner's null wrappers; DRY per eng-review C1)
Returns `(side, strike, gap, settled_open)` or `None` (stand aside):
- `pc = ctx.prev_close()`; `None` → stand aside.
- **settled open = close of the first 5-min bar (~09:20)** — NOT the raw 09:15
  open (pre-open auction spikes, e.g. 2026-02-03 printed +4.86% and repriced in
  one bar). `gap = settled_open / pc - 1`.
- **Band:** trade only if `FLOOR ≤ |gap| ≤ 0.005`. `FLOOR` is not hand-set — it
  is derived by the pre-study (§3) as the smallest bucket whose premium
  excursion clears cost. Cap fixed at 0.5%.
- **Arm F side:** gap-up → PUT, gap-down → CALL.
- **Arm R side:** gap-down that reclaims its open by 09:30 → CALL (arm R ignores
  gap-ups and non-reclaim gap-downs).
- Strike = `ctx.atm_strike(side)` (ATM).

### Entry (`on_bar`)
One trade/day. Arm F enters at the 09:20 settled-open bar; arm R enters at the
09:30 checkpoint if the reclaim condition holds. **6 lots.**

### Exit — FIXED before backtesting, never swept (`manage`)
`TwoStage(target_pts=BOOK, trail_gap=TRAIL, fraction=4/6, stop_pts=STOP)` plus
EOD backstop. All values are PREMIUM points:
- **Capture:** book **4 of 6 lots at +BOOK** premium points.
- **Trail:** trail the remaining **2 lots by TRAIL** premium points.
- **Stop:** full-size premium hard-cap at **−STOP**.
- **EOD 15:15:** `Exit.market("eod")` — flatten backstop only, never the thesis.

`BOOK / TRAIL / STOP / FLOOR` are imported from `strategies/gap_fill_params.py`
(single source of truth, eng-review C2) — never hand-copied into the strategy.

## 3. Pre-study — `analysis/gap_fill_premium_scale.py` (predetermines the exit)

Path-aware (eng-review A2) and **train-window only: 2020-01 → 2024-12**
(eng-review A1 — so 2025-26 stays a genuine OOS test).

For every in-band gap day in the train window, `_select` the ATM option, then
**walk the option premium minute-by-minute** and record:
- **favorable excursion up to the point a fixed candidate stop would fire**
  (the candidate stop is a FIXED rule = a percentile of adverse excursion, NOT
  swept — otherwise it becomes the fit we are avoiding),
- **adverse excursion (MAE).**

Freeze, by rule (never by P&L search), per gap sub-bucket:
- `STOP` = **p75 of MAE** (survives noise).
- `BOOK` = **p40 of the (stop-truncated) favorable excursion** (reachable, not
  the peak).
- `TRAIL` = **0.5 × BOOK**.
- `FLOOR` = smallest gap bucket whose median favorable premium excursion
  exceeds **(round-trip spread + BOOK)**. Buckets below FLOOR are reported as
  diagnostics, not traded (eng-review A4).

Write the frozen values + the per-bucket excursion table + the derivation
window to `gap_fill_params.py` and the pre-study `_out.txt`. A pinning test
asserts the strategy imports match (C2).

**Perf (eng-review P-1):** slice each day's premium/spot windows ONCE and
iterate the slice; never call `SpotSeries.history()` per bar (documented trap).

## 4. Instrument & data

- ATM weekly NIFTY option; `Catalog(ROOT)` + `SpotSeries.from_index(ROOT,
  "5min")` + `MarketData(cat, spot, min_dte=1)`, `ROOT = "/Users/gokul/Desktop/
  tradealgo/data nifty "` (2020-01 → ~2026-02-25).
- **`min_dte=1`** (eng-review A3): nearest non-expiry-day, for intraday gamma +
  cheap premium — NOT the "avoid theta cliff" argument (that is an overnight
  concern; this trade is flat by 15:15).
- Costs: engine `CostModel()` + `Knobs(slippage_pct=…)`. Report base 0.25% and
  a realistic stress tier once base is known.

## 5. Backtest, metrics, honesty guards — `analysis/gap_fill_fade_run.py`

Load spot+catalog ONCE; reuse the single `MarketData` across every arm and null
(eng-review P-1). Run:
- **Arm F** (symmetric fade) and **Arm R** (reclaim-reversal long), head-to-head
  on the same days (eng-review OV1).
- **Null 1 (timing):** fade-side ATM, hold to EOD, no scale-out.
- **Null 2 (direction):** the same frozen exit on the CONTINUATION side
  (gap-up → CALL, gap-down → PUT) — if fade doesn't beat continuation, direction
  has no edge (eng-review A5).

Report full-period + **per-year** + **OOS split (2020–24 vs 2025→2026-02-25)**,
broken out **by side and gap sub-bucket** (incl. the below-FLOOR diagnostic
buckets). Metrics per cell: trades, net, PF, win%, t-stat, avg debit, avg
net/trade, exit-reason mix (book / trail / pstop / eod). A run must beat BOTH
nulls to be a real edge. Regime-stability (count of positive years) is the
headline, not the full-sample mean.

**No stage-gate** (eng-review OV2): build all four deliverables and let the
backtest render the verdict in one pass.

## 6. Testing (TDD)

Unit (`tests/unit/test_gap_fill_fade.py`), synthetic ctx/chain per
`test_cpr_breakout.py` / `test_orb_spread.py`:
- `_select`: skip-no-prevclose, skip-below-FLOOR, skip-above-cap, side up→PUT /
  down→CALL, settled-open uses 09:15-bar close, arm-R reclaim condition.
- `on_bar`: no-entry-before-0920, entry-at-decision-bar, one-trade-per-day.
- `manage`: book path, **trail path**, **stop path**, EOD path.
- **Golden fixture for the pre-study path-aware truncation** (eng-review
  critical gap G1) — a known premium path → known truncated MFE/MAE.
- **Params pinning test** (C2).

Integration (eng-review T1): run `GapFillFade` over ~5 known gap days from the
real cache; assert side/strike/exit-reason — catches chain-selection, `spot_at`,
and settled-open wiring bugs that synthetic units miss.

## 7. Deliverables

- `analysis/gap_fill_premium_scale.py` + `_out.txt` (pre-study; train-window;
  writes params)
- `strategies/gap_fill_params.py` (frozen BOOK/TRAIL/STOP/FLOOR + window)
- `strategies/gap_fill_fade.py` (arms F + R; `_select` selector)
- `analysis/gap_fill_fade_run.py` + `_out.txt` (both arms + both nulls)
- `tests/unit/test_gap_fill_fade.py` (units + golden + pinning) and the
  integration slice
- commit alongside outputs (repo convention)

## 8. Kill criteria (honest GO/NO-GO)

NO-GO if any of:
- net negative after costs at the plain frozen config, OR
- positive only in a single regime (fails positive-years count), OR
- fails to beat the hold-to-EOD (timing) null → the scale-out adds nothing, OR
- fails to beat the continuation (direction) null → the fade side has no edge,
  it's just long gamma on gap days, OR
- edge lives only in a below-FLOOR / cost-dominated bucket.

GO → NIFTY ATM depth audit (see `TODOS.md`), then stress pass, then live gate.
