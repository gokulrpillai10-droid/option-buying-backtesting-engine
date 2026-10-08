"""
straddle_backtest.py — ATM straddle backtester gated on IV/RV classifier.

Entry logic
-----------
For each trading day, scan bars from 09:47 onward. The first bar where the
active filter fires is the entry bar. Enter ATM straddle: buy ATM CE + ATM PE
(nearest weekly expiry, same strike as the signal parquet's `strike` column).

Filters tested (run all, report separately):
  A  iv_rv_30 < 1.0            (options cheap vs 30-min intraday RV)
  B  iv < 0.20                 (absolute IV cheap, below 20% ann.)
  C  iv_rv_30 < 1.0 AND iv < 0.20
  D  always enter at 09:47     (null baseline — no filter)

Exit logic
----------
  Hard stop  : P&L < -STOP_PCT × entry_premium         → exit at that bar's close
  Trail stop : once peak_pnl > TRAIL_ACTIVATE × entry_premium,
               if current_pnl < peak_pnl × (1 - TRAIL_PCT)  → exit at bar's close
  Time stop  : 14:00 bar → exit at open of 14:00 bar

P&L tracking
------------
  Per-minute: uses CallClose + PutClose for the entered strike from raw chain.
  Entry: CallOpen + PutOpen of the signal bar (that minute's open).
  Exit: CallClose + PutClose when stop triggered; CallOpen + PutOpen of 14:00 bar.
  Costs applied on exit: CostModel (brokerage + STT + exchange + SEBI + stamp + GST).
  Lot size: date-dependent via instruments.lot_size_for().

Output
------
  Per-trade CSV  : analysis/straddle_trades_<filter>.csv
  Console report : profit factor, win rate, net P&L, avg trade, max drawdown, t-stat
"""

import warnings, sys, datetime as _dt
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/gokul/Desktop/buildstrategies")

import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from scipy import stats as spstats

from engine.data.catalog import Catalog
from engine.execution.costs import CostModel
from engine.instruments import lot_size_for

ROOT     = "/Users/gokul/Desktop/tradealgo/data nifty "
SIG_PATH = Path(__file__).parent / "iv_rv_intraday.parquet"
OUT_DIR  = Path(__file__).parent

cat   = Catalog(ROOT)
costs = CostModel()

# ── Exit parameters ───────────────────────────────────────────────────────────
STOP_PCT           = 0.40    # hard stop: exit when P&L < -40% of entry premium
TRAIL_ACTIVATE_PCT = 0.00    # trail activates as soon as any profit (0%)
TRAIL_PCT          = 0.50    # trail: exit when P&L drops to 50% of peak
TIME_STOP          = _dt.time(14, 0)
ENTRY_START        = _dt.time(9, 47)
ENTRY_END          = _dt.time(14, 0)   # no new entries at or after 14:00

CHAIN_COLS = ["DateTime", "StrikePrice", "CallOpen", "CallClose", "PutOpen", "PutClose"]

# ── Filter definitions ────────────────────────────────────────────────────────
FILTERS = {
    "A_ivrv30": lambda df: df["iv_rv_30"] < 1.0,
    "B_iv20":   lambda df: df["iv"] < 0.20,
    "C_both":   lambda df: (df["iv_rv_30"] < 1.0) & (df["iv"] < 0.20),
    "D_always": lambda df: pd.Series(True, index=df.index),
    "E_iv12_ivrv": lambda df: (df["iv"] < 0.12) & (df["iv_rv_30"] < 1.0),
}


def load_signal_df() -> pd.DataFrame:
    df = pd.read_parquet(SIG_PATH)
    df["_time"] = df["DateTime"].dt.time
    df["_date"] = df["DateTime"].dt.date
    entry_mask = (df["_time"] >= ENTRY_START) & (df["_time"] < ENTRY_END)
    return df[entry_mask].sort_values("DateTime").reset_index(drop=True)


def find_entries(signal_df: pd.DataFrame, filter_name: str) -> pd.DataFrame:
    """Return one row per trade day — the first bar where filter fires."""
    filt_fn = FILTERS[filter_name]
    flagged  = signal_df[filt_fn(signal_df)].copy()
    # Keep first signal per day
    entries = (flagged.groupby("_date", sort=True)
                      .first()
                      .reset_index()
                      .rename(columns={"_date": "date"}))
    return entries


