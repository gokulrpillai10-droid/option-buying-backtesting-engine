# Regime-Driven Options Trading System — Project Overview

**Last updated:** 2026-08-13
**Branch:** gap-fill-fade
**Status:** Design complete + eng-reviewed. Implementation not started.

This document ties together the whole system: a daily **regime detector** that
decides whether the market favors **option buying or selling**, feeding a
**live execution engine** that deploys the matching strategies under a hard
daily loss limit.

---

## 1. The vision

> Each morning, before the market opens: classify the day's volatility regime →
> indicate BUY-favorable / SELL-favorable / NEUTRAL → deploy the strategy family
> that fits → run it live with a daily-loss kill-switch → record every fill and
> outcome so the system sharpens over time.

```
 VIX + Spot + Option Greeks ─▶ REGIME DETECTOR ─▶ BUY / SELL / NEUTRAL
                                                        │
                        ┌───────────────────────────────┼───────────────────────────┐
                        ▼                                ▼                            ▼
                 BUY (gamma):                     NEUTRAL:                    SELL (theta):
                 stormy / high vol                stand aside / exit          calm / low vol
                 → buying strategies (CPR)                                    → selling strategies
                        │                                                            │
                        └──────────────── EXECUTION ENGINE ──────────────────────────┘
                              (paper-first, daily loss-limit kill-switch)
                                              │
                              store fills / IV / Greeks ─▶ OUTCOME JOURNAL ─▶ self-calibration
```

