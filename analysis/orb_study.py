"""Exploratory edge study (NOT the final backtest).

Hypothesis: an intraday opening-range breakout in NIFTY, traded by buying the
ATM weekly option, captures a directional move big enough to beat theta+costs.

This is approximate hypothesis-generation. It uses the same data the real
engine will use, but a simplified fill/exit model. Anything promising here gets
re-validated rigorously through engine/execution later.

Setup per day:
  - Opening range  = Forward high/low over 09:15-09:29.
  - Signal         = first minute after 09:30 where Forward breaks OR by >buf.
  - Side           = CALL if break up, PUT if break down.
  - Strike         = ATM (nearest traded strike to Forward at signal).
  - Entry fill     = option Open at signal+1 minute, +slippage (pay up).
  - Exit           = first of: stop -SL%, target +TP%, EOD square-off 15:15.
                     stop/target checked intrabar on option High/Low.
  - Costs          = slippage both sides (below), brokerage ignored here.
"""
import sys
from datetime import time

import numpy as np
import pandas as pd

from engine.data.catalog import Catalog

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
OR_END = time(9, 29)
ENTRY_START = time(9, 30)
SQUARE_OFF = time(15, 15)
BREAK_BUF = 0.0005   # 0.05% beyond the range to confirm a breakout
SLIPPAGE = 0.01      # 1% adverse per side
SL = 0.30            # 30% initial stop
TRAIL = 0.25         # trail 25% off the peak once in profit
ENTRY_CUTOFF = time(13, 0)  # no new entries after this (need time to run)
LAST_N_DAYS = 12     # final ~2.5 weeks of each expiry (for DTE variety)


def atm_strike(snap_df, forward, side):
    flag = "CallIsTradedStrike" if side == "CALL" else "PutIsTradedStrike"
    traded = snap_df[snap_df[flag]]
    if traded.empty:
        return None
    return int(traded.iloc[(traded["StrikePrice"] - forward).abs().argmin()]["StrikePrice"])


def run_day(day_df, side_cols):
    fwd = day_df.groupby("DateTime")["Forward"].first().sort_index()
    or_mask = fwd.index.time <= OR_END
    if or_mask.sum() < 5:
        return None
    or_hi, or_lo = fwd[or_mask].max(), fwd[or_mask].min()
    or_width = (or_hi - or_lo) / or_lo
    post = fwd[(fwd.index.time > OR_END) & (fwd.index.time <= ENTRY_CUTOFF)]
    sig_dt, side, ref = None, None, None
    for dt, px in post.items():
        if px > or_hi * (1 + BREAK_BUF):
            sig_dt, side, ref = dt, "CALL", or_hi; break
        if px < or_lo * (1 - BREAK_BUF):
            sig_dt, side, ref = dt, "PUT", or_lo; break
    if sig_dt is None:
        return None
    o, h, l, c, traded = side_cols[side]
    forward_at_sig = fwd.loc[sig_dt]
    break_disp = abs(forward_at_sig - ref) / ref
    dte_days = float(day_df["DaysToExpiry"].iloc[0]) * 365.0
    snap = day_df[day_df["DateTime"] == sig_dt]
    strike = atm_strike(snap, forward_at_sig, side)
    if strike is None:
        return None
    opt = day_df[day_df["StrikePrice"] == strike].set_index("DateTime").sort_index()
    after = opt[opt.index > sig_dt]
    if after.empty:
        return None
    entry = after.iloc[0][o]
    if not entry or entry <= 0:
        return None
    entry *= (1 + SLIPPAGE)
    hold = after[after.index.time <= SQUARE_OFF]
    # trailing-stop exit: hard stop at -SL, then trail TRAIL off the running peak
    peak = entry
    exit_px, reason = None, None
    for dt, row in hold.iterrows():
        peak = max(peak, row[h])
        trail_level = max(entry * (1 - SL), peak * (1 - TRAIL))
        if row[l] <= trail_level:
            exit_px, reason = trail_level, ("stop" if trail_level == entry*(1-SL) else "trail"); break
    if exit_px is None:
        exit_px, reason = (hold.iloc[-1][c] if len(hold) else after.iloc[-1][c]), "eod"
    exit_px *= (1 - SLIPPAGE)
    ret = exit_px / entry - 1
    return dict(side=side, strike=strike, entry=entry, exit=exit_px, ret=ret,
                reason=reason, sig=sig_dt, hour=sig_dt.hour,
                dte_days=round(dte_days, 1), or_width=round(or_width, 4),
                break_disp=round(break_disp, 4))


def main(expiries):
    cat = Catalog(ROOT)
    cols = ["DateTime", "StrikePrice", "Forward", "DaysToExpiry",
            "CallOpen", "CallHigh", "CallLow", "CallClose", "CallIsTradedStrike",
            "PutOpen", "PutHigh", "PutLow", "PutClose", "PutIsTradedStrike"]
    side_cols = {
        "CALL": ("CallOpen", "CallHigh", "CallLow", "CallClose", "CallIsTradedStrike"),
        "PUT": ("PutOpen", "PutHigh", "PutLow", "PutClose", "PutIsTradedStrike"),
    }
    trades = []
    for exp in expiries:
        path = cat.path_for(exp)
        if path is None:
            continue
        df = pd.read_parquet(path, columns=cols)
        df["date"] = df["DateTime"].dt.date
        days = sorted(df["date"].unique())[-LAST_N_DAYS:]
        for d in days:
            r = run_day(df[df["date"] == d], side_cols)
            if r:
                r["date"] = d; r["expiry"] = exp
                trades.append(r)
    t = pd.DataFrame(trades)
    if t.empty:
        print("no trades"); return
    print(f"\n=== ORB ATM option-buying study: {len(t)} trades over {t['expiry'].nunique()} expiries ===")
    print(f"win rate        : {(t['ret'] > 0).mean():.1%}")
    print(f"avg return/trade: {t['ret'].mean():+.1%}")
    print(f"median return   : {t['ret'].median():+.1%}")
    print(f"avg win         : {t.loc[t['ret']>0,'ret'].mean():+.1%}")
    print(f"avg loss        : {t.loc[t['ret']<=0,'ret'].mean():+.1%}")
    gross = t.loc[t['ret']>0,'ret'].sum()
    loss = -t.loc[t['ret']<=0,'ret'].sum()
    print(f"profit factor   : {gross/loss:.2f}" if loss else "profit factor   : inf")
    print(f"total sum ret   : {t['ret'].sum():+.1%}  (per-1-lot equal-weight)")
    print(f"exit reasons    : {t['reason'].value_counts().to_dict()}")
    print(f"by side         : CALL {(t['side']=='CALL').sum()}  PUT {(t['side']=='PUT').sum()}")
    print(f"CALL avg ret {t.loc[t['side']=='CALL','ret'].mean():+.1%} | PUT avg ret {t.loc[t['side']=='PUT','ret'].mean():+.1%}")
    t.to_csv("analysis/orb_trades.csv", index=False)
    print("\nsaved trade ledger -> analysis/orb_trades.csv")


if __name__ == "__main__":
    main(sys.argv[1:])