def simulate_trades(entries: pd.DataFrame) -> pd.DataFrame:
    """
    For each entry, load raw chain, track P&L bar-by-bar, apply exits.
    Returns DataFrame with one row per trade.
    """
    # Group entries by expiry to load each chain parquet once
    exp_to_entries = defaultdict(list)
    for row in entries.itertuples(index=False):
        exp_to_entries[row.expiry].append(row)

    records = []
    n_exp = len(exp_to_entries)

    for i, (exp, entry_rows) in enumerate(sorted(exp_to_entries.items()), 1):
        if i % 50 == 0 or i == n_exp:
            print(f"  {i}/{n_exp} expiries …")

        chain_path = cat.path_for(exp)
        if chain_path is None:
            continue
        try:
            chain = pd.read_parquet(chain_path, columns=CHAIN_COLS)
        except Exception:
            continue

        chain["DateTime"] = pd.to_datetime(chain["DateTime"])
        chain["_date"]    = chain["DateTime"].dt.date
        chain["_time"]    = chain["DateTime"].dt.time

        for row in entry_rows:
            entry_date   = row.date
            entry_strike = int(row.strike)
            entry_call   = float(row.call_prem)
            entry_put    = float(row.put_prem)
            entry_prem   = entry_call + entry_put
            entry_dt     = row.DateTime

            lot  = lot_size_for(entry_date)
            stop_level  = -STOP_PCT * entry_prem

            # Pull all bars for this strike on this date, from entry_dt onward
            day_chain = chain[
                (chain["_date"] == entry_date) &
                (chain["StrikePrice"] == entry_strike)
            ].sort_values("DateTime")

            # Subsequent bars (for P&L tracking): entry_dt+1min onward
            post = day_chain[day_chain["DateTime"] > entry_dt].copy()

            if post.empty:
                # Only the entry bar — time-stop at entry (rare, e.g. end of data)
                records.append(_trade_record(
                    entry_date, entry_dt, entry_dt, entry_strike,
                    entry_prem, entry_call, entry_put,
                    exit_call=entry_call, exit_put=entry_put,
                    pnl_pts=0.0, reason="time_stop_no_data",
                    lot=lot
                ))
                continue

            peak_pnl    = 0.0
            trail_on    = False
            exit_bar    = None
            exit_reason = "time_stop"
            exit_call   = None
            exit_put    = None

            for bar in post.itertuples(index=False):
                bar_time = bar._time if hasattr(bar, '_time') else bar.DateTime.time()
                bar_time = bar.DateTime.time() if not hasattr(bar, '_time') else bar._time

                # Time stop: check if this bar IS the 14:00 bar → exit at OPEN
                if bar_time >= TIME_STOP:
                    exit_call   = bar.CallOpen
                    exit_put    = bar.PutOpen
                    exit_bar    = bar.DateTime
                    exit_reason = "time_stop"
                    break

                # Mark P&L at this bar's close
                pnl = (bar.CallClose + bar.PutClose) - entry_prem

                # Hard stop check
                if pnl < stop_level:
                    exit_call   = bar.CallClose
                    exit_put    = bar.PutClose
                    exit_bar    = bar.DateTime
                    exit_reason = "hard_stop"
                    break

                # Trail logic
                if pnl > peak_pnl:
                    peak_pnl = pnl

                if peak_pnl > TRAIL_ACTIVATE_PCT * entry_prem:
                    trail_on = True

                if trail_on and pnl < peak_pnl * (1 - TRAIL_PCT):
                    exit_call   = bar.CallClose
                    exit_put    = bar.PutClose
                    exit_bar    = bar.DateTime
                    exit_reason = "trail"
                    break

            else:
                # Loop exhausted without hitting any stop — use last bar's close
                last = post.iloc[-1]
                exit_call   = last.CallClose
                exit_put    = last.PutClose
                exit_bar    = last.DateTime
                exit_reason = "time_stop" if last.DateTime.time() >= TIME_STOP else "eod"

            if exit_call is None:
                # Fallback
                last = post.iloc[-1]
                exit_call, exit_put = last.CallClose, last.PutClose
                exit_bar    = last.DateTime
                exit_reason = "eod"

            pnl_pts = (float(exit_call) + float(exit_put)) - entry_prem
            records.append(_trade_record(
                entry_date, entry_dt, exit_bar, entry_strike,
                entry_prem, entry_call, entry_put,
                exit_call=float(exit_call), exit_put=float(exit_put),
                pnl_pts=pnl_pts, reason=exit_reason,
                lot=lot
            ))

    return pd.DataFrame(records)


