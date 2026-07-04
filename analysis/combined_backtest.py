"""Combined backtest of BOTH candidate strategies on REAL option prices,
with slippage AND full India F&O costs. Pre-engine sanity backtest.

  A) orb_call_breakout         -> buy ATM CALL, option-premium trail exit
  B) gap_down_continuation_put -> buy ATM PUT, underlying 25/50/trail scale-out

Honest-but-approximate: minute granularity, fills at option O/H/L/C, 1% slip/side,
realistic cost model below. NOT the golden-tested engine -- but real options, real
costs, no look-ahead (signals use data <= t, fills at t+1).
"""
import sys
from datetime import time, timedelta

import numpy as np
import pandas as pd

from engine.data.catalog import Catalog

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
LOT_SIZE = 65   # NIFTY revised lot size
LOTS = 1
SLIP = 0.01
MIN_DTE = 3

# session / signal params (from the two YAML configs)
OR_END = time(9, 29)
SQUARE_OFF = time(15, 15)
A_ENTRY_END = time(13, 0)
A_OR_WIDTH = 0.003
A_BREAK = 0.0005
A_STOP = 0.30
A_TRAIL = 0.25
B_ENTRY_END = time(11, 30)
B_GAP = 20.0
B_BREAK_PTS = 2.0
B_STOP_PTS = 35.0
B_BOOK1 = 25.0
B_BOOK2 = 50.0
B_TRAIL_PTS = 20.0

# India F&O option costs
BROK = 20.0
STT_SELL = 0.001          # 0.1% of sell premium turnover
EXCH = 0.0003503          # per side of premium turnover
SEBI = 0.000001           # per side
STAMP_BUY = 0.00003       # buy side
GST = 0.18


def costs(buy_val, sell_val, n_orders):
    brok = BROK * n_orders
    stt = STT_SELL * sell_val
    exch = EXCH * (buy_val + sell_val)
    sebi = SEBI * (buy_val + sell_val)
    stamp = STAMP_BUY * buy_val
    gst = GST * (brok + exch + sebi)
    return brok + stt + exch + sebi + stamp + gst


def atm(snap, fwd, flag):
    tr = snap[snap[flag]]
    if tr.empty:
        return None
    return int(tr.iloc[(tr["StrikePrice"] - fwd).abs().argmin()]["StrikePrice"])


def opt_series(day, strike, pfx):
    o = day[day["StrikePrice"] == strike].set_index("DateTime").sort_index()
    return o[[pfx + "Open", pfx + "High", pfx + "Low", pfx + "Close"]]


def run_call(day, fwd):
    om = fwd.index.time <= OR_END
    if om.sum() < 5:
        return None
    hi, lo = fwd[om].max(), fwd[om].min()
    if (hi - lo) / lo < A_OR_WIDTH:
        return None
    post = fwd[(fwd.index.time > OR_END) & (fwd.index.time <= A_ENTRY_END)]
    sig = next((dt for dt, px in post.items() if px > hi * (1 + A_BREAK)), None)
    if sig is None:
        return None
    snap = day[day["DateTime"] == sig]
    k = atm(snap, fwd.loc[sig], "CallIsTradedStrike")
    if k is None:
        return None
    s = opt_series(day, k, "Call")
    aft = s[s.index > sig]
    if aft.empty or aft.iloc[0]["CallOpen"] <= 0:
        return None
    fill = aft.iloc[0]["CallOpen"] * (1 + SLIP)
    stop = fill * (1 - A_STOP)
    peak, exit_px = fill, None
    for _, r in aft[aft.index.time <= SQUARE_OFF].iterrows():
        peak = max(peak, r["CallHigh"])
        lvl = max(stop, peak * (1 - A_TRAIL))
        if r["CallLow"] <= lvl:
            exit_px = lvl
            break
    if exit_px is None:
        exit_px = aft[aft.index.time <= SQUARE_OFF].iloc[-1]["CallClose"]
    sell = exit_px * (1 - SLIP)
    qty = LOT_SIZE * LOTS
    buy_val, sell_val = fill * qty, sell * qty
    c = costs(buy_val, sell_val, 2)
    return dict(side="CALL", strike=k, gross=(sell - fill) * qty,
                cost=c, net=(sell - fill) * qty - c, premium=buy_val)


