# NIFTY Regime Finder — Design

**Date:** 2026-07-18
**Status:** Approved scope: Step 1 only. Step 2 gated on Step 1 output review.
**Origin:** External `Regime_based_project.ipynb` (analyzed 2026-07-18: backtest ~half in-sample,
ML high-vol recall 16% OOS — but the rule-based labeler is sound apart from two bugs).
Reviewed via /plan-eng-review (5 findings resolved, outside-voice challenge absorbed).

## Goal

Answer, with evidence and minimal build: *does regime conditioning improve our deployed
strategies?* Step 1 produces the NIFTY regime history so we can see what regimes even
existed before spending effort on P&L attribution.

## Step 1 (this build): `analysis/regime_finder.py`

One throwaway-grade analysis script, repo convention (committed with `_out.txt`).
No engine changes, no tearsheet changes, no new modules.

**Input:** `analysis/nifty_daily.csv` (^NSEI spot daily OHLC 2019-01 → present, 1,859 rows,
fetched 2026-07-18). NOT `daily_underlying.csv`: that file starts 2020-01, which puts the
COVID crash inside the labeler's ~110-day warm-up (labels would only begin 2020-06-15),
and it carries one known-bad row (2023-03-01 close 1,702 — the engine integrity gate's
1/1517). Cross-checked on 1,518 overlap days: agreement within ~0.5% (options-derived
vs spot basis), sole exception the bad row above. Starting 2019 absorbs the warm-up
before COVID and extends the calendar to the present.

**Labeler — two axes, each defined by what it does well (REVISED 2026-07-18 after
the first real-data run, see "Directional redesign" below):**

- **Volatility axis (high_vol / low_vol) — relative, kept from the notebook.**
  20-day rolling vol of daily returns, z-scored against a 90-day rolling baseline,
  thresholds ±0.5. This axis is realistic as-is: it cleanly caught COVID (73% high_vol),
  the 2021 top, and the 2022 corrections. Vol genuinely IS a relative concept.
  - Fix A: zero-std guard on the z-score (flat stretches must not produce inf labels).

- **Direction axis (bull / bear) — ABSOLUTE, price vs 200-day MA + slope.**
  `bull = close > MA200 AND MA200 rising` (slope over ~20d > 0);
  `bear = close < MA200 AND MA200 falling`; otherwise neutral/transition.
  Replaces the notebook's relative trend z-score, which was mean-reverting and
  labeled ~25% of the 2023-24 rally as "bear" (see redesign note). The absolute
  gate is Layer 1 — the standard directional regime filter — and needs no new data.

- **Stable labels:** raw condition true today AND ≥4 of last 5 days (persistence filter),
  applied to bull, bear, low_vol.
- **Warm-up:** explicit truncation (the notebook's `dropna()` was a no-op on int cols).
  Now governed by the 200-day MA → labeling begins ~2019-10 (was ~2019-06). Immaterial.
- `lagged` view: labels shifted +1 day = what a live trader could act on (decision 1B).

### Directional redesign (2026-07-18) — why the trend axis changed

The first real-data run (Task 3) SANITY-FAILED the bull arm: the notebook's relative
trend definition (20d trend z-scored vs its own rolling 90d baseline) is mean-reverting,
so a steady rally never reads as persistent bull — every interim pause looks weak
*relative to the rally's own hot baseline* and stamps as bear. Measured: 2023-24 rally
(NIFTY +42%) showed stable_bull 27% but stable_bear 25%. Routing long exposure on that
"bear" label would de-risk repeatedly through a historic bull run — fatal to the thesis.

Empirical fix, validated before adopting: price vs 200-day MA recovers known history —
2023-24 rally 100% above a rising MA200 (0% bear), COVID 91% below MA200, 2022 correction
52% below (genuinely mixed). Decision: replace the trend axis with the absolute MA200+slope
gate; keep the relative vol axis. This makes the labeler an improved variant, not a faithful
port — the right call, since accuracy beats fidelity to a half-broken definition.

**Output (`regime_finder_out.txt`):**
1. **Regime calendar** — contiguous episodes: regime, start, end, duration in trading days.
2. **Episode counts** — per regime, total days and number of distinct episodes
   (sample-size honesty; episodes are the real n, not days — outside-voice finding 3).
3. **Sanity check** — assert known history is recovered: COVID crash (2020-02→05) labels
   HighVol/Bear; 2023–24 rally labels Stable_Bull. If not, the labeler is broken; stop.
4. **Sensitivity note** — same calendar at lookback 15/20/30 and z ±0.4/±0.5/±0.6:
   do episode boundaries move by days or by regimes?

**Tests (`tests/unit/test_regime_finder.py`):** synthetic rising → Bull/Stable_Bull;
falling → Bear; vol spike → High_Vol; flat series → no inf/NaN (zero-std guard);
warm-up rows excluded; persistence 4-of-5 boundary; lag shift at a regime flip.

## Step 2 (gated — only after reviewing the Step 1 calendar)

Strategy P&L attribution + verdict, per the eng-review + outside-voice decisions:
- CPR daily net P&L (2023–26) joined to **lagged** labels; trade days only; same-day
  column as reference (1B).
- **GO metric = counterfactual equity replay** (CPR curve with exposure throttled on
  lagged bear/high-vol days) vs. always-on: return, max DD, Sharpe — NOT loss-share
  buckets (tautology risk for vol-linked options strategies).
- Null benchmarks: block-shuffled regime calendar (matched episode structure), dumb
  baseline "throttle when trailing 20-day own-equity is down", and India VIX quantile
  throttle (`analysis/india_vix.csv`, 2019-06→2026-07, fetched 2026-07-18, cross-checked
  vs tradealgo copy). Labeler must beat all three.
- Split-half agreement (2023–24 vs 2025–26) as a hard GO condition.
- Sample guards: ≥30 P&L days AND ≥3 distinct episodes per bucket, else
  "insufficient sample" and ineligible to drive the verdict (4A + episodes).
- ADANIENT arm: descriptive table only, zero GO/NO-GO authority (T3).
- Verdict language: "suggestive", never "proven" — single-digit episode counts.

## Step 3 (only if Step 2 says GO)

Promote labeler to `analysis/regime_core.py` (or `engine/`), daily live runner
(Kite closes → per-symbol regime + strategy roster), manual deployment first.
Not designed further here on purpose.

## NOT in scope

Breadth pipeline (survivorship-biased), ML nowcasting (unjustified OOS), HMM/other
labelers, automated routing, tearsheet modifications (a standalone exporter will be
written in Step 2 instead — validated script stays untouched).

## Decision log

| ID | Decision |
|----|----------|
| 1B | Lagged labels decide; same-day shown as reference |
| 2A | Research-grade in `analysis/`; promotion only on GO |
| 3A→revised | CPR P&L via standalone exporter CSV (Step 2), tearsheet untouched |
| 4A→revised | Min-sample guard counts episodes (≥3) as well as days (≥30) |
| 5A→revised | Tests scoped to the labeler/bucketing functions actually built per step |
| T1–T4 | Counterfactual GO test + nulls + split-half; episode counting; ADANIENT descriptive-only; lightest build shape |
