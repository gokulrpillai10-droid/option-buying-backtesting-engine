"""9/21 EMA crossover, intraday NIFTY option buying.

Signal: on the 1-minute underlying (Forward), EMA9 vs EMA21, RESET each day
(intraday-only, no overnight carry). 9 crosses above 21 -> buy ATM CALL; crosses
below -> buy ATM PUT; flip on the opposite cross; square off 15:15.

Look-ahead-safe: a cross is confirmed at a bar's CLOSE (EMAs use closes <= t),
the fill is the NEXT bar's open. ATM strike chosen on the chain as of the signal
bar. Full India F&O costs + 1% slippage/side.

Also reports SIGNAL QUALITY at the index level (does the cross predict the next
15 minutes?) so we can tell a weak signal apart from weak option mechanics.
"""
import numpy as np
import pandas as pd
from datetime import time

from engine.data.catalog import Catalog
from engine.execution.costs import CostModel

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
LOT, SLIP, MIN_DTE = 65, 0.01, 3
FAST, SLOW = 9, 21
WARMUP, ENTRY_CUTOFF, SQUARE = time(9, 45), time(14, 45), time(15, 15)
LOOKAHEAD = 15   # minutes, for index-level signal quality
CM = CostModel()


def atm(day, dt, fwd, side):
    snap = day[day["DateTime"] == dt]
    flag = "CallIsTradedStrike" if side == "CALL" else "PutIsTradedStrike"
    tr = snap[snap[flag]]
    if tr.empty:
        return None
    return int(tr.iloc[(tr["StrikePrice"] - fwd).abs().argmin()]["StrikePrice"])


def opt_open(day, dt, strike, side):
    col = "CallOpen" if side == "CALL" else "PutOpen"
    row = day[(day["DateTime"] == dt) & (day["StrikePrice"] == strike)]
    if row.empty:
        return None
    v = float(row.iloc[0][col])
    return v if v > 0 else None


def opt_close(day, dt, strike, side):
    col = "CallClose" if side == "CALL" else "PutClose"
    row = day[(day["DateTime"] == dt) & (day["StrikePrice"] == strike)]
    if row.empty:
        return None
    return float(row.iloc[0][col])


def run_day(day, fwd):
    ema_f = fwd.ewm(span=FAST, adjust=False).mean()
    ema_s = fwd.ewm(span=SLOW, adjust=False).mean()
    diff = (ema_f - ema_s)
    sgn = np.sign(diff)
    times = list(fwd.index)

    trades, sig_quality = [], []
    pos = None  # dict(side, strike, entry, fill_dt)

    def close_pos(at_dt, price_src):
        nonlocal pos
        px = price_src(day, at_dt, pos["strike"], pos["side"])
        if px is None:
            px = pos["entry"]
        sell = px * (1 - SLIP)
        qty = LOT
        buy_val, sell_val = pos["entry"] * qty, sell * qty
        cost = CM.total(buy_val, sell_val, 2)
        trades.append(dict(side=pos["side"], strike=pos["strike"],
                           entry=pos["entry"], exit=sell, premium=buy_val,
                           gross=(sell - pos["entry"]) * qty,
                           cost=cost, net=(sell - pos["entry"]) * qty - cost))
        pos = None

    for i in range(1, len(times) - 1):
        t = times[i]
        if t.time() < WARMUP or sgn.iloc[i] == 0 or sgn.iloc[i] == sgn.iloc[i - 1]:
            continue
        side = "CALL" if diff.iloc[i] > 0 else "PUT"
        fill_dt = times[i + 1]

        # index-level signal quality: forward move over next LOOKAHEAD minutes
        j = min(i + LOOKAHEAD, len(times) - 1)
        fwd_move = fwd.iloc[j] - fwd.iloc[i]
        sig_quality.append(1 if (fwd_move > 0) == (side == "CALL") else 0)

        # act at next open: close any open position, then maybe open new
        if pos is not None:
            close_pos(fill_dt, opt_open)
        if t.time() <= ENTRY_CUTOFF:
            k = atm(day, t, fwd.iloc[i], side)
            if k is None:
                continue
            o = opt_open(day, fill_dt, k, side)
            if o is None:
                continue
            pos = dict(side=side, strike=k, entry=o * (1 + SLIP), fill_dt=fill_dt)

    # square off at the last bar <= SQUARE
    if pos is not None:
        sq = [t for t in times if t.time() <= SQUARE]
        if sq:
            close_pos(sq[-1], opt_close)

    return trades, sig_quality


