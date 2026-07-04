# Strategy: ORB Call Breakout (candidate v1)

**Status:** candidate edge from exploratory analysis. NOT yet validated by the
rigorous engine. Numbers are approximate (1% slippage only; brokerage/taxes/
liquidity gating not yet applied). The real test is `engine/execution` on the
full dataset with golden tests.

## The idea
Intraday opening-range breakout in NIFTY, expressed by **buying the ATM call**.
Long options need a real directional move to beat theta — so we only take the
setups where that move is most likely, and we cut the conditions that bleed.

## How the edge was found
`analysis/orb_study.py` ran a naive ORB (buy ATM weekly option on the first
breakout of the 09:15–09:29 range) across **36 expiries spanning 2020–2026**.

Naive version loses: **PF 0.95**, win 35%, avg −0.5%/trade (418 trades). That's
expected — indiscriminate breakouts get killed by theta and chop. Segmenting the
ledger revealed *which* trades carry the edge:

| Filter | n | Win | Avg/trade | Profit factor |
|---|---|---|---|---|
| All (naive) | 418 | 35% | −0.5% | 0.95 |
| Side = CALL | 199 | 37% | +0.4% | 1.04 |
| Side = PUT | 219 | 34% | −1.3% | 0.88 |
| DTE 0–2 | 69 | 25% | −5.6% | **0.55** |
| DTE > 8 | 225 | 41% | +1.7% | 1.18 |
| **CALL + DTE>3** | 159 | 40% | +2.5% | **1.26** |
| **CALL + DTE>3 + OR≥0.3%** | 45 | 47% | +8.7% | **1.94** |

## The three filters (each economically motivated)
1. **CALL only** — NIFTY's structural upward drift makes long puts bleed intraday.
2. **DTE ≥ 3 days** — the 0–2 DTE theta cliff (PF 0.55) is where money dies.
3. **Opening-range width ≥ 0.3%** — a wide morning range signals a volatility-
   expansion day; breakouts follow through (PF 1.94 vs ~1.0 on quiet days).

## Exit
Initial hard stop −30%; once in profit, trail 25% off the running peak (captures
the right-tail trend days that make long-option buying pay); unconditional EOD
square-off at 15:15.

## Honest caveats (to resolve in the engine)
- **Costs**: only 1% slippage applied. India F&O brokerage + STT + exchange + GST
  on a ~₹100–200 premium option is material and **could erase the edge**. Must be
  modelled before trusting it.
- **Liquidity**: the study assumed the ATM was always fillable. The engine's
  liquidity gate + rejection log will show how often it actually wasn't.
- **Expiry selection**: the study used a per-expiry-file DTE proxy. The engine
  must implement true "nearest expiry with DTE≥3" roll logic across files.
- **Sample/overfit**: the OR-width filter flipped sign between a 77-trade and a
  418-trade sample — a reminder to re-test on the *full* dataset, not a slice.

## Downside attempts — both rejected by data
Tested whether the downside can be traded with puts (`analysis/failed_call_reversal.py`):

| Idea | Trades | PF | Verdict |
|---|---|---|---|
| Fresh downside breakout (buy put) | 219 | 0.88 | loser |
| Failed call, full reversal below OR low (Mode A) | 86 | 0.57 | loser |
| Failed call, back inside below OR high (Mode B) | 170 | 0.55 | loser |

Root cause is the same NIFTY upward-drift asymmetry + V-shaped intraday recovery:
failed breakouts often fail *again* (whipsaw), and put entries land right where a
bounce is most likely. The breakout framework yields no downside edge. Puts would
need a **regime gate** (only when already confirmed bearish — gap-down / below a
falling MA / weak prior day), which is a different mechanism — not yet tested.

## Config
See `configs/orb_call_breakout.yaml`. Runnable once the strategy/execution
layers exist.
