"""Independent re-run of G12-CE through the build-strategies data layer.

The signal logic is a faithful port of tradealgo/nifty_fib_backtest.py (the core
FIB engine that generated real_data_trades_5yr.csv): after an >=80pt drop from
the INTRADAY high to a swing low, buy an ITM call (strike = floor(spot/50)*50-150)
on the first 5-min candle that CLOSES back above the 0.44 fib retrace, in the
10:00-12:15 window. Exits: 0.22 fib stop, 0.77/1.00/1.22 fib targets, trail 12
premium pts after the 1.22 extension, EOD 15:15. Max 2 trades/day.

INDEPENDENCE: signal runs on OUR Forward series (resampled 5-min); pricing uses
OUR Data-v3 real CallClose at the strike & minute; OUR CostModel + slippage.
Look-ahead: entry/exit priced at the 5-min bar CLOSE minute (first instant the
candle is known) -- same correction their report applied.

Slippage: 1.5 premium pts/side + 2 pts adverse on stop exits (their model).
"""
import numpy as np
import pandas as pd
from datetime import datetime, time, date

from engine.data.catalog import Catalog
from engine.data.integrity import at_most_one_row
from engine.execution.costs import CostModel

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
LOTS = 3
MIN_DROP, DELTA, TRAIL_PREM = 80.0, 0.70, 12.0
SLIP, STOP_EXTRA = 1.5, 2.0
LOT_CHANGE = date(2025, 12, 30)
CM = CostModel()


def lot_size(d):
    return 65 if d >= LOT_CHANGE else 75


