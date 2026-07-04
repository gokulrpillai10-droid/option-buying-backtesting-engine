"""Triage for two fresh option-BUYING angles, before any full backtest.

Q1 LATE-DAY MOMENTUM PERSISTENCE (attacks theta via short holding time):
   at decision time T, does sign(move 09:20->T) predict sign(move T->15:15)?
   Tested for T in {11:00, 13:00, 14:15}. >55% hit-rate => exploitable, and a
   14:15 entry only holds ~1h so theta is small.

Q2 LONG-STRADDLE ECONOMICS (sidesteps direction entirely):
   buy the ATM straddle at 09:20, sell at 15:15 (same strike), full costs + slip.
   Does realised range pay for the premium? Does cheap morning IV/RV help?

Look-ahead-safe: each predictor uses only data up to its decision time.
"""
import numpy as np
import pandas as pd
from datetime import time

from engine.data.catalog import Catalog
from engine.execution.costs import CostModel

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
LOT, SLIP, MIN_DTE = 65, 0.01, 3
T_IV = time(9, 20)
SQUARE = time(15, 15)
DECISIONS = [time(11, 0), time(13, 0), time(14, 15)]
RV_WINDOW = 10
CM = CostModel()


def realised_vol():
    d = pd.read_csv("analysis/daily_underlying.csv", parse_dates=["date"]).sort_values("date")
    ret = np.log(d["close"] / d["close"].shift(1))
    d["rv"] = (ret.rolling(RV_WINDOW).std() * np.sqrt(252)).shift(1)
    return dict(zip(d["date"].dt.date, d["rv"]))


def fwd_at(fwds, t):
    sub = fwds[fwds.index.time <= t]
    return float(sub.iloc[-1]) if len(sub) else np.nan


def run_day(day, rv_d):
    fwds = day.groupby("DateTime")["Forward"].first().sort_index()
    if fwds.index[0].time() != time(9, 15):
        return None
    f0 = fwd_at(fwds, T_IV)
    fend = fwd_at(fwds, SQUARE)
    if np.isnan(f0) or np.isnan(fend):
        return None

    row = dict(date=None)
    for T in DECISIONS:
        fT = fwd_at(fwds, T)
        row[f"mom_{T.hour}{T.minute:02d}"] = fT - f0
        row[f"fut_{T.hour}{T.minute:02d}"] = fend - fT

    # straddle at 09:20 -> 15:15
    bar0 = day[day["DateTime"].dt.time == T_IV]
    if bar0.empty:
        return None
    atmrow = bar0.iloc[(bar0["StrikePrice"] - f0).abs().argmin()]
    k = int(atmrow["StrikePrice"])
    iv = float(atmrow["IV"])
    ks = day[day["StrikePrice"] == k].set_index("DateTime").sort_index()
    o = ks[ks.index.time == T_IV]
    c = ks[ks.index.time <= SQUARE]
    if o.empty or c.empty:
        return None
    c0 = float(o["CallClose"].iloc[0]) + float(o["PutClose"].iloc[0])
    c1 = float(c["CallClose"].iloc[-1]) + float(c["PutClose"].iloc[-1])
    if c0 <= 0:
        return None
    buy = c0 * (1 + SLIP) * LOT
    sell = c1 * (1 - SLIP) * LOT
    cost = CM.total(buy, sell, 4)   # 2 legs in + 2 legs out
    row["straddle_net"] = (sell - buy) - cost
    row["straddle_prem"] = buy
    row["iv"] = iv
    row["rv"] = rv_d
    row["ratio"] = iv / rv_d if rv_d else np.nan
    row["realised_move"] = abs(fend - f0)
    return row


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
            "CallClose", "PutClose"]
    rows = []
    for e, ds in by_exp.items():
        p = cat.path_for(e.isoformat())
        if p is None:
            continue
        df = pd.read_parquet(p, columns=cols)
        df["date"] = df["DateTime"].dt.date
        for d in ds:
            if pd.isna(rv.get(d)):
                continue
            day = df[df["date"] == d]
            if len(day) < 20:
                continue
            r = run_day(day, rv.get(d))
            if r:
                r["date"] = d
                rows.append(r)

    t = pd.DataFrame(rows)
    t.to_csv("analysis/buying_triage.csv", index=False)
    report(t)


def report(t):
    print("=" * 88)
    print(f"  OPTION-BUYING TRIAGE   ({len(t)} days)")
    print("=" * 88)
    print("Q1  LATE-DAY MOMENTUM PERSISTENCE (does sign(09:20->T) predict sign(T->15:15)?)")
    print("    >55% = exploitable continuation;  <45% = reversal (fade it)")
    for T in DECISIONS:
        tag = f"{T.hour}{T.minute:02d}"
        m, f = t[f"mom_{tag}"], t[f"fut_{tag}"]
        mask = m != 0
        hit = (np.sign(m[mask]) == np.sign(f[mask])).mean()
        # signed continuation return in points
        signed = (np.sign(m[mask]) * f[mask]).mean()
        print(f"    T={T.strftime('%H:%M')}  hit={hit:5.1%}  mean signed continuation={signed:+6.1f} pts"
              f"   (avg |move so far|={m.abs().mean():5.1f} pts)")
    print("-" * 88)
    print("    Strong-trend subset (|move 09:20->T| in top tercile):")
    for T in DECISIONS:
        tag = f"{T.hour}{T.minute:02d}"
        m, f = t[f"mom_{tag}"], t[f"fut_{tag}"]
        thr = m.abs().quantile(0.66)
        s = m.abs() > thr
        hit = (np.sign(m[s]) == np.sign(f[s])).mean()
        signed = (np.sign(m[s]) * f[s]).mean()
        print(f"    T={T.strftime('%H:%M')}  n={s.sum():4d}  hit={hit:5.1%}  signed cont={signed:+6.1f} pts")
    print("=" * 88)
    print("Q2  LONG ATM STRADDLE 09:20->15:15 (full costs + slip):")
    s = t.dropna(subset=["straddle_net"])
    net = s["straddle_net"].sum()
    win = (s["straddle_net"] > 0).mean()
    pl = -s.loc[s.straddle_net <= 0, "straddle_net"].sum()
    pf = s.loc[s.straddle_net > 0, "straddle_net"].sum() / pl if pl else float("inf")
    print(f"    ALL: n={len(s)}  win={win:.0%}  net=Rs{net:,.0f}  avg=Rs{s.straddle_net.mean():,.0f}  PF={pf:.2f}")
    print("    By IV/RV ratio (cheap vol should help a long straddle):")
    ql, qh = s["ratio"].quantile([0.33, 0.66])
    for name, m in (("cheap (low IV/RV)", s.ratio <= ql),
                    ("mid             ", (s.ratio > ql) & (s.ratio <= qh)),
                    ("rich (high IV/RV)", s.ratio > qh)):
        ss = s[m]
        p2 = -ss.loc[ss.straddle_net <= 0, "straddle_net"].sum()
        pf2 = ss.loc[ss.straddle_net > 0, "straddle_net"].sum() / p2 if p2 else float("inf")
        print(f"      {name}: n={len(ss):4d} win={ (ss.straddle_net>0).mean():.0%} "
              f"net=Rs{ss.straddle_net.sum():>9,.0f} avg=Rs{ss.straddle_net.mean():>6,.0f} PF={pf2:.2f}")
    print("=" * 88)


if __name__ == "__main__":
    main()
