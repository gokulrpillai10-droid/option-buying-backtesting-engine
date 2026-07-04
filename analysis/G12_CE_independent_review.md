# G12-CE — Independent Review

*Reviewer: Claude (build-strategies engine). Source: `tradealgo/real_data_trades_5yr.csv`
(106 real-priced trades, 2021-01-22 → 2026-02-06), cross-read against
`G12_Strategy.md`, `G12_5yr_RealData.md`, `G12_Quant_Review.md`. Numbers below are
my own recompute with real entry/exit prices + slippage (1.5pt/side, +2pt on stops)
+ India F&O cost model, 3 lots, ₹5L capital.*

---

## 1. What the strategy is

A **directional CALL-buying** bounce play. When Nifty drops ≥80 pts from the prior
day's high before 12:15, and 7–8 filters pass (no gap-up, pullback genuine, candle
closes strong, not expiry-Wednesday), buy a **deep-ITM call (strike = ATM − 150)**
and ride a Fibonacci recovery, exiting on Fib extensions / a trailing stop / EOD.

**The construction is smart.** A 150-pt ITM call has delta ~0.8 — it behaves like a
*partial future*, not a lottery ticket. That deliberately **sidesteps most of the
theta/vega tax** that kills ATM option buying. This is exactly the "monetize a
directional edge with a delta-1-ish instrument" conclusion from our own research —
G12 is the CALL/bounce mirror of our gap-down (PUT/continuation) work.

---

## 2. The headline conflict: 1-year vs 5-year

| Report | Period | Net / Return | Sharpe | Win | t-stat |
|---|---|---|---|---|---|
| `G12_Strategy.md` (1 yr) | May'25–Apr'26 | +₹252k / **50%** | 2.3 | 54% | 2.85 (CE) |
| `G12_5yr_RealData.md` (5 yr, real) | 2021–2026 | +₹185k / **7.3% ann** | — | 42% | — |
| **My recompute** (5 yr, real) | 2021–2026 | **+₹163k / 6.5% ann** | — | 42% | **1.40** |

My recompute matches their 5-year report (±12%, the gap is cost-model detail). **The
5-year number is the truth; the 1-year 50% is a favorable-regime slice.** May'25–Apr'26
was an unusually volatile, gap-down-rich, flat/bearish tape — the ideal habitat for a
"buy the morning bounce" strategy. It is not representative.

---

## 3. Three structural problems (my evidence)

### 3.1 Not statistically significant over 5 years
**t-stat = 1.40** on 106 trades (need > 2 for 95% confidence). The 1-year t=2.85 was
the good slice. On the full sample you **cannot reject "no edge."**

### 3.2 Extreme tail dependence (lottery structure)
| Cut | Net |
|---|---|
| All 106 trades | +₹163,020 |
| **Excl. top-5 winners** | **+₹16,053** (≈ breakeven) |
| **Excl. top-10 winners** | **−₹111,132** (negative) |
| T3 (trailing) winners — 38 trades | +₹624,620 |
| Non-T3 — 68 trades | −₹461,599 |

The **top 5 trades are ~90% of all profit**; the other 101 net ~zero. Remove the top
10 and it loses. This is the *same* lottery profile that made me reject the ORB call —
the edge is exposure to a handful of rare sharp-recovery days, not a repeatable signal.
It also means the entire P&L rides on **perfect trailing-stop execution** of those few
winners (live trail on weekly options is hard — flagged in their own review).

### 3.3 Regime-dependent and lumpy by year
| Year | Trades | Net | Win | PF |
|---|---|---|---|---|
| 2021 | 10 | +₹6,125 | 30% | 1.17 |
| 2022 | 28 | **−₹28,672** | 32% | 0.81 |
| 2023 | 13 | +₹38,730 | 62% | 1.82 |
| 2024 | 20 | +₹5,015 | 35% | 1.05 |
| 2025 | 28 | +₹65,152 | 46% | 1.47 |
| 2026 (part) | 7 | +₹76,669 | 57% | 4.86 |

Loses in 2022, ~flat in 2021/2024, with profit concentrated in 2023/2025/2026 (incl.
the Q1-2026 tariff selloff). The strategy needs a volatile/choppy tape; in a calm bull
run it barely fires and barely earns.

---

## 4. What's genuinely good

