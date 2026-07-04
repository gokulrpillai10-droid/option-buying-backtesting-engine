"""Mean-reversion triage: does intraday OVEREXTENSION predict a FADE?

Every momentum/breakout signal we tested rode continuation and failed (~49-53%).
The flow study hinted the opposite: early move vs rest-of-day corr = -0.145
(reversion). Here we test it directly, before building any option backtest.

For each day, at decision time T we measure the move from the 09:15 open
m_T = fwd(T)/fwd(open)-1, and the rest-of-day move f_T = fwd(15:15)/fwd(T)-1.
FADE works if sign(f_T) is OPPOSITE sign(m_T). We look at all days and at the
most-stretched tercile (|m_T| top third). >54% fade hit-rate on stretched days
= an exploitable reversion edge worth building.

Look-ahead-safe: m_T uses data <= T, predicts f_T (> T).
"""
import numpy as np
import pandas as pd
from datetime import time

from engine.data.catalog import Catalog

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
MIN_DTE = 3
OPEN_T, CLOSE_T = time(9, 15), time(15, 15)
DECISIONS = [time(11, 0), time(12, 0), time(13, 0), time(14, 0)]


def fwd_at(fwds, t):
    sub = fwds[fwds.index.time <= t]
    return float(sub.iloc[-1]) if len(sub) else np.nan


def run_day(day):
    fwds = day.groupby("DateTime")["Forward"].first().sort_index()
    if fwds.index[0].time() != OPEN_T:
        return None
    op = fwd_at(fwds, OPEN_T)
    cl = fwd_at(fwds, CLOSE_T)
    if np.isnan(op) or np.isnan(cl):
        return None
    row = {}
    for T in DECISIONS:
        fT = fwd_at(fwds, T)
        tag = T.strftime("%H%M")
        row[f"m_{tag}"] = fT / op - 1.0          # move from open to T
        row[f"f_{tag}"] = cl / fT - 1.0           # rest-of-day move
    return row


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

    cols = ["DateTime", "StrikePrice", "Forward"]
    rows = []
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
            r = run_day(day)
            if r:
                r["date"] = d
                rows.append(r)

    t = pd.DataFrame(rows)
    t.to_csv("analysis/mean_reversion_triage.csv", index=False)
    report(t)


def report(t):
    print("=" * 90)
    print(f"  MEAN-REVERSION TRIAGE   ({len(t)} days)")
    print("  Does overextension from the open predict a FADE in the rest of the day?")
    print("  FADE hit = sign(rest-of-day) opposite sign(move-so-far).  >54% = exploitable")
    print("=" * 90)
    for T in DECISIONS:
        tag = T.strftime("%H%M")
        m, f = t[f"m_{tag}"], t[f"f_{tag}"]
        mask = m != 0
        corr = m.corr(f)
        fade_all = (np.sign(f[mask]) != np.sign(m[mask])).mean()
        # most-stretched tercile
        thr = m.abs().quantile(0.66)
        s = m.abs() >= thr
        fade_str = (np.sign(f[s]) != np.sign(m[s])).mean()
        # reversion payoff in points: -sign(m)*f, positive => fade pays
        signed_rev_pts = (-np.sign(m[s]) * f[s] * t.loc[s].assign(p=1)["p"]).mean()  # fraction
        rev_pts = (-np.sign(m[s]) * f[s]).mean()
        print(f"  T={T.strftime('%H:%M')}  corr(m,f)={corr:+.3f}  "
              f"fade-hit ALL={fade_all:5.1%}  STRETCHED={fade_str:5.1%}  "
              f"mean fade-return(stretched)={rev_pts*1e4:+6.1f} bps  (avg|move|={m[s].abs().mean()*100:.2f}%)")
    print("-" * 90)
    # deeper look at the strongest stretched bucket: split up-stretch vs down-stretch
    T = time(11, 0); tag = "1100"
    m, f = t[f"m_{tag}"], t[f"f_{tag}"]
    thr = m.abs().quantile(0.66)
    up = (m >= thr)
    dn = (m <= -thr)
    print(f"  Detail @11:00 stretched:")
    print(f"    UP-stretched (>= +{thr*100:.2f}%): n={up.sum():4d}  rest-of-day mean={f[up].mean()*1e4:+6.1f} bps  %down(fade)={ (f[up]<0).mean():.1%}")
    print(f"    DN-stretched (<= -{thr*100:.2f}%): n={dn.sum():4d}  rest-of-day mean={f[dn].mean()*1e4:+6.1f} bps  %up(fade)={ (f[dn]>0).mean():.1%}")
    print("=" * 90)


if __name__ == "__main__":
    main()