def run_put(day, fwd, prev_close):
    if pd.isna(prev_close):
        return None
    om = fwd.index.time <= OR_END
    if om.sum() < 5:
        return None
    if fwd.iloc[0] - prev_close > -B_GAP:
        return None
    lo = fwd[om].min()
    post = fwd[(fwd.index.time > OR_END) & (fwd.index.time <= B_ENTRY_END)]
    sig = next((dt for dt, px in post.items() if px < lo - B_BREAK_PTS), None)
    if sig is None:
        return None
    snap = day[day["DateTime"] == sig]
    k = atm(snap, fwd.loc[sig], "PutIsTradedStrike")
    if k is None:
        return None
    s = opt_series(day, k, "Put")
    aft = s[s.index > sig]
    if aft.empty or aft.iloc[0]["PutOpen"] <= 0:
        return None
    entry_fwd = fwd.loc[sig]
    fill = aft.iloc[0]["PutOpen"] * (1 + SLIP)
    qty = LOT_SIZE * LOTS
    fwd_aft = fwd[fwd.index > sig]
    remaining, booked, armed, fav_ext = 1.0, False, False, 0.0
    gross, sell_val, n_sell = 0.0, 0.0, 0
    hold = aft[aft.index.time <= SQUARE_OFF]
    closed = False
    for dt, r in hold.iterrows():
        if dt not in fwd_aft.index:
            continue
        fav = entry_fwd - fwd_aft.loc[dt]          # +ve = put winning
        price = r["PutClose"]
        if (fwd_aft.loc[dt] - entry_fwd) >= B_STOP_PTS:   # underlying adverse stop
            sell = price * (1 - SLIP)
            gross += remaining * (sell - fill) * qty
            sell_val += remaining * sell * qty; n_sell += 1
            remaining = 0.0; closed = True
            break
        if not booked and fav >= B_BOOK1:
            sell = price * (1 - SLIP)
            gross += 0.5 * (sell - fill) * qty
            sell_val += 0.5 * sell * qty; n_sell += 1
            remaining = 0.5; booked = True
        if booked and not armed and fav >= B_BOOK2:
            armed, fav_ext = True, fav
        if armed:
            fav_ext = max(fav_ext, fav)
            if fav <= fav_ext - B_TRAIL_PTS:
                sell = price * (1 - SLIP)
                gross += remaining * (sell - fill) * qty
                sell_val += remaining * sell * qty; n_sell += 1
                remaining = 0.0; closed = True
                break
    if not closed and remaining > 0:
        sell = hold.iloc[-1]["PutClose"] * (1 - SLIP)
        gross += remaining * (sell - fill) * qty
        sell_val += remaining * sell * qty; n_sell += 1
    buy_val = fill * qty
    c = costs(buy_val, sell_val, 1 + n_sell)
    return dict(side="PUT", strike=k, gross=gross, cost=c, net=gross - c, premium=buy_val)


def choose_expiry(d, expiries):
    for e in expiries:
        if (e - d).days >= MIN_DTE:
            return e
    return None


def main():
    cat = Catalog(ROOT)
    exp_dates = [pd.Timestamp(e).date() for e in cat.expiries()]
    daily = pd.read_csv("analysis/daily_underlying.csv", parse_dates=["date"])
    daily["d"] = daily["date"].dt.date
    pclose = dict(zip(daily["d"], daily["prev_close"]))
    # map each trading date -> nearest expiry with DTE>=3
    date_exp = {}
    for d in daily["d"]:
        e = choose_expiry(d, exp_dates)
        if e is not None:
            date_exp[d] = e
    by_exp = {}
    for d, e in date_exp.items():
        by_exp.setdefault(e, []).append(d)

    cols = ["DateTime", "StrikePrice", "Forward",
            "CallOpen", "CallHigh", "CallLow", "CallClose", "CallIsTradedStrike",
            "PutOpen", "PutHigh", "PutLow", "PutClose", "PutIsTradedStrike"]
    rows = []
    for e, dates in by_exp.items():
        p = cat.path_for(e.isoformat())
        if p is None:
            continue
        df = pd.read_parquet(p, columns=cols)
        df["date"] = df["DateTime"].dt.date
        for d in dates:
            day = df[df["date"] == d]
            if len(day) < 20:
                continue
            fwd = day.groupby("DateTime")["Forward"].first().sort_index()
            for fn, r in (("A", run_call(day, fwd)),
                          ("B", run_put(day, fwd, pclose.get(d)))):
                if r:
                    r["date"], r["strat"] = d, fn
                    rows.append(r)
    t = pd.DataFrame(rows)
    t.to_csv("analysis/combined_trades.csv", index=False)
    report(t)


def report(t):
    t = t.sort_values("date").reset_index(drop=True)
    t["year"] = pd.to_datetime(t["date"]).dt.year
    eq = t["net"].cumsum()
    dd = (eq.cummax() - eq).max()

    def block(name, s):
        if len(s) == 0:
            print(f"{name:24s} no trades"); return
        win = (s["net"] > 0).mean()
        pl = -s.loc[s["net"] <= 0, "net"].sum()
        pf = s.loc[s["net"] > 0, "net"].sum() / pl if pl else float("inf")
        print(f"{name:24s} n={len(s):4d}  win={win:4.0%}  net=Rs{s['net'].sum():>10,.0f}  "
              f"avg=Rs{s['net'].mean():>7,.0f}  PF={pf:4.2f}")

    print("\n" + "=" * 78)
    print(f"  COMBINED BACKTEST  (real option fills, 1% slip/side, full F&O costs)")
    print(f"  {LOTS} lot x {LOT_SIZE} qty | NIFTY 2020-2026 | nearest expiry DTE>={MIN_DTE}")
    print("=" * 78)
    block("ALL TRADES", t)
    block("  A: ORB call", t[t.strat == "A"])
    block("  B: gap-down put", t[t.strat == "B"])
    print("-" * 78)
    gross, cost, net = t["gross"].sum(), t["cost"].sum(), t["net"].sum()
    print(f"Gross P&L  : Rs {gross:>12,.0f}")
    print(f"Costs      : Rs {cost:>12,.0f}   ({cost/abs(gross)*100 if gross else 0:.1f}% of gross)")
    print(f"NET P&L    : Rs {net:>12,.0f}")
    print(f"Avg cost/trade: Rs {t['cost'].mean():,.0f}   |   max drawdown: Rs {dd:,.0f}")
    print(f"Return on capital deployed (sum premium): {net / t['premium'].sum() * 100:.1f}%")
    print("-" * 78)
    print("By year (NET):")
    for y, s in t.groupby("year"):
        block(f"  {y}", s)
    print("=" * 78)


if __name__ == "__main__":
    main()
