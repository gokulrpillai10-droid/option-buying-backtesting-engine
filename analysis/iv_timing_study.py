"""IV-cheapness study (Strategy B candidate).

Hypothesis: intraday option BUYING only pays when implied vol is cheap relative
to what the index actually realises. We take a neutral directional trigger
(opening-range breakout, either side), buy the ATM option, and split the SAME
trades by the IV/realised-vol ratio measured at entry. If the edge lives in the
cheap-IV bucket and survives costs + a tail check, we have something.

Honest-but-approximate (pre-golden, same engine cost model): minute fills at
option O/H/L/C, 1% slip/side, full India F&O costs, no look-ahead (signal data
<= t, fill at t+1, realised vol uses only prior sessions).
"""
import numpy as np
import pandas as pd
from datetime import time

from engine.data.catalog import Catalog
from engine.execution.costs import CostModel

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
LOT, SLIP, MIN_DTE = 65, 0.01, 3
OR_END, ENTRY_END, SQUARE_OFF = time(9, 29), time(13, 0), time(15, 15)
IV_SAMPLE = time(9, 20)
BREAK, STOP, TRAIL = 0.0005, 0.30, 0.25
RV_WINDOW = 10
CM = CostModel()


def realised_vol():
    d = pd.read_csv("analysis/daily_underlying.csv", parse_dates=["date"])
    d = d.sort_values("date")
    ret = np.log(d["close"] / d["close"].shift(1))
    # shift(1): RV known at the OPEN of day t uses returns through t-1 only
    d["rv"] = (ret.rolling(RV_WINDOW).std() * np.sqrt(252)).shift(1)
    return dict(zip(d["date"].dt.date, d["rv"]))


def atm(snap, fwd, flag):
    tr = snap[snap[flag]]
    if tr.empty:
        return None
    return int(tr.iloc[(tr["StrikePrice"] - fwd).abs().argmin()]["StrikePrice"])


def trade(day, fwd, side, k):
    pfx = "Call" if side == "CALL" else "Put"
    s = day[day["StrikePrice"] == k].set_index("DateTime").sort_index()
    s = s[[pfx + "Open", pfx + "High", pfx + "Low", pfx + "Close"]]
    return s


def run_day(day, fwd, sig_dt, side):
    snap = day[day["DateTime"] == sig_dt]
    flag = "CallIsTradedStrike" if side == "CALL" else "PutIsTradedStrike"
    k = atm(snap, fwd.loc[sig_dt], flag)
    if k is None:
        return None
    pfx = "Call" if side == "CALL" else "Put"
    s = trade(day, fwd, side, k)
    aft = s[s.index > sig_dt]
    if aft.empty or aft.iloc[0][pfx + "Open"] <= 0:
        return None
    fill = aft.iloc[0][pfx + "Open"] * (1 + SLIP)
    stop = fill * (1 - STOP)
    peak, exit_px = fill, None
    for _, r in aft[aft.index.time <= SQUARE_OFF].iterrows():
        peak = max(peak, r[pfx + "High"])
        lvl = max(stop, peak * (1 - TRAIL))
        if r[pfx + "Low"] <= lvl:
            exit_px = lvl
            break
    if exit_px is None:
        exit_px = aft[aft.index.time <= SQUARE_OFF].iloc[-1][pfx + "Close"]
    sell = exit_px * (1 - SLIP)
    qty = LOT
    buy_val, sell_val = fill * qty, sell * qty
    cost = CM.total(buy_val, sell_val, 2)
    return dict(side=side, strike=k, premium=buy_val,
                gross=(sell - fill) * qty, cost=cost, net=(sell - fill) * qty - cost)


def signal(fwd):
    """Opening-range breakout, either side, before ENTRY_END. Returns (dt, side)."""
    om = fwd.index.time <= OR_END
    if om.sum() < 5:
        return None
    hi, lo = fwd[om].max(), fwd[om].min()
    post = fwd[(fwd.index.time > OR_END) & (fwd.index.time <= ENTRY_END)]
    for dt, px in post.items():
        if px > hi * (1 + BREAK):
            return dt, "CALL"
        if px < lo * (1 - BREAK):
            return dt, "PUT"
    return None


