# Daily OI features — predictive-power triage first (NIFTY regime)

**Date:** 2026-08-18
**Branch:** gap-fill-fade
**Status:** design — eng-reviewed + outside-voice; scope reduced to a one-script gate
**Related:** [[regime-buysell-detector]], `analysis/regime_straddle_pnl.py`,
`analysis/flow_signal_study.py`, `analysis/regime_vol.py`, `engine/data/chain.py`,
`engine/data/market.py`, `analysis/data_integrity.py`

## Problem

The regime detector labels each day on direction (200-DMA) and vol mood
(realized-vol z + India VIX) and ignores options positioning. The user wants daily
OI features — **PCR, OI buildup/unwind, max-pain / support-resistance shift** —
surfaced **alongside** the regime and available to the strategy builder.

But the committed verdict is that regime *timing* is NO-GO: **short-vol premium
dominates regardless of regime** (`regime_straddle_pnl.py` — always-short wins; the
label adds nothing). So the honest first question is not "add OI to the label" but
**"does any daily OI feature predict the thing that actually pays — next-period
short-straddle P&L — incremental to just being always-short?"** If not, this is a
documented NO-GO like the last three, proven with one script, not a subsystem.

## Decision (eng review + outside voice)

**Build ONE triage script first; defer the module tree behind its verdict.**

1. **Validate against P&L, not the label.** Target = next-period ATM short-straddle
   return (the `regime_straddle_pnl` P&L), NOT next-day direction/vol-state (a
   proven-dead target). Feature earns its place only if it beats always-short
   *incrementally*.
2. **Tenor = front weekly, KEEP the pin window.** Do NOT roll off at DTE<3 — max-
   pain/walls/PCR are most informative in the contract's last 2–3 days (pin/dealer-
   gamma). (This reverses the earlier min-DTE-roll call after the outside voice
   showed it discards the highest-signal window; the earlier concern was the
   change-feature roll seam, handled below.) Guard the expiry-cadence change
   (Thu→Tue ~Sep 2025, `market.py`) so roll days are detected from real partitions,
   not a weekday rule.
3. **Settled-OI, backtest-only. No live-parity claim.** Data-v3 EOD OI is *settled*;
   live Kite intraday OI is *provisional/revised post-close* — they are not the same
   number. The triage uses settled Data-v3 OI only. Live OI is out of scope here.
4. **Honor the data_integrity quarantine + verify OI coverage.** Skip quarantined
   days (`analysis/data_integrity.py`); check CallOI/PutOI are non-zero/non-stale
   across 2020–2026 and truncate/flag any systematically degraded early period
   (early NIFTY weeklys had thin far-strike OI), the way `regime_vol` truncates the
   VIX warm-up.
5. **Look-ahead safe.** EOD(D) settled OI, consumed lagged +1; change features are
   NaN across a contract roll; level features (max-pain/walls) also step on a roll —
   compare only within the same contract or as distance-from-spot, not raw level.

## Architecture (minimal)

```
 Data-v3 chain (settled CallOI/PutOI, quarantine-filtered)
        │ front-weekly EOD snapshot per day (keep pin window)
        ▼
 ┌──────────────────────────────┐     reuse: regime_straddle_pnl episode/P&L,
 │ analysis/oi_features.py (pure)│     flow_signal_study ΔOI extraction,
 │  pcr, max_pain, oi_walls,     │     chain.py snapshot.oi
 │  classify_buildup             │
 └───────────────┬──────────────┘
                 ▼  per-day features (lagged +1)
 ┌──────────────────────────────────────────────┐
 │ analysis/oi_pnl_triage.py (+_out.txt)          │  THE GATE
 │  daily OI feature  →  next-period short-straddle│
 │  P&L, incremental to always-short; sign test,   │
 │  quarantine-honored, front-weekly               │
 └──────────────────────────────────────────────┘
                 │ GO?                         │ NO-GO?
                 ▼                             ▼
   (future) build the module tree      document NO-GO in the memory,
   + label parallel axis + dashboard   stop (one script spent)

 Independent, land now regardless:
 ┌──────────────────────────────┐
 │ analysis/chain_daily.py       │  DRY-extract eod_snapshot_for(day,tenor)
 │  (from regime_straddle_pnl)   │  out of regime_straddle_pnl — standalone win
 └──────────────────────────────┘
```

