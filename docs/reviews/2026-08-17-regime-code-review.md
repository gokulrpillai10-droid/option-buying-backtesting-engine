# Regime detector — code review findings (FIXED 2026-08-18)

**Date:** 2026-08-17 (findings) / 2026-08-18 (fixed + re-run)
**Reviewed:** `analysis/regime_vol.py`, `analysis/regime_straddle_pnl.py` + their tests
**Method:** /code-review high-effort workflow (18 agents, independent verify pass) — 13 findings survived verification.
**Decision:** user chose "fix correctness + re-run".

> **STATUS: all 6 correctness findings + 3 cleanups fixed, test-first (6 new
> failing→green tests; full suite 79 green), and both drivers re-run.** The
> directional verdict held: the regime is a NO-GO, short-vol premium dominates.
> Corrected numbers — straddle COMBINED **PF 0.83 / −122k** (was 0.69/−189k),
> always-short **PF 1.92** (was 7.78), **48 priced** (was mis-reported 62); sharpened
> window now **2020-11-03..2026-07-17** (COVID correctly excluded as inside the VIX
> warm-up; sanity falls back to the price-only axis, still 85% PASS). The `_out.txt`
> files are now trustworthy.

> Original bottom line (pre-fix): the straddle NO-GO verdict and the sharpened labels
> were both computed WITH these bugs; the exact numbers were flattered/corrupted until
> fixed + re-run.

## Correctness (fix these, test-first)

| # | Sev | File:line | Bug | Fix |
|---|-----|-----------|-----|-----|
| 1 | P0 | `regime_straddle_pnl.py:~101` (`straddle_trade`) + test `:61` | Slippage only half-applied: `SpreadLeg.entry` must be ALREADY entry-slipped by the caller (see `engine/backtest.py` pre-slip); `SpreadPosition.close` slips only the exit. Passing raw closes as entry → ~50% of round-trip slippage dropped on the GO gate. | Pre-slip entry legs: buy `entry*(1+slip)`, sell `entry*(1-slip)` before building `SpreadLeg`; add a test asserting entry-side slip is present (both long & short). Fix the `both_sides`/slippage test that failed to catch it. |
| 2 | P0 | `regime_vol.py:27` (`expanding_percentile`) + `:82` | NaN current value → `(s<=NaN).mean()==0.0` → day bucketed VIX-"low"→spurious "calm" (8 days). Interior NaNs also inflate the window denominator → every post-gap day biased toward calm. Root cause: `vix.reindex(lab.index)` injects 17 NaN gap days. | In `expanding_percentile`: return NaN when the current value is NaN, and exclude NaNs from the window count. Handle the reindex gaps explicitly (drop or ffill-with-flag). Tests: NaN current → NaN; NaN-in-window doesn't shift a later percentile. |
| 3 | P1 | `regime_straddle_pnl.py:~207` (main header) + `:231` | Prints `len(rows)` as "priced" but ~19 are dropped later in `_price_side`; committed output says "62 priced" while real n=43. | Count actual priced trades; report labeled / priced / skipped honestly. |
| 4 | P1 | `regime_vol.py:82` (`sharpen_labels`) | "calm" needs `vix_low=True` but the 252-day VIX warm-up forces it False for ~first year → Calm structurally impossible 2019-10→~2020-10 (mislabeled, not truncated). | Truncate the sharpened labels to where the VIX percentile is valid (like `regime_finder` truncates its warm-up), OR mark warm-up rows unknown rather than not-calm. |
| 5 | P1 | `regime_vol.py:89` (`fear_rising`) | On reindexed series with NaN gaps, `vix > vix.shift()` is False across gaps → "cooling" misread on the 17 gap days. | Compute fear_rising on the gap-free VIX (before reindex) or guard NaN; test a gap boundary. |
| 6 | P1 | `regime_straddle_pnl.py:~125` (`_episode_entries`) | Enters at the SAME-day EOD off a same-day-close label; `regime_finder` documents `lagged()` (shift +1) as "the executable one". P&L optimistically timed (live trader acts next session). | Enter at next session's open/EOD (use `lagged()` labels or shift entry +1 day); re-run. |

## Cleanup (lower severity)

| # | File:line | Item | Fix |
|---|-----------|------|-----|
| 7 | `regime_vol.py:26` | `expanding_percentile` is O(n²) (`.expanding().apply` python lambda) | Vectorize (single-pass rank / rolling rank). |
| 8 | `regime_straddle_pnl.py:120` | `_episode_entries` hardcodes calm→short/stormy→long instead of reusing tested `direction_for` | Iterate labeled days through `direction_for` (single source of truth). |
| 9 | `regime_vol.py:149` | string-keyed `.loc[str(b), ...]` fragile on DatetimeIndex | Index by the Timestamp/positional, not a stringified date. |

## Re-run checklist (after fixes)
1. `PYTHONPATH=. pytest -q` green (incl. new NaN + slippage tests).
2. Re-run `analysis/regime_straddle_pnl.py` → refresh `_out.txt` (expect worse net/PF after full slippage + lagged entry; verify NO-GO still holds, correct n=43).
3. Re-run `analysis/regime_vol.py` → refresh `_out.txt` (expect corrected calm counts; COVID sanity still PASS).
4. Update `[[regime-buysell-detector]]` memory with corrected numbers.
