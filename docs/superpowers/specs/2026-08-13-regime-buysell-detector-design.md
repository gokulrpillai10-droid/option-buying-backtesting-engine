# NIFTY Buy-vs-Sell Regime Detector — Design (single-phase, full system)

**Date:** 2026-08-13
**Status:** Reviewed via /plan-eng-review (scope reversed to single-phase per user; see Decision log D2).
**Branch:** gap-fill-fade
**Supersedes scope of:** `2026-07-18-regime-finder-design.md` (that spec's Step-1 labeler is reused, not replaced).
**Runtime it plugs into:** `2026-08-12-live-execution-engine-design.md` (paper-first live engine + daily loss limit).

## Goal

A daily **regime detector** that indicates whether the derivatives market is
**favorable for option BUYING or option SELLING** (or NEUTRAL), then routes the
matching strategy family into the execution engine. Matches the user's three
architecture diagrams (detector → buy/sell/neutral branch → strategy logic →
execute → store fills → update positions → feedback).

Priority (user-stated): **buy-vs-sell suitability > direction.** "Detecting the
regime properly" = the buy/sell call is validated OUT-OF-SAMPLE against nulls,
not merely plausible.

## System pipeline

```
 VIX Historical ┐
 Spot OHLC      ├─▶ Feature Engineering ─▶ REGIME CLASSIFIER ─▶ BUY / SELL / NEUTRAL (+confidence)
 Option OHLC+Greeks ┘   (IV/RV, VIX %ile,     (vol-centric composite)      │
                        vol dynamics, skew,                                 ▼
                        MA200 trend, breadth)              ┌────────────┬────────────┬───────────┐
                                                           │ Buy-fav    │ Neutral    │ Sell-fav  │
                                                           │ buying     │ do nothing │ selling   │
                                                           │ strategies │ /exit      │ strategies│
                                                           └─────┬──────┴─────┬──────┴─────┬─────┘
                                                                 └──── position-aware ─────┘
                                                                            ▼
                                              orders ─▶ EXECUTION ENGINE (paper-first, loss-limit)
                                                                            ▼
                                     store fill/IV/Greeks/OHLC ─▶ OUTCOME JOURNAL ─▶ adaptive weights
```

## Data sources

| Input | Backtest (on disk now) | Live (daily EOD) |
|---|---|---|
| India VIX (IV, percentile) | `analysis/india_vix.csv` (2019-06→2026-07) | Kite EOD quote |
| NIFTY spot OHLC (RV, MA200) | `analysis/nifty_daily.csv` (2019-01→2026-07) | Kite EOD |
| Option OHLC + Greeks (ATM IV, skew, OI) | Data-v3 chain (`engine/data/chain.py`, ends 2026-02-25) | Kite chain EOD |
| NIFTY50 breadth (% > MA20/50, adv-decl) | Kite bulk historical + PIT membership | Kite EOD per constituent |

The detector runs on **daily EOD** data (prior-close in → today's bias out, look-ahead safe). No intraday feed needed for detection.

## Detector — the classifier

Rule-based **vol-centric composite** (no ML — the source notebook's per-regime RF
was circular; `regime_finder.py` already rejects ML). Modules score in `[-1,+1]`,
+1 = strongly favors BUYING:

| Module | Weight | Signal | Buy vs Sell |
|---|---|---|---|
| **IV/RV (vol-risk-premium)** | ~40% | India VIX vs **21-trading-day** realized vol (tenor-matched to VIX's 30-cal-day) | IV≫RV → SELL; RV catching / IV cheap → BUY |
| **IV level** | ~20% | VIX **expanding-window (min-1y)** percentile | low %ile → BUY; high → SELL |
| **Vol dynamics** | ~15% | RV & VIX expanding vs contracting | expanding → BUY; contracting → SELL |
| **Skew / chain Greeks** | ~10% | ATM IV, put/call IV skew, OI tilt from Data-v3 chain | rich puts / fear skew → context for SELL vs BUY |
| **Trend confirmer** | ~10% | MA200 direction (reuse `regime_finder.label_regimes`) | trend → directional BUY favorable |
| **Breadth** | ~5% | NIFTY50 PIT: % > MA20/50, adv-decline | health/fragility confirmer |

**Composite:** `score = Σ wᵢ·moduleᵢ`. `score > +thr → BUY`, `< −thr → SELL`,
else NEUTRAL. **Confidence = module agreement** (sign consensus); low agreement →
NEUTRAL. **4-of-5-day persistence filter** (`regime_finder.persistence`) to stop flip-flop.

**Adaptation (self-calibrating + bounded outcome-weighting):**
- Expanding-window recalibration of percentiles/thresholds (look-ahead safe; the honest "improve day by day").
- **Outcome journal** (`data_regime/journal.parquet`): each day logs bias + module scores; after horizon H (~21d) writes ground truth (straddle P&L sign) + each module's correctness.
- Bounded weight nudging toward better modules: **min ≥40 resolved samples**, weight clamps [5%,50%], EWMA shrinkage to base weights, re-normalized. Base weights are the fallback; adaptation disables if it degrades on the journal.

## Validation — the thermometer (GO gate)

The detector's buy/sell call is proven by **economic straddle P&L**, NOT by a
realized-vs-implied hit-rate (tautology risk — grades the signal on a forward
restatement of its own IV/RV input; the exact class the prior regime review rejected).

- **Standalone straddle harness** (`analysis/regime_straddle_pnl.py`): price ATM
  call+put from the Data-v3 chain at entry, hold to ~21d / next expiry, exit;
  long on BUY days, short on SELL days; sign gives P&L. Reuses `CostModel` +
  0.25% slippage + date-keyed `lot_size` + `Chain.snapshot_at`. **Does NOT use the
  long-only intraday `Runner`** (`runner.py:106` prices a long intraday exit; net-short multi-day has no path).
- **GO metric:** OOS walk-forward straddle P&L beats ALL nulls net of costs:
  always-buy, always-sell, VIX-percentile-only, random bias, block-shuffled regime calendar.
- **Split-half agreement** (2021-23 vs 2024-26) as a hard GO condition.
- **Sample guards:** ≥30 P&L days AND ≥3 distinct buy/sell episodes per bucket, else "insufficient sample," ineligible to drive the verdict.
- Realized-vs-implied hit-rate kept as a **descriptive check only**.
- Window: straddle P&L capped at options-data end **2026-02-25**; report the usable episode count explicitly (no silent truncation).

## Strategy routing & execution

- **BUY-favorable** → deploy buying strategies (CPRBreakout + future directional buys), position-aware.
- **SELL-favorable** → deploy selling strategies (premium selling). **UNVALIDATED — built but live-gated.**
- **NEUTRAL** → do nothing / exit open positions.
- Orders flow through the **execution engine** (`2026-08-12` spec): paper-first, daily-loss-limit kill-switch, order/fill journal feeding the outcome journal.

**CAPITAL-SAFETY GATE (non-negotiable, runtime not phase):** live real-money
SELLING is disabled until (1) the thermometer GO passes AND (2) `allow_short_open`
is deliberately enabled. Short premium = margin/assignment/naked-short risk.
Building the selling code is in scope; flipping it live is gated.

## Module layout

```
engine/regime/
  features.py     # IV/RV (21d), VIX %ile (expanding), vol dynamics, skew/greeks
  breadth.py      # NIFTY50 breadth from chain of constituents
  membership.py   # point-in-time NIFTY50 membership (configs/nifty50_membership.csv)
  classifier.py   # composite -> BUY/SELL/NEUTRAL + confidence + persistence
  journal.py      # outcome journal + resolution + adaptive weights
  contract.py     # RegimeBias dataclass + classify(as_of) entry point
analysis/
  regime_straddle_pnl.py   # validation thermometer (+ _out.txt)
  regime_buysell_run.py    # detector run + calendar + sensitivity (+ _out.txt)
configs/nifty50_membership.csv
data_regime/journal.parquet
```
Reuses: `regime_finder.persistence/label_regimes`, `engine/data/chain.py`,
`engine/execution/costs.CostModel`, `engine/instruments.lot_size_for`,
`engine/equity/kite_loader.make_kite`.

## Test coverage (100% target)

```
[+] engine/regime/features.py
  ├── iv_rv_ratio()        [★★★] 21d tenor; high IV/RV→sell score; low→buy; flat/zero-vol guard
  ├── vix_percentile()     [★★★] expanding min-1y; look-ahead-safe (no full-sample); warm-up truncation
  ├── vol_dynamics()       [★★]  expanding vs contracting sign
  └── skew()               [★★]  chain skew from snapshot; missing-greek → None passthrough
[+] engine/regime/classifier.py
  ├── composite/threshold  [★★★] buy/sell/neutral boundaries; confidence=agreement; low-agree→neutral
  └── persistence          [★★]  4-of-5 boundary (reuse regime_finder test pattern)
[+] engine/regime/membership.py active_on(date) [★★★] add/remove boundaries; as-of correctness
[+] engine/regime/breadth.py    [★★] %>MA on PIT universe; empty universe guard
[+] engine/regime/journal.py    [★★★] resolution after H; adaptive weights: min-sample gate, clamps, shrinkage, revert-on-degrade
[+] analysis/regime_straddle_pnl.py [★★★][→backtest] long/short sign; costs applied; hold-to-expiry; nulls; sample guards
```
Golden: reuse the "identity" discipline — a fixed synthetic vol series → known bias sequence.

## Failure modes (each new codepath)

| Codepath | Realistic failure | Test? | Handled? | Visible? |
|---|---|---|---|---|
| VIX/NIFTY date join | calendar mismatch → NaN ratio | yes | inner-join on common days | n/a |
| VIX percentile | full-sample leak | yes | expanding-only, asserted | n/a |
| membership as-of | stale/current list applied back (survivorship) | yes | PIT table + test | n/a |
| straddle chain read | missing ATM strike / untraded | yes | is_traded gate → skip+log | logged |
| adaptive weights | chase noise on thin sample | yes | min-40 gate + clamps + revert | journal report |
| live selling | naked-short before validation | n/a | runtime GO gate default-off | hard-blocked |

## NOT in scope

- Intraday regime switching (daily EOD only).
- Term-structure (VIX futures) input — data not available locally.
- ML nowcasting — rejected (circular; prior review).
- Auto-flipping live selling — gated on thermometer GO + explicit enable.
- The execution-engine runtime itself — separate `2026-08-12` spec.

## What already exists (reuse, don't rebuild)

- `regime_finder.py` — persistence + MA200 trend + relative-vol axis (reused for trend confirmer).
- `engine/data/chain.py` + `market.py` — chain snapshots with Greeks (reused for skew + straddle).
- `engine/execution/costs.CostModel`, `engine/instruments.lot_size_for` — costing (reused).
- `engine/equity/kite_loader.make_kite` — live/bulk Kite (reused for breadth + live EOD).
- `analysis/india_vix.csv`, `nifty_daily.csv` — detector inputs, on disk.

## Decision log (this review)

| ID | Decision |
|----|----------|
| D1 | Review target = this buy/sell detector |
| D2 | Scope: **single-phase full build** (reversed the earlier reduce-and-gate; user directive). Breadth + adaptivity + chain-Greeks inputs all IN. |
| I1 | RV = 21-trading-day, tenor-matched to VIX |
| I2 | VIX percentile = expanding window, min-1y (doubles as look-ahead-safe "improve day by day") |
| I3 | GO metric = economic straddle P&L (not realized-vs-implied; tautology) |
| I4 | Thermometer = standalone chain P&L, engine untouched (long-only Runner can't do net-short) |
| I5 | DRY: reuse `regime_finder` primitives; promote to `regime_core.py` on GO |
| I6 | Classifier inputs INCLUDE chain IV/Greeks/skew + VIX + spot (full detector) |
| SAFETY | Live selling gated on thermometer GO + explicit short-open enable (runtime gate) |
