# Combined Backtest Report — real options, slippage + full F&O costs

**Run:** `analysis/combined_backtest.py` | NIFTY 2020–2026 | 1 lot × 65 qty |
nearest expiry DTE≥3 | **1% slippage/side** | full India F&O cost model.
**Pre-engine sanity backtest** (minute granularity, O/H/L/C fills, no look-ahead).
Not yet golden-tested — but real option prices and real costs.

## Headline

| Strategy | Trades | Win | Net P&L | Avg/trade | PF |
|---|---|---|---|---|---|
| **A — ORB call breakout** | 249 | 36% | **+₹37,771** | +₹152 | **1.12** |
| **B — gap-down continuation put** | 280 | 46% | **−₹59,131** | −₹211 | 0.72 |
| **COMBINED** | 529 | 41% | **−₹21,360** | −₹40 | 0.96 |

- Gross P&L +₹18,768; **costs ₹40,128 = 214% of gross** → net negative.
- Avg cost/trade ₹76. Max drawdown ₹74,626.

## By year (combined net)
| Year | Net | PF |
|---|---|---|
| 2020 | +₹43,577 | 1.36 |
| 2021 | −₹30,289 | 0.67 |
| 2022 | +₹18,995 | 1.19 |
| 2023 | −₹21,234 | 0.42 |
| 2024 | +₹4,515 | 1.08 |
| 2025 | −₹24,375 | 0.69 |
| 2026 (part) | −₹12,548 | 0.57 |

Inconsistent: only 2020/2022/2024 positive.

## The key finding: index-point edge ≠ option-buying edge
Strategy B looked strong in the index-point study (PF 1.36) but **loses on real
options (PF 0.72)**. Why — exactly the caveat flagged earlier, now quantified:
1. **Delta dilution** — a +25 index-pt capture is only ~12 premium points on a
   ~0.5-delta put. The target is too small once converted.
2. **Double damage on stops** — when the underlying bounces (the stop), the put
   loses on delta *and* vega (IV crush on the bounce), so option losses exceed the
   symmetric index-point stop.
3. **Costs dominate** — gross is razor-thin, so slippage+fees flip it negative.

Strategy A survives because its exits are already in option-premium terms (−30%
stop / 25% trail), it rides trend days (right-tail winners), and IV behaves
favourably on up-breakouts.

## Verdict & next steps
- **Combined as specified is NOT viable** (B drags A down).
- **A (ORB call) is marginally positive after full costs** — the keeper; worth
  trying to strengthen (tighter entry filters, position sizing).
- **B needs rework or removal**: option-premium-based exits, larger targets, an
  IV/vega filter, or drop it. The index-point capture scheme does not transfer.
- All of this must be re-confirmed by the rigorous engine (golden tests, exact
  fills) before any paper trading.

## Follow-up: putting B's stop/targets on OPTION PREMIUM (not index points)
Tested (`analysis/put_premium_test.py`) — it got **worse**, conclusively killing
the long-put idea:

| Exit definition | Net | PF |
|---|---|---|
| Index-point (orig) | −₹59k | 0.72 |
| Premium −30% / 25% trail | −₹113k | 0.69 |
| Premium +50% book / trail / −30% | −₹154k | 0.70 |

And by gap size (premium, simple trail) — no salvage anywhere:
gap 20–50 PF 0.71 · gap 50–100 PF 0.69 · **gap >100 PF 0.68**.

**Conclusion: the gap-down downside edge is real in the INDEX but cannot be
monetised with LONG PUTS** — under any exit. Long puts pay the theta+vega tax
exactly when the frequent intraday V-recoveries hit. Capturing it would need a
delta-1 instrument (futures) or a non-long-premium options structure (debit
spreads / selling call spreads) — neither is "option buying". **Drop the long put.**
Strategy A (ORB call, +₹37.8k, PF 1.12) is the only option-buying survivor.

## Assumptions (to revisit in the engine)
Slippage 1%/side; brokerage ₹20/order; STT 0.1% sell premium; NSE txn 0.03503%/
side; SEBI ₹10/cr; stamp 0.003% buy; GST 18%; lot 65. Minute O/H/L/C fills;
stops assumed fillable at the level; partial scale-out modelled with fractional qty.