def _trade_record(date, entry_dt, exit_dt, strike, entry_prem,
                  entry_call, entry_put, exit_call, exit_put,
                  pnl_pts, reason, lot):
    buy_val  = entry_prem * lot
    sell_val = (exit_call + exit_put) * lot
    cost     = costs.total(buy_val, sell_val, n_orders=4)  # 2 legs × buy+sell
    net_pnl  = pnl_pts * lot - cost
    return {
        "date":        date,
        "entry_time":  entry_dt.time() if hasattr(entry_dt, 'time') else entry_dt,
        "exit_time":   exit_dt.time()  if hasattr(exit_dt,  'time') else exit_dt,
        "strike":      strike,
        "entry_call":  round(entry_call, 2),
        "entry_put":   round(entry_put,  2),
        "entry_prem":  round(entry_prem, 2),
        "exit_call":   round(exit_call,  2),
        "exit_put":    round(exit_put,   2),
        "pnl_pts":     round(pnl_pts,    2),
        "lot":         lot,
        "gross_pnl":   round(pnl_pts * lot, 2),
        "cost":        round(cost,       2),
        "net_pnl":     round(net_pnl,    2),
        "reason":      reason,
    }


def print_report(filter_name: str, trades: pd.DataFrame) -> None:
    if trades.empty:
        print(f"  {filter_name}: no trades")
        return

    n     = len(trades)
    wins  = trades[trades["net_pnl"] > 0]
    loses = trades[trades["net_pnl"] <= 0]
    win_r = len(wins) / n

    gross_profit = wins["net_pnl"].sum()
    gross_loss   = min(loses["net_pnl"].sum(), -1e-9)
    pf           = abs(gross_profit / gross_loss)

    net_total    = trades["net_pnl"].sum()
    avg_trade    = trades["net_pnl"].mean()
    avg_win      = wins["net_pnl"].mean()  if len(wins) else 0
    avg_loss     = loses["net_pnl"].mean() if len(loses) else 0

    # Max drawdown on equity curve
    equity = trades["net_pnl"].cumsum()
    roll_max = equity.cummax()
    dd = (equity - roll_max)
    max_dd = dd.min()

    # T-stat on net_pnl
    t, p = spstats.ttest_1samp(trades["net_pnl"], 0)

    # Exit distribution
    exit_counts = trades["reason"].value_counts().to_dict()

    yr_table = (trades.groupby(pd.to_datetime(trades["date"]).dt.year)
                      .agg(n=("net_pnl","count"), net=("net_pnl","sum"), wr=("net_pnl", lambda x: (x>0).mean()))
                      .reset_index())

    label = {
        "A_ivrv30":    "IV/RV_30 < 1.0",
        "B_iv20":      "IV < 20%",
        "C_both":      "IV/RV < 1.0 AND IV < 20%",
        "D_always":    "always enter (null baseline)",
        "E_iv12_ivrv": "IV < 12% AND IV/RV_30 < 1.0",
    }.get(filter_name, filter_name)

    print(f"\n{'='*70}")
    print(f"Filter: {label}")
    print(f"{'='*70}")
    print(f"  Trades: {n}   Win rate: {win_r*100:.1f}%   PF: {pf:.2f}")
    print(f"  Net P&L: {net_total:>12,.0f}   Avg/trade: {avg_trade:>8,.0f}")
    print(f"  Avg win: {avg_win:>8,.0f}   Avg loss: {avg_loss:>8,.0f}")
    print(f"  Max drawdown: {max_dd:>10,.0f}   t-stat: {t:>6.2f}  (p={p:.3f})")
    print(f"  Exit reasons: {exit_counts}")
    print(f"\n  Year  Trades    Net P&L   Win%")
    print(f"  {'─'*38}")
    for _, r in yr_table.iterrows():
        print(f"  {int(r['date']):>4}   {int(r['n']):>5}  {r['net']:>10,.0f}   {r['wr']*100:>4.1f}%")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Loading IV/RV signal parquet …")
    signal_df = load_signal_df()
    print(f"  {signal_df['_date'].nunique()} days, {len(signal_df):,} bars")

    run_filters = sys.argv[1:] if len(sys.argv) > 1 else list(FILTERS.keys())
    for fname in run_filters:
        print(f"\n[{fname}] Finding entries …")
        entries = find_entries(signal_df, fname)
        print(f"  {len(entries)} signal days")
        if entries.empty:
            print("  (skip)")
            continue

        print(f"  Simulating trades …")
        trades = simulate_trades(entries)

        csv_path = OUT_DIR / f"straddle_trades_{fname}.csv"
        trades.to_csv(csv_path, index=False)
        print(f"  → saved {csv_path.name}")

        print_report(fname, trades)
