# buildstrategies — Project Documentation

**Last updated:** 2026-08-13
**Repo:** `/Users/gokul/Desktop/buildstrategies` (git; main branch `main`, active `gap-fill-fade`)

Backtesting engine + strategies for **Indian markets** — NIFTY index options
(intraday buying) and NSE equities. The governing principle is **accuracy first:
a backtest must be trustworthy, not merely plausible.** Convention: research
scripts live in `analysis/` and commit alongside their `_out.txt` output.

---

## 1. Repository layout

```
engine/          the backtesting engine (options + equity subsystems)
strategies/      Strategy subclasses (the tradeable + falsified ideas)
analysis/        research-grade scripts (91 .py) + committed _out.txt
tests/           53 test files (unit / integration / golden reconciliation)
configs/         YAML strategy configs
data_equity/     per-symbol 1-min equity parquet (ADANIENT, HDFCBANK, TATASTEEL)
data_index/      NIFTY index gap data
docs/            designs (HLD + 6 LLDs), specs, this file
CLAUDE.md        skill-routing + conventions
ENGINE.md        the engine's design + accuracy manifesto
AGENTS.md        architecture notes
```

Options-chain + index spot data lives OUTSIDE the repo under
`/Users/gokul/Desktop/tradealgo/data nifty ` (Data-v3, globbed by the loader).

---

## 2. The engine (`engine/`)

**Defining property:** it reproduces the reference strategy **G12-CE 582/582
trades exact** over 2020–2026 on real data — an identity, not a tolerance match.
Everything downstream inherits that trust.

**Central accuracy decision — spot decides *when*, the chain decides *at what price*:**
- Signals fire off the real **INDEX spot** (`SpotSeries`), never the chain-implied Forward (Forward's intraday noise flipped G12-CE's sign).
- Fills come from the **Data-v3 option chain** at the real per-minute price.

### Layers

| Layer | Module | Responsibility |
|---|---|---|
| **Data** | `data/spot.py` (`SpotSeries`) | look-ahead-safe spot OHLC; `prev_day_hlc`, `close_before`, `window`; START-labelled resampled bars |
| | `data/chain.py` (`Chain`) | per-minute option snapshots (price/IV/Greeks/vol/OI/is_traded) |
| | `data/market.py` (`MarketData`) | facade; picks real expiry via `expiry_for(day)` (not a weekday rule — expiry moved Thu→Tue ~2025-09) |
| | `data/catalog.py`/`loader.py` | expiry discovery + parquet resolution |
| | `instruments.py` | **date-keyed lot size** (75→65 at 2025-12-30) — engine owns it, not the strategy |
| **Execution** | `execution/model.py` | the ONLY place an entry fill is produced: tradeability → liquidity gate → fill at t+1 open → adverse slippage; returns `Fill` or `Rejection` (never silently dropped) |
| | `execution/costs.py` (`CostModel`) | India F&O cost card (brokerage/STT/exchange/SEBI/stamp/GST); slippage applied to fill, not here |
| **Position** | `position/manager.py` | `Position` (long premium, partial closes) + `SpreadPosition`/`SpreadLeg` (multi-leg credit/debit — premium *selling* is testable) |
| | `position/exits.py` | intrabar stop/target/trail resolution on 1-min option high/low (gap-through honesty) |
| **Strategy** | `strategy/base.py` | `Strategy` + `BarContext` (truncates to `<= now` → structural no-look-ahead) |
| | `strategy/exit.py` | `Exit.market / .at / .bracket / .trail`, per-side fractions (scale-outs) |
| **Driver** | `backtest.py` | `Backtest(market, cost_model, knobs).run(strategy, start, end)`; intraday-only, EOD square-off |
| | `indicators.py` | RSI / Stochastic / Williams %R / EMA (look-ahead-safe primitives) |

### `Knobs` — the sensitivity reframe

A single backtest number is not trustworthy (a G12 config swung +₹135k vs +₹49k
on bar-construction alone). So the engine is parameterizable over
`slippage_pct`, `timeframe`, `fill_price`, `min_volume_mult` — and the real
deliverable is a **sensitivity sweep**, not one number. `Result` exposes `net`,
`win_rate`, `profit_factor`, `tail_excl(n)`, `by_year`, `t_stat`.

