# option-buying-backtesting-engine

Intraday NIFTY options and NSE equity backtesting engine. Built to test and validate trading strategies on real historical data  not a paper-trading simulator, not a framework with abstractions for every scenario. One purpose: find edge.

## What's in here

```
engine/          core backtest engine (data, execution, signals, strategy, metrics)
strategies/      all strategy implementations; README.md = results ledger
analysis/        research scripts — each script commits alongside its _out.txt
tests/           unit + integration + golden tests (437 passing)
docs/            architecture docs, ADRs, design specs
configs/         YAML config files for strategy parameters
scripts/         broker auth helpers (Fyers, Kite)
```

## Install

```bash
python3 -m pip install -e .
```

This installs the `engine` and `strategies` packages in editable mode so all analysis scripts can import them without `sys.path` hacks.

## Run a backtest

```bash
# CPR breakout (the validated live candidate)
python3 analysis/cpr_tearsheet.py

# ATM straddle with IV/RV filter
python3 analysis/straddle_backtest.py E_iv12_ivrv
```

## Run tests

```bash
python3 -m pytest tests/ -q
# → 437 passed, 1 skipped
```

## Results summary

See `strategies/README.md` for the full ledger. The short version:

- **CPR Breakout** — GO (live candidate): 5L capital, +143% 2023-26, Sharpe 1.74, DD −19%
- **Gap Drive Short (ADANIENT)** — passes stress gate, pending live fill audit
- Everything else — NO-GO (documented with exact numbers)

## Data

NIFTY 1-min options chain ends **2026-02-25**. Refresh the `data nifty/` folder before going live.

Options chain path: `/Users/gokul/Desktop/tradealgo/data nifty /`
Spot path: `data_spot_kite/NIFTY50/`

Data files are excluded from git (`.gitignore`).

## Branches

- `main` — stable
- `gap-fill-fade` — current research branch
