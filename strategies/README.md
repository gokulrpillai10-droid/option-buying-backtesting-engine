# Strategy Results Ledger

All completed studies. User is the decision gate — this file records verdicts, not recommendations.

| Strategy | File | Instrument | Period | Verdict | Key numbers |
|---|---|---|---|---|---|
| CPR Breakout | `strategies/cpr_breakout.py` | NIFTY options | 2023-02-25 to 2026-02-25 | **GO (live candidate)** | +143% on 5L, Sharpe 1.74, max DD −19%, realistic costs included |
| CPR Reversal Trap — Long CE | `analysis/cpr_reclaim_long.py` | NIFTY ATM CE | 2023-26 | **NO-GO** | PF 0.97; 2023 absorbs all loss; ATM is wrong strike |
| CPR Reversal Trap — Short PE (deep-ITM) | `analysis/cpr_reclaim_short.py` | NIFTY deep-ITM PE | 2023-26 | **WATCH** | PF 1.03–1.06, +90k; fat delta is the edge, not ATM |
| Fib Retracement — CALL+PUT | `analysis/fib_retracement_run.py` | NIFTY options | 2020-26 | **NO-GO** | PF 1.00 (coin flip); custom levels 0.22/0.44/0.77/1.0/1.2 |
| Fib Retracement — CE only | `analysis/fib_retracement_trades2026.py` | NIFTY CE | 2020-26 | **NO-GO** | Net −211k, PF 0.83 |
| ATM Straddle — IV/RV < 1.0 | `analysis/straddle_backtest.py` (filter A) | NIFTY straddle | 2020-26 | **NO-GO** | PF 0.53; 92% trail exits (exit design broken) |
| ATM Straddle — IV < 20% | `analysis/straddle_backtest.py` (filter B) | NIFTY straddle | 2020-26 | **NO-GO** | PF 0.51 |
| ATM Straddle — IV < 12% + IV/RV < 1.0 | `analysis/straddle_backtest.py` (filter E) | NIFTY straddle | 2020-26 | **NO-GO (unclean)** | PF 0.41; trail stop fires on noise — needs re-run with TRAIL_ACTIVATE 20% |
| ATM Straddle — always enter | `analysis/straddle_backtest.py` (filter D) | NIFTY straddle | 2020-26 | **NO-GO** | PF 0.32 (worst filter, baseline) |
| Reversal Blast (RSI/Stoch/%R + EMA) | `analysis/reversal_blast_run.py` | ADANIENT, NIFTY | 2020-26 | **NO-GO** | Falling-knife on ADANIENT; anti-predictive on NIFTY |
| EMA 5 (5 exit schemes) | `analysis/ema5_itm_run.py` | ADANIENT | 2022-26 | **NO-GO** | All 5 exit schemes negative |
| EMA Trend-Line Break | `analysis/ema_trendline_break.py` | ADANIENT | 2022-26 | **NO-GO (diagnostic)** | 200-EMA filter works; MIS 15:15 guillotines runners; needs swing engine |
| Gap Drive Short | `strategies/gap_drive_short_equity.py` | ADANIENT | 2020-26 | **PASS stress gate** | PF 1.94 worst-case; last gate = live fill audit (entry side done) |
| Gap Router | `strategies/gap_router_equity.py` | ADANIENT | 2020-26 | **NO-GO** | OOS PF 0.43 |
| ORB Short Equity | `strategies/orb_short_equity.py` | ADANIENT | 2020-26 | **NO-GO** | No edge over baseline |
| Gap Fill Fade (NIFTY options) | `strategies/gap_fill_fade.py` | NIFTY options | 2020-26 | **NO-GO** | All arms net-negative after theta/spread; fade beats null but loses |
| OI Features (PCR/buildup/max-pain) | `analysis/oi_pnl_triage.py` | NIFTY | 2020-26 | **NO-GO** | All |t|<3, corr~0, no edge over always-short |
| Vol-Surface Regime | `analysis/vol_surface_regime.py` | NIFTY | 2020-26 | **NO-GO as improvement** | Real (fwd-vol t=+11.2) but redundant with VIX |
| Buy/Sell Regime Detector (theta/gamma) | `analysis/regime_buysell_run.py` | NIFTY | 2020-26 | **NO-GO** | Short-vol dominates; regime doesn't improve buyer P&L |

## Data

- NIFTY 1-min options chain: `/Users/gokul/Desktop/tradealgo/data nifty /` (parquet)
- NIFTY 1-min spot (Kite): `data_spot_kite/NIFTY50/`
- ADANIENT 1-min spot (Kite): `data_equity/ADANIENT/`
- IV/RV intraday classifier: `analysis/iv_rv_intraday.parquet` (not committed, 26MB)
- Data ends: **2026-02-25** (refresh before live)

## Engine

See `docs/engine.md` for the full engine reference.