def main():
    cat = Catalog(ROOT)
    exp_dates = [pd.Timestamp(e).date() for e in cat.expiries()]
    rv = realised_vol()
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

    cols = ["DateTime", "StrikePrice", "Forward", "IV",
            "CallOpen", "CallHigh", "CallLow", "CallClose", "CallIsTradedStrike",
            "PutOpen", "PutHigh", "PutLow", "PutClose", "PutIsTradedStrike"]
    rows = []
    for e, ds in by_exp.items():
        p = cat.path_for(e.isoformat())
        if p is None:
            continue
        df = pd.read_parquet(p, columns=cols)
        df["date"] = df["DateTime"].dt.date
        for d in ds:
            day = df[df["date"] == d]
            if len(day) < 20 or pd.isna(rv.get(d)) or rv.get(d) in (0, None):
                continue
            fwd = day.groupby("DateTime")["Forward"].first().sort_index()
            sig = signal(fwd)
            if sig is None:
                continue
            sig_dt, side = sig
            # ATM IV sampled at 09:20 (look-ahead-safe: before the OR even ends)
            ivbar = day[day["DateTime"].dt.time == IV_SAMPLE]
            if ivbar.empty:
                continue
            fwd0 = ivbar["Forward"].iloc[0]
            iv = ivbar.iloc[(ivbar["StrikePrice"] - fwd0).abs().argmin()]["IV"]
            if iv <= 0:
                continue
            r = run_day(day, fwd, sig_dt, side)
            if not r:
                continue
            r.update(date=d, iv=float(iv), rv=float(rv[d]), ratio=float(iv) / float(rv[d]))
            rows.append(r)

    t = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    t.to_csv("analysis/iv_timing_trades.csv", index=False)
    report(t)


def stats(s):
    n = len(s)
    if n == 0:
        return "no trades"
    win = (s.net > 0).mean()
    pl = -s.loc[s.net <= 0, "net"].sum()
    pf = s.loc[s.net > 0, "net"].sum() / pl if pl else float("inf")
    r = s.net / s.premium
    tstat = r.mean() / (r.std() / np.sqrt(n)) if n > 1 and r.std() else 0.0
    top = s.sort_values("net", ascending=False)
    ex10 = s.net.sum() - top.head(10).net.sum()
    return (f"n={n:4d} win={win:4.0%} net=Rs{s.net.sum():>9,.0f} "
            f"avg=Rs{s.net.mean():>6,.0f} PF={pf:4.2f} t={tstat:5.2f} exTop10=Rs{ex10:>9,.0f}")


def report(t):
    print("=" * 92)
    print("  IV-CHEAPNESS STUDY  (ORB breakout either side, ATM option, full costs, 1% slip)")
    print(f"  {len(t)} trades | IV/RV ratio measured at 09:20 | bottom ratio = cheap vol")
    print("=" * 92)
    print("ALL                :", stats(t))
    print("-" * 92)
    q = t["ratio"].quantile([0.33, 0.66])
    lo, hi = q.iloc[0], q.iloc[1]
    print(f"IV/RV terciles: cheap < {lo:.2f} < mid < {hi:.2f} < rich")
    print("CHEAP vol (low IV/RV):", stats(t[t.ratio <= lo]))
    print("MID                  :", stats(t[(t.ratio > lo) & (t.ratio <= hi)]))
    print("RICH vol (high IV/RV):", stats(t[t.ratio > hi]))
    print("-" * 92)
    print("IV/RV < 1.0 (IV below realised):", stats(t[t.ratio < 1.0]))
    print("IV/RV >= 1.0                   :", stats(t[t.ratio >= 1.0]))
    print("-" * 92)
    for side in ("CALL", "PUT"):
        print(f"{side:4s} cheap only        :", stats(t[(t.side == side) & (t.ratio <= lo)]))
    print("=" * 92)


if __name__ == "__main__":
    main()
