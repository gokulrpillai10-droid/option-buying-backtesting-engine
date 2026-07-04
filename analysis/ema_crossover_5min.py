"""9/21 EMA crossover on the 5-MINUTE timeframe -- exact user spec.

Spec:
  - EMA9 vs EMA21 on 5-minute underlying bars, monitoring from 09:15, reset daily.
  - Bullish cross (9 above 21) -> buy ATM/near-ATM CALL.
  - Bearish cross (9 below 21) -> buy ATM/near-ATM PUT.
  - Intraday, naked, NOT expiry-day (nearest expiry, DTE>=3).
  - Exit ONLY on: position P&L >= +72,000 ; P&L <= -72,000 ; or 15:10 time-stop.
    (No flip on opposite cross. One position at a time; re-enter on the next
     fresh cross after going flat.)

NOTE: for 1 lot (65 qty) the +/-72,000 thresholds need a +/-Rs1,108 premium move,
so they almost never fire -> in practice this is a hold-to-15:10 system. The
report shows how often each exit actually triggers and the average hold time.

Look-ahead-safe: cross confirmed at a 5-min bar close; fill at the next 1-min
open. Full India F&O costs + 1% slippage/side.
"""
import numpy as np
import pandas as pd
from datetime import time

from engine.data.catalog import Catalog
from engine.execution.costs import CostModel

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
LOT, SLIP, MIN_DTE = 65, 0.01, 3
FAST, SLOW = 9, 21
TARGET, STOPLOSS = 2000.0, 2000.0
TIME_EXIT = time(15, 10)
CM = CostModel()


def opt_frame(day, strike, side):
    pfx = "Call" if side == "CALL" else "Put"
    s = day[day["StrikePrice"] == strike].set_index("DateTime").sort_index()
    return s[[pfx + "Open", pfx + "High", pfx + "Low", pfx + "Close"]]


def atm(day, dt, fwd, side):
    snap = day[day["DateTime"] == dt]
    flag = "CallIsTradedStrike" if side == "CALL" else "PutIsTradedStrike"
    tr = snap[snap[flag]]
    if tr.empty:
        return None
    return int(tr.iloc[(tr["StrikePrice"] - fwd).abs().argmin()]["StrikePrice"])


def run_day(day, fwd1):
    # 5-min bars (right-labelled close) from the 1-min forward
    fwd5 = fwd1.resample("5min", label="right", closed="right").last().dropna()
    if len(fwd5) < 3:
        return []
    ema_f = fwd5.ewm(span=FAST, adjust=False).mean()
    ema_s = fwd5.ewm(span=SLOW, adjust=False).mean()
    diff = ema_f - ema_s
    sgn = np.sign(diff)
    b5 = list(fwd5.index)
    times1 = list(fwd1.index)

    trades = []
    busy_until = None  # 1-min timestamp we are flat again after

    for i in range(1, len(b5)):
        T = b5[i]
        if sgn.iloc[i] == 0 or sgn.iloc[i] == sgn.iloc[i - 1]:
            continue
        if busy_until is not None and T <= busy_until:
            continue  # still in a position from an earlier cross
        side = "CALL" if diff.iloc[i] > 0 else "PUT"
        # fill at first 1-min bar strictly after the 5-min close T
        later = [t for t in times1 if t > T]
        if not later:
            continue
        fill_dt = later[0]
        if fill_dt.time() >= TIME_EXIT:
            continue
        k = atm(day, T, fwd5.iloc[i], side)
        if k is None:
            continue
        of = opt_frame(day, k, side)
        aft = of[of.index >= fill_dt]
        if aft.empty:
            continue
        pfx = "Call" if side == "CALL" else "Put"
        o0 = float(aft.iloc[0][pfx + "Open"])
        if o0 <= 0:
            continue
        entry = o0 * (1 + SLIP)
        qty = LOT

        exit_px, reason, exit_dt = None, None, None
        for dt, r in aft.iterrows():
            hi, lo, cl = float(r[pfx + "High"]), float(r[pfx + "Low"]), float(r[pfx + "Close"])
            if dt.time() >= TIME_EXIT:
                exit_px, reason, exit_dt = cl, "time", dt
                break
            # adverse first (conservative), then favourable
            if (lo - entry) * qty <= -STOPLOSS:
                exit_px, reason, exit_dt = entry - STOPLOSS / qty, "stop72k", dt
                break
            if (hi - entry) * qty >= TARGET:
                exit_px, reason, exit_dt = entry + TARGET / qty, "target72k", dt
                break
        if exit_px is None:
            exit_px, reason, exit_dt = float(aft.iloc[-1][pfx + "Close"]), "eod", aft.index[-1]

        sell = exit_px * (1 - SLIP)
        cost = CM.total(entry * qty, sell * qty, 2)
        hold = (exit_dt - fill_dt).total_seconds() / 60.0
        trades.append(dict(side=side, strike=k, entry=entry, exit=sell, premium=entry * qty,
                           gross=(sell - entry) * qty, cost=cost, net=(sell - entry) * qty - cost,
                           reason=reason, hold_min=hold))
        busy_until = exit_dt

    return trades


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
            "CallOpen", "CallHigh", "CallLow", "CallClose", "CallIsTradedStrike",
            "PutOpen", "PutHigh", "PutLow", "PutClose", "PutIsTradedStrike"]
    rows, days = [], 0
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
            fwd1 = day.groupby("DateTime")["Forward"].first().sort_index()
            tr = run_day(day, fwd1)
            for r in tr:
                r["date"] = d
            rows.extend(tr)
            days += 1

    t = pd.DataFrame(rows)
    t.to_csv("analysis/ema_5min_2k_trades.csv", index=False)
    report(t, days)


def report(t, days):
    print("=" * 90)
    print(f"  9/21 EMA CROSSOVER (5-min)  ATM buy, exits +/-Rs2k or 15:10  ({days} days, {len(t)} trades)")
    print(f"  trades/day = {len(t)/days:.2f}   avg hold = {t.hold_min.mean():.0f} min")
    print("=" * 90)
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
    print(f"  median premium deployed/trade = Rs{t.premium.median():,.0f}  (1 lot, 65 qty)")
    print("-" * 90)
    print("Exit reason breakdown (target72k/stop72k labels = the +/-Rs2k target/stop):")
    print(t["reason"].value_counts().to_string())
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
