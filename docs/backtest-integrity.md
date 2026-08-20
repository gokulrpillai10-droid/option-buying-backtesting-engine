# Backtest Integrity — audit + hardening

Authoritative record for the data-integrity / backtest-accuracy effort. Triggered
by a silent data-scoping bug (a multi-day parquet filtered on time-but-not-date →
`.iloc[0]` took an arbitrary day's row). Goal: **audit** past results for that
class of error, then **harden** so it can't recur.

## Trust standard (a result is "verified" only if all that apply hold)
1. **Integrity invariants pass** (the real net — catches always-present bugs).
2. **Reproduces its `_out.txt`** (table-stakes; catches drift, not baked-in bugs).
3. **Matches an external reference** where one exists (gold).
4. **Manual spot-check** by the owner.

## Invariants (`engine/data/integrity.py`)
| Dimension | Check | Mode |
|---|---|---|
| Scoping | `assert_unique_strikes` (no repeated strike in a snapshot), `check_snapshot` (one `Forward`/snapshot), `check_call_monotone` (corr(strike,call) ≤ −0.3) | **hard-fail** |
| Single-day | `check_single_day` | **hard-fail** |
| Timezone | `check_session_bounds` (IST 09:15–15:30) | **hard-fail** |
| Look-ahead | via `Chain` (as-of only) + per-strategy leak tests | **hard-fail** |
| Cost | range/bps sanity | soft-warn |
| Fill realism | next-bar fill (engine property, tested) | checklist |
| Survivorship/PIT | membership as-of | checklist (hard only in equity/breadth path) |

`strict=False` downgrades any hard-fail to a warning (exploration only).
Guarded readers `scoped_day()` / `snapshot_at()` / `one_row()` replace bare
`read_parquet` + `frame[mask].iloc[0]`. Calibrated on 125 real snapshots
(0 false-trips; corr min −0.96 / mean −0.72).

## Scope
- **Engine-run backtests** (through `Backtest`/`MarketData`/`Chain`) are
  structurally scoped and near-immune; `OptionChainSnapshot` now hard-fails on
  duplicate strikes. Audited by **`_out.txt` reproduction** (+ external parity).
  Deployed CPR (`cpr_tearsheet.py`) is here.
- **Raw-parquet analysis scripts** are the real exposure — **all get switched to
  the guarded reader, no exceptions.**

## Decision-driving list (silver audit required)
Criterion (written once, not a vibe): a script is *decision-driving* if it
**produced a committed result (`_out.txt` / tearsheet / a recorded GO/NO-GO) or
feeds a live/near-live candidate.** Pure live-data tooling is guarded but not
silver-audited.

**Silver-audit (raw-parquet):** regime_straddle_pnl, expiry_strangle_run,
g12_ce_spot, g12_ce_corrected, g12_ce_engine_rerun, ema_crossover_5min,
ema_crossover_study, ema_crossover_v3, ema_crossover_v4, ema_crossover_v5,
cpr_reversal_features, orb_study, combined_backtest, regime_analysis,
iv_timing_study, buying_triage, flow_signal_study, put_premium_test,
failed_call_reversal, mean_reversion_triage, gap_down_continuation,
hdfc_gapandgo, hdfc_patterns, hdfc_sr_levels, hdfc_sr_multiframe.

**Tooling (guard only):** chain_daily.

> Each script's *recorded verdict* is confirmed by reading its header during the
> sweep; a script found decision-relevant but unlabeled gets promoted here.

## `_out.txt`-diff protocol
Investigate every diff. On a real fix: regenerate `_out.txt`, and **explicitly flag
verdict impact — escalate loudly on any NO-GO→GO or GO→NO-GO flip.** Switching a
script to the hard-fail reader may make it crash: a crash is a *finding to
investigate*, not a regression to paper over.

## External parity (gold)
Reference CSV schema (matches the verified strangle export): `trade_group_id,
structure, entry_signal_tag, exit_signal_tag, strike, option_type, side, qty,
lot_size, n_units_at_entry, entry_ts, exit_ts, entry_price, exit_price, gross_pnl,
cost, net_pnl`. Timestamps **UTC**; P&L **per 1 lot**. Drop `analysis/
cpr_reference.csv` to activate `tests/integrity/test_cpr_parity.py`.

## Cadence (keep "opportunistic" from becoming "never")
- [ ] **Quarterly** review of this list.
- Rule: **re-classify on role change** (a NO-GO revived, a script promoted toward live).

## Status
- [x] Step 1 — integrity module + invariants + tests (commit 4dd2acf)
- [x] Step 2 — hot-path guard in `OptionChainSnapshot` (commit 4dd2acf)
- [x] Step 3 — CPR silver audit: **reproduces `_out.txt` bit-for-bit under the
  guarded engine, 0 invariant trips** (net +744,824, Sharpe 1.08, DD −122,916,
  2020-01→2026-02). Gold-pending on `analysis/cpr_reference.csv`; parity fixture stubbed.
- [~] Step 4 — switch all 26 raw-parquet scripts to the guarded reader (stakes-ordered):
  - [x] Batch 1 (commit 2e62060, no findings): regime_straddle_pnl (scoped_day →
    reproduces _out.txt), chain_daily seam (front_weekly → scoped_day, protects
    oi/vol_surface), g12_ce_corrected + g12_ce_engine_rerun (call_close →
    at_most_one_row), g12_ce_spot (already date+strike scoped, no change),
    cpr_reversal_features (reproduces _out.txt; aggregate reads, safe).
  - [ ] Batch 2+: ema_crossover_5min/_study/_v3/_v4/_v5, orb_study, combined_backtest,
    regime_analysis, iv_timing_study, buying_triage, flow_signal_study,
    put_premium_test, failed_call_reversal, mean_reversion_triage,
    gap_down_continuation, hdfc_gapandgo/_patterns/_sr_levels/_sr_multiframe,
    expiry_strangle_run, oi_pnl_triage, vol_surface_daily. (~20 remaining)
- [ ] Step 5 — finalize the list after the sweep
