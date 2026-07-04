"""Definitive reconciliation: corrected G12-CE signal on THEIR Kite spot 5-min data
(tradealgo/nifty_5min_5yr.csv), priced from OUR Data-v3 store.

Isolates the spot-vs-Forward variable: same signal logic as g12_ce_corrected.py,
but the underlying is their actual Kite spot (start-labeled 5-min bars). Option
fills priced at signal_ts + 5 min (bar close) from Data-v3 -- their look-ahead rule.
If this reproduces ~+Rs163k the edge is real on spot; if it loses, it isn't.
"""
import os
import numpy as np
import pandas as pd
from datetime import date

from engine.data.catalog import Catalog
from engine.execution.costs import CostModel
from g12_ce_corrected import process_day, lot_size, LOTS, SLIP, STOP_EXTRA

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
# Prefer the full 5yr spot once downloaded; else fall back to the real-spot file on
# disk (nifty_5min_data.csv = real Kite spot 2025-04..2026-04).
SPOT = "/Users/gokul/Desktop/tradealgo/nifty_5min_full.csv"
if not os.path.exists(SPOT):
    SPOT = "/Users/gokul/Desktop/tradealgo/nifty_5min_data.csv"
CM = CostModel()


def gen_signals():
    s = pd.read_csv(SPOT, parse_dates=["date"])
    if s["date"].dt.tz is not None:
        s["date"] = s["date"].dt.tz_localize(None)
    s = s[s["date"] >= "2020-01-01"].sort_values("date").reset_index(drop=True)
    s["d"] = s["date"].dt.date
    dh = s.groupby("d")["high"].max(); do = s.groupby("d")["open"].first(); dc = s.groupby("d")["close"].last()
    days = sorted(s["d"].unique())
    prev = {d: days[i - 1] for i, d in enumerate(days) if i > 0}

    core, filt = [], []
    for d, g in s.groupby("d"):
        bars = g.set_index("date")[["open", "high", "low", "close"]]
        bars.columns = ["o", "h", "l", "c"]
        for tr in process_day(bars):
            core.append((d,) + tr)
        if pd.Timestamp(d).weekday() == 2 or d not in prev:
            continue
        if (do[d] - dc[prev[d]]) >= 50:
            continue
        for tr in process_day(bars, {"prev_high": dh[prev[d]]}):
            filt.append((d,) + tr)
    return core, filt


def price(sigs, cat, exp_dates):
    def nearest_expiry(d):
        for e in exp_dates:
            if e >= d:
                return e
        return None
    by_exp = {}
    for sig in sigs:
        d = sig[0]
        e = nearest_expiry(d)
        if e:
            by_exp.setdefault(e, []).append(sig)

    cols = ["DateTime", "StrikePrice", "CallClose"]
    rows, miss = [], 0
    for e, sgs in by_exp.items():
        p = cat.path_for(e.isoformat())
        if p is None:
            miss += len(sgs); continue
        strikes = sorted({s[3] for s in sgs})
        df = pd.read_parquet(p, columns=cols, filters=[("StrikePrice", "in", strikes)])
        df["DateTime"] = pd.to_datetime(df["DateTime"])
        for d, et, xt, sk, reason, t3 in sgs:
            sub = df[(df["StrikePrice"] == sk) & (df["DateTime"].dt.date == d)].set_index("DateTime").sort_index()
            if sub.empty:
                miss += 1; continue

            def at(ts):
                t = pd.Timestamp(ts) + pd.Timedelta(minutes=5)   # bar close
                if t in sub.index:
                    return float(sub.loc[t, "CallClose"])
                pos = min(sub.index.searchsorted(t), len(sub) - 1)
                return float(sub.iloc[pos]["CallClose"])
            ein, exo = at(et), at(xt)
            if ein <= 0 or exo <= 0:
                miss += 1; continue
            qty = LOTS * lot_size(d)
            entry = ein + SLIP
            exit_ = exo - SLIP - (STOP_EXTRA if "Stop" in reason else 0)
            gross = (exit_ - entry) * qty
            cost = CM.total(entry * qty, exit_ * qty, 2)
            rows.append(dict(date=d, strike=sk, reason=reason, t3=t3,
                             entry=entry, exit=exit_, premium=entry * qty,
                             gross=gross, cost=cost, net=gross - cost,
                             entry_dt=str(pd.Timestamp(et)), exit_dt=str(pd.Timestamp(xt))))
    return pd.DataFrame(rows), miss


def report(t, miss):
    if len(t) == 0:
        print(f"  no trades (missed {miss})"); return
    n = len(t); net = t.net.sum()
    win = (t.net > 0).mean()
    pl = -t.loc[t.net <= 0, "net"].sum()
    pf = t.loc[t.net > 0, "net"].sum() / pl if pl else float("inf")
    r = t.net / t.premium
    tstat = r.mean() / (r.std() / np.sqrt(n)) if r.std() else 0.0
    yrs = (pd.Timestamp(t.date.max()) - pd.Timestamp(t.date.min())).days / 365.25
    top = t.sort_values("net", ascending=False)
    print(f"  {n} trades | NET Rs{net:,.0f} | {net/500000/yrs*100:+.1f}% ann | win {win:.0%} | "
          f"PF {pf:.2f} | t {tstat:.2f}  (missed {miss})")
    print(f"  tail: excl top-10 Rs{net-top.head(10).net.sum():,.0f}")
    print("  by year:", {int(y): int(s.net.sum()) for y, s in t.assign(y=pd.to_datetime(t.date).dt.year).groupby("y")})


def main():
    cat = Catalog(ROOT)
    exp_dates = sorted(pd.Timestamp(e).date() for e in cat.expiries())
    core, filt = gen_signals()
    print(f"Raw signals -- core {len(core)}, filtered {len(filt)}")
    print("\n>>> SPOT-DATA (their underlying) -- CORE FIB (no filters)")
    core_df, core_miss = price(core, cat, exp_dates)
    core_df.to_csv("analysis/g12_spot_core_trades.csv", index=False)   # reconciliation reference
    report(core_df, core_miss)
    print("\n>>> SPOT-DATA -- FILTERED  (compare to their +Rs163-185k / 106 trades)")
    report(*price(filt, cat, exp_dates))


if __name__ == "__main__":
    main()