def main():
    cat = Catalog(ROOT)
    exp_dates = [pd.Timestamp(e).date() for e in cat.expiries()]
    daily = pd.read_csv("analysis/daily_underlying.csv", parse_dates=["date"])
    dates = [d.date() for d in daily["date"]]

    def choose(d):
        for e in exp_dates:
            if (e - d).days >= MIN_DTE:
                return e
        return None

    by_exp = {}
    for d in dates:
        e = choose(d)
        if e:
            by_exp.setdefault(e, []).append(d)

    cols = ["DateTime", "StrikePrice", "Forward",
            "CallOpen", "CallClose", "CallIsTradedStrike",
            "PutOpen", "PutClose", "PutIsTradedStrike"]
    rows, sigq, day_count = [], [], 0
    for e, ds in by_exp.items():
        p = cat.path_for(e.isoformat())
        if p is None:
            continue
        df = pd.read_parquet(p, columns=cols)
        df["date"] = df["DateTime"].dt.date
        for d in ds:
            day = df[df["date"] == d]
            if len(day) < 50:
                continue
            fwd = day.groupby("DateTime")["Forward"].first().sort_index()
            tr, sq = run_day(day, fwd)
            for r in tr:
                r["date"] = d
            rows.extend(tr)
            sigq.extend(sq)
            day_count += 1

    t = pd.DataFrame(rows)
    t.to_csv("analysis/ema_crossover_trades.csv", index=False)
    report(t, sigq, day_count)


def report(t, sigq, days):
    print("=" * 90)
    print(f"  9/21 EMA CROSSOVER  intraday option buying  ({days} days, {len(t)} trades)")
    print(f"  trades/day = {len(t)/days:.1f}   (whipsaw check)")
    print("=" * 90)
    if sigq:
        print(f"SIGNAL QUALITY (index level): cross direction matches next {LOOKAHEAD}-min move "
              f"{np.mean(sigq):.1%} of the time  (50% = no edge, n={len(sigq)})")
    print("-" * 90)
    if len(t) == 0:
        print("no trades"); return
    n = len(t)
    win = (t.net > 0).mean()
    pl = -t.loc[t.net <= 0, "net"].sum()
    pf = t.loc[t.net > 0, "net"].sum() / pl if pl else float("inf")
    r = t.net / t.premium
    tstat = r.mean() / (r.std() / np.sqrt(n)) if r.std() else 0.0
    top = t.sort_values("net", ascending=False)
    ex10 = t.net.sum() - top.head(10).net.sum()
    gross, cost = t.gross.sum(), t.cost.sum()
    print(f"RESULT: win={win:.0%}  net=Rs{t.net.sum():,.0f}  avg=Rs{t.net.mean():,.0f}  "
          f"PF={pf:.2f}  t-stat={tstat:.2f}")
    print(f"  gross=Rs{gross:,.0f}  costs=Rs{cost:,.0f} ({cost/abs(gross)*100 if gross else 0:.0f}% of gross)")
    print(f"  tail check: net excl top-10 winners = Rs{ex10:,.0f}")
    print("-" * 90)
    print("By side:")
    for s in ("CALL", "PUT"):
        ss = t[t.side == s]
        if len(ss):
            p2 = -ss.loc[ss.net <= 0, "net"].sum()
            pf2 = ss.loc[ss.net > 0, "net"].sum() / p2 if p2 else float("inf")
            print(f"  {s}: n={len(ss):4d} win={ (ss.net>0).mean():.0%} "
                  f"net=Rs{ss.net.sum():>9,.0f} PF={pf2:.2f}")
    print("By year (net):")
    t2 = t.copy(); t2["yr"] = pd.to_datetime(t2["date"]).dt.year
    for y, ss in t2.groupby("yr"):
        print(f"  {y}: n={len(ss):4d}  net=Rs{ss.net.sum():>9,.0f}")
    print("=" * 90)


if __name__ == "__main__":
    main()
