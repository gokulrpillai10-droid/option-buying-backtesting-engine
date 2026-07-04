"""Test: do FAILED call-ORB breakouts make good PUT buys?

Thesis (failed-breakout reversal): price breaks above the opening-range high
(a CALL ORB signal), then fails and reverses back DOWN through the range. The
trapped breakout-longs puke, momentum flips down hard -> buy an ATM PUT.

Two failure definitions tested:
  A (full reversal): after the up-break, price later trades below OR low - buf.
  B (back inside):   after the up-break, price later trades below OR high - buf.

Same fill/exit/cost model as orb_study.py so results are comparable.
"""
import sys
from datetime import time

import pandas as pd

from engine.data.catalog import Catalog

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
OR_END = time(9, 29)
ENTRY_CUTOFF = time(14, 0)   # reversals can come later; allow until 14:00
SQUARE_OFF = time(15, 15)
BREAK_BUF = 0.0005
SLIPPAGE = 0.01
SL = 0.30
TRAIL = 0.25
LAST_N_DAYS = 12

PUT = ("PutOpen", "PutHigh", "PutLow", "PutClose", "PutIsTradedStrike")


def atm_strike(snap_df, forward, flag):
    traded = snap_df[snap_df[flag]]
    if traded.empty:
        return None
    return int(traded.iloc[(traded["StrikePrice"] - forward).abs().argmin()]["StrikePrice"])


def run_day(day_df, mode):
    fwd = day_df.groupby("DateTime")["Forward"].first().sort_index()
    or_mask = fwd.index.time <= OR_END
    if or_mask.sum() < 5:
        return None
    or_hi, or_lo = fwd[or_mask].max(), fwd[or_mask].min()
    or_width = (or_hi - or_lo) / or_lo
    post = fwd[fwd.index.time > OR_END]

    # 1) the call breakout must happen first
    up_dt = None
    for dt, px in post.items():
        if px > or_hi * (1 + BREAK_BUF):
            up_dt = dt
            break
    if up_dt is None:
        return None  # no call signal -> no failed call to convert

    # 2) after the up-break, look for the reversal that defines "failed"
    level = or_lo * (1 - BREAK_BUF) if mode == "A" else or_hi * (1 - BREAK_BUF)
    after_up = post[(post.index > up_dt) & (post.index.time <= ENTRY_CUTOFF)]
    rev_dt = None
    for dt, px in after_up.items():
        if px < level:
            rev_dt = dt
            break
    if rev_dt is None:
        return None  # breakout did not fail -> no put trade

    o, h, l, c, flag = PUT
    forward_at_sig = fwd.loc[rev_dt]
    dte_days = float(day_df["DaysToExpiry"].iloc[0]) * 365.0
    snap = day_df[day_df["DateTime"] == rev_dt]
    strike = atm_strike(snap, forward_at_sig, flag)
    if strike is None:
        return None
    opt = day_df[day_df["StrikePrice"] == strike].set_index("DateTime").sort_index()
    after = opt[opt.index > rev_dt]
    if after.empty:
        return None
    entry = after.iloc[0][o]
    if not entry or entry <= 0:
        return None
    entry *= (1 + SLIPPAGE)
    hold = after[after.index.time <= SQUARE_OFF]
    peak, exit_px, reason = entry, None, None
    for dt, row in hold.iterrows():
        peak = max(peak, row[h])
        trail_level = max(entry * (1 - SL), peak * (1 - TRAIL))
        if row[l] <= trail_level:
            exit_px = trail_level
            reason = "stop" if trail_level == entry * (1 - SL) else "trail"
            break
    if exit_px is None:
        exit_px, reason = (hold.iloc[-1][c] if len(hold) else after.iloc[-1][c]), "eod"
    exit_px *= (1 - SLIPPAGE)
    return dict(ret=exit_px / entry - 1, reason=reason, dte_days=round(dte_days, 1),
                or_width=round(or_width, 4), hour=rev_dt.hour,
                up_to_rev_min=int((rev_dt - up_dt).total_seconds() // 60))


def main(expiries, mode):
    cat = Catalog(ROOT)
    cols = ["DateTime", "StrikePrice", "Forward", "DaysToExpiry",
            "PutOpen", "PutHigh", "PutLow", "PutClose", "PutIsTradedStrike"]
    rows = []
    for exp in expiries:
        path = cat.path_for(exp)
        if path is None:
            continue
        df = pd.read_parquet(path, columns=cols)
        df["date"] = df["DateTime"].dt.date
        for d in sorted(df["date"].unique())[-LAST_N_DAYS:]:
            r = run_day(df[df["date"] == d], mode)
            if r:
                rows.append(r)
    t = pd.DataFrame(rows)
    if t.empty:
        print("no trades"); return
    pf_l = -t.loc[t.ret <= 0, "ret"].sum()
    pf = t.loc[t.ret > 0, "ret"].sum() / pf_l if pf_l else 99
    print(f"\n=== Failed-call -> PUT reversal (mode {mode}): {len(t)} trades ===")
    print(f"win rate {(t.ret>0).mean():.0%} | avg {t.ret.mean():+.1%} | median {t.ret.median():+.1%} | PF {pf:.2f}")
    print(f"exit reasons: {t['reason'].value_counts().to_dict()}")
    def seg(name, s):
        if len(s) < 8: return
        l = -s.loc[s.ret<=0,'ret'].sum(); pf = s.loc[s.ret>0,'ret'].sum()/l if l else 99
        print(f"  {name:22s} n={len(s):3d} win={(s.ret>0).mean():4.0%} avg={s.ret.mean():+6.1%} PF={pf:4.2f}")
    print("by DTE:");      seg("dte<=3", t[t.dte_days<=3]); seg("dte>3", t[t.dte_days>3])
    print("by OR width:"); seg("OR<0.3%", t[t.or_width<0.003]); seg("OR>=0.3%", t[t.or_width>=0.003])
    t.to_csv(f"analysis/failed_call_put_{mode}.csv", index=False)


if __name__ == "__main__":
    mode = "A"
    args = sys.argv[1:]
    if args and args[0] in ("A", "B"):
        mode, args = args[0], args[1:]
    main(args, mode)