### Validation posture (what makes it different)
- **Trade-by-trade reconciliation** (G12 582/582), not aggregate tolerance.
- **Walk-forward, not in-sample tuning** (CPR's IV≤0.12 chosen on prior years, survived OOS).
- **Fill audits + a ≥10× liquidity floor** wired into the driver.
- **Falsification** — candidate filters tested to *disprove* them, retired when they don't beat the simple rule.

### Equity subsystem (`engine/equity/`)
Parallel path for NSE equities: `kite_loader.py` (direct kiteconnect),
`gap_features.py`, `nifty_gap.py`, its own `backtest.py`/`context.py`/`costs.py`/
`model.py`/`position.py`. Powers the gap/ORB/S-R equity studies.

---

## 3. Strategies built (`strategies/`) + verdicts

| Strategy | Verdict |
|---|---|
| **`cpr_breakout`** | ✅ **The validated winner.** Deep-ITM CPR breakout (+50 target / 12-pt trail / far-edge stop / IV≤0.12). 6 lots: **+₹868k/6.1y, PF 1.70, Sharpe 1.42, every year green**, walk-forward + fill-audited. Live-pilot candidate. |
| `cpr_breakout_equity` | equity variant of the CPR logic |
| `g12_ce` | calibration reference — the 582/582 reconciliation, not for deployment |
| `ema_crossover` / `ema5_itm` | ❌ no directional edge buying (t −3 to −26); crossover is a coin-flip entry |
| `gap_down_put` | ❌ real *index* downside edge (PF 1.36) dies as long puts (delta/vega crush) |
| `gap_fill_fade` | ❌ NIFTY gap-fill options NO-GO after theta/spread (see study) |
| `gap_drive_short_equity` | ADANIENT gap-drive short — passed the stress gate; live fill audit pending |
| `orb_short_equity` / `orb_spread` | ❌ falsified; the "CPR+ORB regime portfolio" is dead |
| `gap_router_equity` | ❌ NO-GO (OOS PF 0.43) |
| `atr_roc_buy` | high-vol OTM momentum; ~breakeven 5-min, loses 15-min |

**Recurring lesson:** for NIFTY intraday option *buying*, absolute premium cost
(low IV) is the binding edge filter; most signals have no edge once friction is
modeled honestly. CPR in the low-IV regime is the one real thread.

---

## 4. Research studies (`analysis/`, 91 scripts)

Research-grade, committed with `_out.txt`. Highlights (full detail in the memory files):

| Study | Outcome |
|---|---|
| **CPR deployed** | The live candidate: 5L acct 2023–26 **+143%, Sharpe 1.74, DD −19%**; costs included |
| CPR reversal-trap / reclaim | PUT-side structurally weak; deep-ITM PE short first positive cell (PF ~1.03) |
| Gap-Fill Fade | NO-GO all arms after theta/spread; durable NIFTY gap facts (2:1 up-bias, reclaim-reversal edge) |
| ADANIENT | 5-EMA closed; gap-drive short passed stress; ORB-short NO-GO; live audit started |
| Regime finder | `regime_finder.py` two-axis labeler (MA200 dir + relative vol); recovers COVID/2023-24 |
| Intraday equity ₹5k | NO-GO: cost×frequency wall; levels have memory not prediction |
| Reversal Blast | NO-GO on ADANIENT + NIFTY; entry has no directional edge |
| EMA trendline break | NO-GO intraday but mis-caged (200-EMA filter works; 1:5 runner guillotined by 15:15) |
| Gap Router | NO-GO (OOS PF 0.43) |
| Equity S/R | swing-pivot S/R tools sharing `analysis/sr_core.py` |

---

## 5. Data assets

| Data | Location | Range | Notes |
|---|---|---|---|
| NIFTY index spot 1-min | tradealgo `Data-v3 .../INDEX` | 2019-12 → 2026-02 | signals + calendar |
| Option chain per-expiry | tradealgo `Data-v3` | 2020-01 → **2026-02-25** | fills + IV/Greeks; **refresh before live** |
| India VIX daily | `analysis/india_vix.csv` | 2019-06 → 2026-07 | regime detector |
| NIFTY daily | `analysis/nifty_daily.csv` | 2019-01 → 2026-07 | regime detector |
| Equity 1-min | `data_equity/{ADANIENT,HDFCBANK,TATASTEEL}` | multi-year | equity studies |

Live data path: direct `kiteconnect` via `engine/equity/kite_loader.make_kite`
(the Kite MCP is broken upstream — do not re-debug).

---

## 6. Active projects (designed, not yet built)

### A. Regime buy/sell detector — `docs/regime-trading-system.md`, spec `specs/2026-08-13-...`
Daily detector: **theta/SELL in calm markets, gamma/BUY in stormy markets** (vol
STATE is the primary axis, reusing `regime_finder.py`). Both regimes verified
well-powered (SELL 32 episodes, BUY 33). Validated by a two-sided straddle
thermometer before any live selling. Eng-reviewed.

### B. Live execution engine — spec `specs/2026-08-12-...`
Paper-first strategy-agnostic live runner reusing the backtest `Strategy` core.
**Daily loss limit** = hard MTM flatten + halt (sticky across restarts). Sized
for a ₹50–70k account. `mode: paper|live` swaps only the order gateway.

The detector feeds the execution engine: regime → strategy family → sized orders
→ loss-limit kill-switch → fill/outcome journal.

---

## 7. Testing

```
cd /Users/gokul/Desktop/buildstrategies && PYTHONPATH="$PWD" python3 -m pytest -q
```
53 test files across data / execution / position / strategy / driver, including
the golden 582/582 reconciliation and liquidity-floor goldens.

**Add a strategy:** subclass `Strategy`, then
`Backtest(MarketData(Catalog(ROOT), SpotSeries.from_index(ROOT), min_dte=N), CostModel(), Knobs(...)).run(strat, start, end)`.

---

## 8. Known limitations (acknowledged)

- **1-min fidelity cap** on intrabar exits (gap-through logic captures overshoot).
- **NIFTY index has no volume** in the store (per-strike option volume is used for the liquidity floor).
- **Options data ends 2026-02-25** — every recent options number is capped there; refresh Data-v3 before live decisions.
- **Lot-size schedule** currently encodes only the 75→65 (2025-12-30) boundary.

---

## 9. Docs & records

- **Designs:** `docs/designs/` — HLD + 6 LLDs (data layer, strategy API, driver, exits, position/costs/metrics, equity subsystem).
- **Specs:** `docs/superpowers/specs/` — per-project design docs (engine, equity, gap-router, gap-fill-fade, regime finder, execution engine, regime detector).
- **Strategy guide:** `docs/strategies/cpr-breakout.md`.
- **Engine manifesto:** `ENGINE.md`. **Conventions/routing:** `CLAUDE.md`.
- **Decision history + rejected variants:** auto-memory at
  `~/.claude/projects/-Users-gokul-Desktop-buildstrategies/memory/`.
```