**Core insight (validated on data):** the buy/sell decision is fundamentally a
**volatility-state** call.
- **Calm / low-vol / range-bound → SELL** (theta decay: short options bleed away, price doesn't threaten strikes).
- **Stormy / high / expanding vol → BUY** (gamma / directional: movement pays for the premium).

---

## 2. Sub-projects & sequencing

| # | Sub-project | Spec | Status |
|---|---|---|---|
| **A** | **Regime detector** (buy/sell classifier + validation) | `specs/2026-08-13-regime-buysell-detector-design.md` | Designed, eng-reviewed |
| **B** | **Live execution engine** (paper-first runtime + loss limit) | `specs/2026-08-12-live-execution-engine-design.md` | Designed (WIP, paused at test section) |
| **A′** | Prior regime labeler (Step 1, built) | `specs/2026-07-18-regime-finder-design.md` | **Built** (`analysis/regime_finder.py`) |

**Build order:** the detector's **two-sided straddle thermometer** (validation) is
the first thing that must pass — it proves the calm→theta and storm→gamma edges
survive costs before routing + execution are built on top. User elected a
single-phase full build; live real-money **selling stays gated** on that GO.

---

## 3. The regime detector (sub-project A)

**Inputs (daily EOD):** India VIX (`analysis/india_vix.csv`), NIFTY spot
(`analysis/nifty_daily.csv`), option OHLC+Greeks (Data-v3 chain). Live: Kite EOD.

**Classifier** — rule-based composite (no ML), modules score `[-1,+1]`
(+1 = BUY/gamma, −1 = SELL/theta):

| Module | Weight | Role |
|---|---|---|
| **Vol STATE (primary)** | 40% | `regime_finder` `stable_low_vol`→SELL / `high_vol`→BUY |
| Vol dynamics | 20% | expanding→BUY / contracting→SELL |
| IV/RV (VRP refinement) | 15% | VIX vs 21-day realized vol (tenor-matched) |
| IV level | 10% | expanding-window VIX percentile |
| Skew / Greeks | 8% | chain skew / OI context |
| Trend + breadth | 7% | minor direction/health confirmer |

Composite → threshold → BUY/SELL/NEUTRAL + **confidence** (module agreement) +
**4-of-5-day persistence**. Look-ahead safe (prior close in → today's bias out).

**Adaptation:** an outcome journal logs each day's bias + module scores; after a
~21-day horizon it records what actually paid, then nudges module weights
(bounded, min-40 samples, clamps, shrinkage, reverts on degrade). Percentiles
recalibrate on an expanding window — the honest "improve day by day."

**Validation (the GO gate):** a standalone two-sided straddle P&L harness —
LONG straddle on BUY/stormy days, SHORT straddle on SELL/calm days, held to
~21d, net of `CostModel` + slippage — must beat nulls (always-buy, always-sell,
VIX-only, random, block-shuffled calendar) OOS, with split-half agreement and
≥3-episode / ≥30-day sample guards. The **short-vol tail** (a calm patch ending
in a gap) must be captured, or the SELL edge is illusory.

**Verified power (2019-10→2026-02):** SELL/calm = 32 episodes (577 days),
BUY/storm = 33 episodes (458 days). Both sides well-sampled.

---

## 4. The execution engine (sub-project B)

Strategy-agnostic live runner that reuses the backtest's `Strategy` core
unchanged. **Paper-first:** `mode: paper|live` swaps only the order gateway;
feed, bars, risk, sizing, logging are identical. Sized for a ₹50–70k account.

**Daily loss limit (the guardrail):** realized + open MTM (adverse-priced) ≤
−limit → flatten everything + halt for the day. Checked every poll (~3–5s);
**halt is sticky across restarts** (day-state JSON). Feed = Kite REST polling
behind a swappable interface. Direct `kiteconnect` (`kite_loader.make_kite`);
the Kite MCP is broken upstream and untouched.

---

## 5. Data

| Data | Path | Range | Use |
|---|---|---|---|
| India VIX daily | `analysis/india_vix.csv` | 2019-06 → 2026-07 | IV, VIX percentile |
| NIFTY spot daily | `analysis/nifty_daily.csv` | 2019-01 → 2026-07 | RV, trend, vol state |
| Option chain (Data-v3) | `/Users/gokul/Desktop/tradealgo/data nifty ` | 2020-01 → **2026-02-25** | skew/Greeks + straddle validation |
| NIFTY50 constituents | Kite bulk + `configs/nifty50_membership.csv` (PIT) | to fetch | breadth (deferred/low-value) |

**Boundary:** the options chain ends **2026-02-25** — every validation number is
capped there. Refresh Data-v3 before any live decision.

---

## 6. Key decisions

| ID | Decision |
|----|----------|
| Detector reuses `regime_finder.py` (no ML — the source notebook's RF was circular) |
| SELL = theta/calm, BUY = gamma/storm; primary axis = vol STATE |
| RV = 21-trading-day (tenor-matched to VIX 30-cal-day) |
| VIX percentile = expanding window, min-1y (also the "improve day by day" mechanism) |
| GO metric = economic straddle P&L, NOT realized-vs-implied (tautology risk) |
| Validation harness is standalone (engine is long-only intraday; can't do net-short) |
| Full single-phase build (user directive), confirmed after a data power-check |
| **Live SELLING gated** on thermometer GO + explicit short-open enable (margin/naked-short risk) |
| Execution engine: paper-first, hard MTM daily-loss flatten+halt, sticky across restarts |

---

## 7. Open items / risks

- **Adaptive weight loop** activates only after ~1.5y of resolved samples — candidate to cut from v1.
- **Breadth via hand-curated PIT membership** is low value (7%) and error-prone — may proxy with a stable large-cap basket.
- **Prior track record is sober:** recent NIFTY vol/options studies (Gap-Fill Fade, intraday-equity, Reversal-Blast) were all NO-GO. The thermometer is the honest GO/NO-GO before building routing + execution.
- **Only one validated strategy exists** (CPRBreakout, a buying strategy). The SELL *regime* is testable, but a SELL *strategy* is unbuilt and unvalidated.

---

## 8. Next step

Implement sub-project A starting with the two-sided straddle thermometer
(reuse `regime_finder` labels + `CostModel`), get the OOS GO/NO-GO, then build
the detector + routing + execution per the specs.

**Related specs:** `docs/superpowers/specs/` — regime detector (2026-08-13),
execution engine (2026-08-12), regime finder Step 1 (2026-07-18).