- **Sound mechanism.** Post-80-pt-drop intraday partial recovery is a real, >50%
  base-rate pattern. The 0.44 retrace entry / 0.22 stop is a legitimate asymmetric setup.
- **Deep-ITM strike = the right instrument** (delta ~0.8, low theta/vega bleed). This is
  why G12-CE can show a positive 5-year edge where our ATM-buying tests could not.
- **Liquidity is real** — 100% of entries/exits on actually-traded strikes (median vol
  35k/22k contracts); deep-ITM fills exist.
- **Look-ahead was found and removed correctly.** Their own correction (pricing at the
  5-min bar *close*, not *label*) cut a ~₹440k phantom gain — exactly the discipline our
  engine enforces. The ledger I reviewed is the corrected one.
- **PF 1.32, avg win/loss ~2.9×** — a real asymmetric payoff that tolerates a 42% win rate.

---

## 5. Verdict

**A real but modest, fragile edge — not the 50% machine the 1-year report suggests.**

- Honest through-cycle expectancy: **~6.5–7.3% annualized at 3 lots**, barely above the
  6.5% risk-free rate, with −12% to −13% max drawdown.
- **Statistically marginal** (t=1.40 over 5 yrs) and **lottery-structured** (top-5 trades =
  the whole edge) and **regime-dependent** (needs volatility).
- It is the most *legitimate* option-buying strategy I've seen in this project — because
  the deep-ITM construction and post-drop bounce logic are structurally sound — but its
  honest risk-adjusted return is small and depends on flawless trailing-stop execution.

**Deployment:** a 1–3 lot pilot is defensible; do **not** scale (−12% DD at 3 lots → −35%
at 9 on a 42%-WR lottery). Size to ~10% allocation, expect multi-month dry spells, and
the edge stands or falls on **algorithmic trailing-stop execution** of the rare winners.

---

## 6. Independent re-run on the build-strategies engine (DONE)

I re-implemented the FIB state machine from scratch (`analysis/g12_ce_engine_rerun.py`),
ran it on **our** Forward series, priced every fill from **our** Data-v3 store, with our
cost model + slippage. Results:

| Version | Trades | Net | PF | t-stat |
|---|---|---|---|---|
| Their filtered (real prices) | 106 | **+₹163–185k** | 1.32 | 1.40 |
| **My core FIB, no filters** | 443 | **−₹448,700** | 0.82 | −1.98 |
| My filtered (F7 mis-set) | 140 | −₹235,124 | 0.72 | −1.82 |
| **My filtered (F7 = skip Wed, matches their code)** | 141 | **−₹25,134** | **0.97** | **−0.14** |

**Findings:**
1. **Pricing is sound.** Re-pricing their 106 trades from their own entry/exit prices
   reproduces +₹163k — their look-ahead fix and cost model are correct.
2. **The core mechanism has NO edge.** Unfiltered, the 80pt-drop + 0.44-retest + Fib
   setup loses **−₹449k (PF 0.82)**. 100% of the reported edge is in the filter stack.
3. **The edge does not reproduce independently.** My faithful filtered reimplementation
   lands at **breakeven (−₹25k, PF 0.97, t≈0)** — not +₹163k. The residual gap traces to
   implementation choices I can't fully match (their signal runs on **Kite spot**; mine on
   our **Forward** ≈ spot+carry; plus 5-min bar alignment).
4. **Why a small implementation diff swings the result so much: extreme tail dependence.**
   In every version, the **top ~10 trades are the entire P&L** (my filtered: T3 winners
   +₹771k vs non-T3 −₹796k; excl top-10 → −₹296k). When the whole result rides on a
   handful of big-winner days, whether your exact implementation *catches* those specific
   days (spot vs forward, bar timing) flips +₹163k ⇄ breakeven.

## 7. Final verdict
**G12-CE is not a robust, reproducible edge.** It is a **filter-selected, tail-dependent,
implementation-sensitive** strategy. Its P&L swings from +₹163k → breakeven → −₹449k
depending on (a) whether the filters are applied and (b) micro implementation choices,
because the "edge" is a few big-winner days, not a repeatable signal. Combined with t≈0–1.4
(not significant), the deep-ITM construction being sound is not enough. **Do not deploy on
the strength of the backtest.** If pursued, it must be paper-traded live to see whether the
rare big winners actually materialize under real execution — they are the entire thesis.