### Deliverables now
- **`analysis/oi_features.py`** — pure, tested: `pcr(snap, band)`, `max_pain(snap)`,
  `oi_walls(snap)`, `classify_buildup(d_call_oi, d_put_oi, price_ret)`. ~30 lines +
  tests; reused later if the triage is a GO.
- **`analysis/oi_pnl_triage.py`** (+`_out.txt`) — the one script. Reuses
  `regime_straddle_pnl` for the P&L target and `flow_signal_study` controls.
- **`analysis/chain_daily.py`** — DRY extraction of `_eod_snap` /
  `_collect_episode_snapshots`, landed independently (real win even if OI is NO-GO).

## What already exists (reused, not rebuilt)
`regime_straddle_pnl.py` (P&L target + episode machinery + `_eod_snap`),
`flow_signal_study.py:61` (ΔOI extraction + one-file triage template + the
intraday-OI precedent), `engine/data/chain.py:29,68,84` (`snapshot.oi` already
exposed — no new plumbing), `engine/data/market.py:52-57` (expiry cadence +
quarantine), `regime_vol` (percentile/warm-up-truncation pattern).

## NOT in scope (deferred behind a GO verdict)
- The 4-module subsystem (`oi_daily.py` daily-series + CSV, `oi_regime_triage.py`
  label integration, the parallel positioning axis, dashboard OI panel).
- Live OI (Kite snapshot/stream) and any settled-vs-live reconciliation.
- IV/Greeks recompute.
- Fixed-DTE constant-maturity tenor.

## Test plan
- `oi_features` (100%, pure): `pcr` full + ATM-band + zero-call-OI → NaN (not inf);
  `max_pain` on a hand-verified toy chain; `oi_walls` tie-break + all-zero → None;
  `classify_buildup` all 4 quadrants + flat/zero ΔOI boundary.
- `oi_pnl_triage`: committed `_out.txt` with sign test, incremental-to-always-short
  control, lagged, quarantine-honored. (Study, like `flow_signal_study`.)
- `chain_daily`: extraction is behavior-preserving — a golden test that the shared
  `eod_snapshot_for` reproduces `regime_straddle_pnl._eod_snap` output.

## Failure modes
- Settled-vs-provisional OI conflation → false live-parity. Guard: backtest uses
  settled Data-v3 OI only; live is out of scope (documented).
- Contract-roll level/step discontinuity → fake signal. Guard: within-contract or
  distance-from-spot features; change features NaN on roll.
- Thin/zero early-period OI → biased ranks. Guard: coverage check + truncate/flag,
  honor quarantine set.
- Zero call OI in a band → PCR div/0. Guard: NaN, not inf.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open→resolved | 2 arch decisions + scope reduced; outside voice reframed to one-script gate |
| Outside Voice | Claude subagent | Independent 2nd opinion | 1 | issues_found | 7 findings; strategic + triage-target + tenor accepted |

**CROSS-MODEL:** Outside voice overrode two of our locked calls — triage target
(label → **straddle P&L**) and tenor (min-DTE roll → **front-weekly, keep pin
window**) — and reduced scope from a 4-module subsystem to a **one-script P&L gate**.
User accepted all three (D4=A). Correctness fixes (settled-vs-provisional OI, honor
data_integrity quarantine) folded in unconditionally.

**VERDICT:** ENG CLEARED — build the triage script (TDD) + land the `chain_daily`
extraction; gate the subsystem on the triage verdict.

**NO UNRESOLVED DECISIONS**
