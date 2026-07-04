"""Re-test the gap-down put with exits defined on OPTION PREMIUM (not index pts).

Same entry as before (gap-down >=20, break OR low by 2pts before 11:30, ATM put,
DTE>=3, fill next-min PutOpen + 1% slip). Only the stop/targets change:

  B-simple   : -30% stop, trail 25% off premium peak          (mirrors the call)
  B-scaleout : book half at +50%, trail rest 25% off peak, -30% stop

Reuses data plumbing + cost model from combined_backtest.py.
"""
import pandas as pd

import combined_backtest as cb

SLIP = cb.SLIP
QTY = cb.LOT_SIZE * cb.LOTS
STOP = 0.30
TRAIL = 0.25
BOOK1 = 0.50


def _entry(day, fwd, prev_close):
    if pd.isna(prev_close):
        return None
    om = fwd.index.time <= cb.OR_END
    gap = fwd.iloc[0] - prev_close
    if om.sum() < 5 or gap > -cb.B_GAP:
        return None
    lo = fwd[om].min()
    post = fwd[(fwd.index.time > cb.OR_END) & (fwd.index.time <= cb.B_ENTRY_END)]
    sig = next((dt for dt, px in post.items() if px < lo - cb.B_BREAK_PTS), None)
    if sig is None:
        return None
    snap = day[day["DateTime"] == sig]
    k = cb.atm(snap, fwd.loc[sig], "PutIsTradedStrike")
    if k is None:
        return None
    s = cb.opt_series(day, k, "Put")
    aft = s[(s.index > sig) & (s.index.time <= cb.SQUARE_OFF)]
    if aft.empty or aft.iloc[0]["PutOpen"] <= 0:
        return None
    return k, aft, aft.iloc[0]["PutOpen"] * (1 + SLIP), gap


def run_put_simple(day, fwd, prev_close):
    e = _entry(day, fwd, prev_close)
    if e is None:
        return None
    k, aft, fill, gap = e
    stop, peak, exit_px = fill * (1 - STOP), fill, None
    for _, r in aft.iterrows():
        peak = max(peak, r["PutHigh"])
        lvl = max(stop, peak * (1 - TRAIL))
        if r["PutLow"] <= lvl:
            exit_px = lvl
            break
    if exit_px is None:
        exit_px = aft.iloc[-1]["PutClose"]
    sell = exit_px * (1 - SLIP)
    gross = (sell - fill) * QTY
    c = cb.costs(fill * QTY, sell * QTY, 2)
    return dict(side="PUT", strike=k, gross=gross, cost=c, net=gross - c,
                premium=fill * QTY, gap=gap)


def run_put_scaleout(day, fwd, prev_close):
    e = _entry(day, fwd, prev_close)
    if e is None:
        return None
    k, aft, fill, gap = e
    stop, book1 = fill * (1 - STOP), fill * (1 + BOOK1)
    peak, remaining, booked = fill, 1.0, False
    gross, sell_val, n_sell, closed = 0.0, 0.0, 0, False
    for _, r in aft.iterrows():
        peak = max(peak, r["PutHigh"])
        lvl = max(stop, peak * (1 - TRAIL)) if booked else stop
        if r["PutLow"] <= lvl:                       # stop / trail exit on remainder
            sell = lvl * (1 - SLIP)
            gross += remaining * (sell - fill) * QTY
            sell_val += remaining * sell * QTY; n_sell += 1
            remaining = 0.0; closed = True
            break
        if not booked and r["PutHigh"] >= book1:     # book half at +50%
            sell = book1 * (1 - SLIP)
            gross += 0.5 * (sell - fill) * QTY
            sell_val += 0.5 * sell * QTY; n_sell += 1
            remaining, booked = 0.5, True
    if not closed and remaining > 0:
        sell = aft.iloc[-1]["PutClose"] * (1 - SLIP)
        gross += remaining * (sell - fill) * QTY
        sell_val += remaining * sell * QTY; n_sell += 1
    c = cb.costs(fill * QTY, sell_val, 1 + n_sell)
    return dict(side="PUT", strike=k, gross=gross, cost=c, net=gross - c,
                premium=fill * QTY, gap=gap)