def calc_strike(entry):
    return (int(entry) // 50) * 50 - 150


def fib_levels(hi, lo):
    r = hi - lo
    return {"f000": lo, "f022": lo + 0.22 * r, "f044": lo + 0.44 * r,
            "f077": lo + 0.77 * r, "f100": hi, "f122": lo + 1.22 * r}


def five_min_bars(fwd1):
    g = fwd1.resample("5min", label="right", closed="right")
    b = pd.DataFrame({"o": g.first(), "h": g.max(), "l": g.min(), "c": g.last()}).dropna()
    return b


def process_day(bars, filt=None):
    """Port of process_day; returns list of (entry_ts, exit_ts, strike, reason, t3).

    filt=None -> core FIB only. filt=dict(prev_high, prev_close, day_open) ->
    also apply F1/F2/F3/F8 at entry (F7/F9 are day-level, handled by caller).
    """
    EA, EB, EOD = time(10, 0), time(12, 15), time(15, 15)
    trades = []
    day_hi, day_lo, hi_idx = -np.inf, np.inf, -1
    lfib, strat_on, b044 = None, False, False
    in_t, tfib, ep, et, sk = False, None, None, None, None
    t1 = t2 = t3 = trailing = False
    peak, tcount, sl_done = -np.inf, 0, False

    ts_list = list(bars.index)
    for idx, ts in enumerate(ts_list):
        o, h, l, c = bars.iloc[idx][["o", "h", "l", "c"]]
        tt = ts.time()

        if tt >= EOD:
            if in_t:
                trades.append((et, ts, sk, "EOD", t3))
            break

        if h > day_hi:
            day_hi, hi_idx, day_lo = h, idx, l
            strat_on, b044, lfib = False, False, None
        elif idx >= hi_idx and l < day_lo:
            day_lo = l

        if not in_t:
            drop = day_hi - day_lo if day_lo != np.inf else 0
            if drop >= MIN_DROP:
                nf = fib_levels(day_hi, day_lo)
                if lfib is None:
                    lfib, strat_on = nf, True
                elif day_lo < lfib["f000"]:
                    lfib, b044 = nf, False

        if in_t:
            f = tfib
            if not t1 and h >= f["f077"]:
                t1 = True
            if not t2 and h >= f["f100"]:
                t2 = True
            if not t3 and h >= f["f122"]:
                t3, trailing, peak = True, True, max(peak, h)
            if trailing:
                peak = max(peak, h)
                trail_sl = peak - TRAIL_PREM / DELTA
                if l <= trail_sl:
                    trades.append((et, ts, sk, "Trail SL", t3))
                    in_t, tcount, b044 = False, tcount + 1, False
                    continue
            else:
                if l <= f["f022"]:
                    trades.append((et, ts, sk, "Stop Loss", t3))
                    in_t, tcount, sl_done = False, tcount + 1, True
                    break
            continue

        if not strat_on or lfib is None or sl_done or tcount >= 2:
            continue
        if not b044 and l < lfib["f044"]:
            b044 = True
        if not b044:
            continue
        if tt < EA or tt >= EB:
            continue
        if c > lfib["f044"]:
            if filt is not None:                       # F1/F2/F3/F8 entry filters
                ph = filt["prev_high"]
                if np.isnan(ph):
                    continue
                if c >= ph - 100:                      # F1: not chasing breakouts
                    continue
                if day_hi > ph:                        # F2: no breakout above prev high today
                    continue
                if c >= day_hi - 30:                   # F3: genuine pullback
                    continue
                cp = (c - l) / (h - l) if h > l else 0.0
                if cp < 0.80:                          # F8: strong-close candle
                    continue
            ep, et, sk = c, ts, calc_strike(c)
            tfib, in_t = dict(lfib), True
            t1 = t2 = t3 = trailing = False
            peak = ep

    return trades


def call_close(day, ts, strike):
    row = at_most_one_row(day, DateTime=ts, StrikePrice=strike)   # guarded: fails on duplicate context
    if row is None:
        return None
    v = float(row["CallClose"])
    return v if v > 0 else None


def is_expiry_day(d):
    return d.weekday() == (1 if d >= date(2025, 9, 1) else 3)


def price_trades(day, raw, miss_counter, d):
    out = []
    for et, xt, sk, reason, t3 in raw:
        ein, exo = call_close(day, et, sk), call_close(day, xt, sk)
        if ein is None or exo is None:
            miss_counter[0] += 1
            continue
        qty = LOTS * lot_size(d)
        entry = ein + SLIP
        exit_ = exo - SLIP - (STOP_EXTRA if "Stop" in reason else 0)
        gross = (exit_ - entry) * qty
        cost = CM.total(entry * qty, exit_ * qty, 2)
        out.append(dict(date=d, strike=sk, reason=reason, t3=t3, qty=qty,
                        entry=entry, exit=exit_, premium=entry * qty,
                        gross=gross, cost=cost, net=gross - cost))
    return out


def main():
    cat = Catalog(ROOT)
    exp_dates = sorted(pd.Timestamp(e).date() for e in cat.expiries())
    daily = pd.read_csv("analysis/daily_underlying.csv", parse_dates=["date"]).sort_values("date")
    daily["prev_high"] = daily["high"].shift(1)
    ctx = {r["date"].date(): (r["prev_high"], r["prev_close"], r["open"])
           for _, r in daily.iterrows()}
    dates = [d.date() for d in daily["date"]]

    def nearest_expiry(d):
        for e in exp_dates:
            if e >= d:
                return e
        return None
    by_exp = {}
    for d in dates:
        e = nearest_expiry(d)
        if e:
            by_exp.setdefault(e, []).append(d)

    cols = ["DateTime", "StrikePrice", "Forward", "CallClose", "CallIsTradedStrike"]
    rows_core, rows_filt, miss = [], [], [0]
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
            bars = five_min_bars(fwd1)
            if len(bars) < 5:
                continue
            rows_core += price_trades(day, process_day(bars), miss, d)
            # filtered mode: F7 (skip WEDNESDAY, per their code) + F9 (no gap-up >=50)
            ph, pc, op = ctx.get(d, (np.nan, np.nan, np.nan))
            if d.weekday() == 2 or (not np.isnan(pc) and (op - pc) >= 50):
                continue
            filt = {"prev_high": ph, "prev_close": pc, "day_open": op}
            rows_filt += price_trades(day, process_day(bars, filt), miss, d)

    pd.DataFrame(rows_core).to_csv("analysis/g12_ce_engine_trades.csv", index=False)
    print(">>> CORE FIB (no F1/F2/F3/F8/F9 filters)")
    report(pd.DataFrame(rows_core), miss[0])
    print("\n>>> FILTERED (F1/F2/F3/F7/F8/F9 applied) -- compare to their +Rs163-185k / 106 trades")
    report(pd.DataFrame(rows_filt), miss[0])


def report(t, miss):
    print("=" * 90)
    print(f"  G12-CE INDEPENDENT RE-RUN (build-strategies engine, real Data-v3 fills)")
    print(f"  {len(t)} trades 2021-2026 | 3 lots | slippage 1.5pt/side (+2 on stops) | India F&O costs")
    print(f"  (option-price lookups missed: {miss})")
    print("=" * 90)
    if len(t) == 0:
        print("no trades"); return
    n = len(t)
    net = t.net.sum()
    win = (t.net > 0).mean()
    pl = -t.loc[t.net <= 0, "net"].sum()
    pf = t.loc[t.net > 0, "net"].sum() / pl if pl else float("inf")
    r = t.net / t.premium
    tstat = r.mean() / (r.std() / np.sqrt(n)) if r.std() else 0.0
    yrs = (pd.Timestamp(t.date.max()) - pd.Timestamp(t.date.min())).days / 365.25
    top = t.sort_values("net", ascending=False)
    print(f"  NET = Rs{net:,.0f}   simple ann = {net/500000/yrs*100:.1f}% on Rs5L")
    print(f"  win={win:.0%}  PF={pf:.2f}  t-stat={tstat:.2f}  avg=Rs{t.net.mean():,.0f}")
    print(f"  gross=Rs{t.gross.sum():,.0f}  costs=Rs{t.cost.sum():,.0f}")
    print("-" * 90)
    print("TAIL CHECK:")
    for lab, m in [("T3 winners", t.t3 == True), ("non-T3", t.t3 != True)]:
        s = t[m]
        print(f"  {lab:12s} n={len(s):3d}  net=Rs{s.net.sum():>10,.0f}")
    print(f"  net excl top-5 winners  = Rs{net - top.head(5).net.sum():,.0f}")
    print(f"  net excl top-10 winners = Rs{net - top.head(10).net.sum():,.0f}")
    print("-" * 90)
    print("Exit reasons:", dict(t["reason"].value_counts()))
    print("-" * 90)
    print("By year:")
    t2 = t.copy(); t2["yr"] = pd.to_datetime(t2["date"]).dt.year
    for y, s in t2.groupby("yr"):
        pf2 = s.loc[s.net > 0, "net"].sum() / max(1e-9, -s.loc[s.net <= 0, "net"].sum())
        print(f"  {y}: n={len(s):3d}  net=Rs{s.net.sum():>9,.0f}  win={ (s.net>0).mean():.0%}  PF={pf2:.2f}")
    print("=" * 90)


if __name__ == "__main__":
    main()
