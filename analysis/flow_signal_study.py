"""Flow predictive-power study (Strategy A, step 1 -- signal triage).

Before building any option backtest, ask the only question that matters: does
early-session ORDER FLOW predict the rest-of-day direction? If it has no power,
A is dead and we don't waste effort. If it does, we build the option strategy
gated on it.

For each day at a 09:45 decision point we measure, over ATM +/-3 strikes:
  - volume imbalance  = (sum CallVol - sum PutVol)/(sum)   over 09:15-09:45
  - OI-change imbalance = (dCallOI - dPutOI)/(|.|+|.|)      09:15 -> 09:45
and the momentum control early_ret = fwd(09:45)/fwd(09:15)-1.
Target = rest-of-day forward return fwd(15:15)/fwd(09:45)-1.

Everything uses data <= 09:45 to predict > 09:45 (no look-ahead).
"""
import numpy as np
import pandas as pd
from datetime import time

from engine.data.catalog import Catalog

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
MIN_DTE = 3
T0, TDEC, TEND = time(9, 15), time(9, 45), time(15, 15)
NBAND = 3   # ATM +/- 3 strikes


def near_atm(day, dec_dt, fwd0):
    """The set of strikes within +/-NBAND*50 of the opening ATM."""
    strikes = sorted(day["StrikePrice"].unique())
    atm = min(strikes, key=lambda s: abs(s - fwd0))
    return [s for s in strikes if abs(s - atm) <= NBAND * 50]


def at(day, t):
    return day[day["DateTime"].dt.time == t]


def run_day(day):
    fwds = day.groupby("DateTime")["Forward"].first().sort_index()
    if fwds.index[0].time() != T0:
        return None
    b0, bdec = at(day, T0), at(day, TDEC)
    bend = day[day["DateTime"].dt.time <= TEND]
    if b0.empty or bdec.empty or bend.empty:
        return None
    fwd0 = b0["Forward"].iloc[0]
    fwd_dec = bdec["Forward"].iloc[0]
    fwd_end = bend.sort_values("DateTime")["Forward"].iloc[-1]
    band = set(near_atm(day, TDEC, fwd0))

    win = day[(day["DateTime"].dt.time > T0) & (day["DateTime"].dt.time <= TDEC)
              & (day["StrikePrice"].isin(band))]
    cvol, pvol = win["CallVolume"].sum(), win["PutVolume"].sum()
    if cvol + pvol <= 0:
        return None
    vol_imb = (cvol - pvol) / (cvol + pvol)

    oi0 = b0[b0["StrikePrice"].isin(band)]
    oidec = bdec[bdec["StrikePrice"].isin(band)]
    dcall = oidec["CallOI"].sum() - oi0["CallOI"].sum()
    dput = oidec["PutOI"].sum() - oi0["PutOI"].sum()
    denom = abs(dcall) + abs(dput)
    oi_imb = (dcall - dput) / denom if denom else 0.0

    return dict(early_ret=fwd_dec / fwd0 - 1.0,
                target=fwd_end / fwd_dec - 1.0,
                vol_imb=vol_imb, oi_imb=oi_imb)


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
            "CallVolume", "PutVolume", "CallOI", "PutOI"]
    rows = []
    for e, ds in by_exp.items():
        p = cat.path_for(e.isoformat())
        if p is None:
            continue
        df = pd.read_parquet(p, columns=cols)
        df["date"] = df["DateTime"].dt.date
        for d in ds:
            day = df[df["date"] == d]
            if len(day) < 20:
                continue
            r = run_day(day)
            if r:
                r["date"] = d
                rows.append(r)

    t = pd.DataFrame(rows).dropna()
    t.to_csv("analysis/flow_signal.csv", index=False)
    report(t)


def report(t):
    print("=" * 86)
    print(f"  FLOW PREDICTIVE-POWER STUDY   ({len(t)} days)")
    print("  Does 09:15-09:45 flow predict the 09:45->15:15 forward move?")
    print("=" * 86)

    def corr(x):
        return t[x].corr(t["target"])
    print("Pearson corr with rest-of-day target return:")
    print(f"  volume imbalance : {corr('vol_imb'):+.3f}")
    print(f"  OI-change imbalance: {corr('oi_imb'):+.3f}")
    print(f"  early momentum   : {corr('early_ret'):+.3f}   (control)")
    print("-" * 86)

    def sign_hit(x):
        m = t[x] != 0
        return (np.sign(t.loc[m, x]) == np.sign(t.loc[m, "target"])).mean()
    print("Directional hit-rate (sign of signal == sign of rest-of-day move; 50% = no edge):")
    print(f"  volume imbalance : {sign_hit('vol_imb'):.1%}")
    print(f"  OI-change imbalance: {sign_hit('oi_imb'):.1%}")
    print(f"  early momentum   : {sign_hit('early_ret'):.1%}   (control)")
    print("-" * 86)

    print("Mean rest-of-day target by VOLUME-imbalance tercile (bps):")
    ql, qh = t["vol_imb"].quantile([0.33, 0.66])
    for name, m in (("call-heavy (top)", t.vol_imb > qh),
                    ("neutral (mid)  ", (t.vol_imb >= ql) & (t.vol_imb <= qh)),
                    ("put-heavy (bot)", t.vol_imb < ql)):
        s = t[m]
        print(f"  {name}: n={len(s):4d}  mean={s.target.mean()*1e4:+7.1f}  "
              f"median={s.target.median()*1e4:+7.1f}  %up={ (s.target>0).mean():.0%}")
    print("-" * 86)
    print("Strong-signal subset (|vol_imb|>0.5): does it predict better?")
    s = t[t.vol_imb.abs() > 0.5]
    if len(s):
        hit = (np.sign(s.vol_imb) == np.sign(s.target)).mean()
        print(f"  n={len(s)}  directional hit-rate={hit:.1%}  "
              f"mean signed target={ (np.sign(s.vol_imb)*s.target).mean()*1e4:+.1f} bps")
    print("=" * 86)


if __name__ == "__main__":
    main()