def main():
    cat = cb.Catalog(cb.ROOT)
    exp_dates = [pd.Timestamp(e).date() for e in cat.expiries()]
    daily = pd.read_csv("analysis/daily_underlying.csv", parse_dates=["date"])
    daily["d"] = daily["date"].dt.date
    pclose = dict(zip(daily["d"], daily["prev_close"]))
    by_exp = {}
    for d in daily["d"]:
        ex = cb.choose_expiry(d, exp_dates)
        if ex is not None:
            by_exp.setdefault(ex, []).append(d)

    cols = ["DateTime", "StrikePrice", "Forward",
            "CallOpen", "CallHigh", "CallLow", "CallClose", "CallIsTradedStrike",
            "PutOpen", "PutHigh", "PutLow", "PutClose", "PutIsTradedStrike"]
    A, Bs, Bo = [], [], []
    for ex, dates in by_exp.items():
        p = cat.path_for(ex.isoformat())
        if p is None:
            continue
        df = pd.read_parquet(p, columns=cols)
        df["date"] = df["DateTime"].dt.date
        for d in dates:
            day = df[df["date"] == d]
            if len(day) < 20:
                continue
            fwd = day.groupby("DateTime")["Forward"].first().sort_index()
            ra = cb.run_call(day, fwd)
            if ra: ra["date"] = d; A.append(ra)
            rs = run_put_simple(day, fwd, pclose.get(d))
            if rs: rs["date"] = d; Bs.append(rs)
            ro = run_put_scaleout(day, fwd, pclose.get(d))
            if ro: ro["date"] = d; Bo.append(ro)

    def stats(name, rows):
        t = pd.DataFrame(rows)
        if t.empty:
            print(f"{name:34s} no trades"); return t
        win = (t.net > 0).mean()
        pl = -t.loc[t.net <= 0, "net"].sum()
        pf = t.loc[t.net > 0, "net"].sum() / pl if pl else 99
        print(f"{name:34s} n={len(t):4d} win={win:4.0%} net=Rs{t.net.sum():>9,.0f} "
              f"avg=Rs{t.net.mean():>6,.0f} PF={pf:4.2f}")
        return t

    print("\n" + "=" * 80)
    print(f"  PUT WITH OPTION-PREMIUM EXITS  (real fills, 1% slip/side, full F&O costs, lot {cb.LOT_SIZE})")
    print("=" * 80)
    ta = stats("A: ORB call (unchanged)", A)
    print("-" * 80)
    print("Old B (index-pt exits): see COMBINED_REPORT -> net -Rs59,131, PF 0.72")
    print("-" * 80)
    tbs = stats("B-simple: -30% stop / 25% trail", Bs)
    tbo = stats("B-scaleout: +50% book / trail / -30%", Bo)
    print("-" * 80)
    for label, tb in (("A + B-simple", tbs), ("A + B-scaleout", tbo)):
        comb = pd.concat([ta, tb], ignore_index=True)
        stats(f"COMBINED {label}", comb)
    print("-" * 80)
    print("B-simple by gap size (is there a salvage in big panic gaps?):")
    for lo, hi, nm in [(20, 50, "gap 20-50"), (50, 100, "gap 50-100"), (100, 1e9, "gap >100")]:
        s = tbs[(-tbs.gap >= lo) & (-tbs.gap < hi)]
        if len(s) >= 8:
            pl = -s.loc[s.net <= 0, "net"].sum(); pf = s.loc[s.net > 0, "net"].sum() / pl if pl else 99
            print(f"  {nm:12s} n={len(s):3d} win={(s.net>0).mean():4.0%} net=Rs{s.net.sum():>8,.0f} PF={pf:4.2f}")
    print("=" * 80)


if __name__ == "__main__":
    main()
