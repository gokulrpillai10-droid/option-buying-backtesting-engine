"""G12-CE with CORRECTED swing/fib anchoring (per user clarification 2026-06-19).

Anchoring (the fix vs g12_ce_engine_rerun.py):
  - Track a running high; it adopts new highs UNTIL price falls >=80 pts from it.
  - On that 80-pt drop the swing HIGH LOCKS (fixed) and the fib is drawn high->low
    (0.0 = swing low, 1.0 = swing high; 0.22/0.44/0.77/1.0/1.22).
  - While locked & waiting for entry: the swing LOW only ratchets DOWN -- a new lower
    low re-plots the fib's 0.0 (high stays fixed) and re-arms the entry watch.
  - INVALIDATION (Q1=b): if price rallies back ABOVE the locked swing high before any
    entry, the down-move is dead -> unlock and resume tracking a fresh running high.
  - ENTRY (Q2): after price trades BELOW 0.44, the first 5-min candle that CLOSES above
    0.44 -> buy deep-ITM call (floor(spot/50)*50-150), window 10:00-12:15.
  - Exits: 0.22 stop / 0.77,1.00 targets / trail 12 prem-pts after 1.22 / EOD 15:15.
  - Post-trade (assumption): max 2 trades/day; 0.22 stop ends the day; trail-exit allows
    one re-entry on the same locked fib.

Runs on OUR Forward 5-min, prices from OUR Data-v3, our CostModel + slippage. Both
CORE (no F-filters) and FILTERED (F1/F2/F3/F6/F7/F8/F9) reported.
"""
import numpy as np
import pandas as pd
from datetime import time, date

from engine.data.catalog import Catalog
from engine.execution.costs import CostModel

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
LOTS = 3
MIN_DROP, DELTA, TRAIL_PREM = 80.0, 0.70, 12.0
SLIP, STOP_EXTRA = 1.5, 2.0
CM = CostModel()


def lot_size(d):
    return 65 if d >= date(2025, 12, 30) else 75


def calc_strike(entry):
    return (int(entry) // 50) * 50 - 150


def fib_levels(hi, lo):
    r = hi - lo
    return {"f000": lo, "f022": lo + 0.22 * r, "f044": lo + 0.44 * r,
            "f077": lo + 0.77 * r, "f100": hi, "f122": lo + 1.22 * r}


def five_min_bars(fwd1):
    g = fwd1.resample("5min", label="right", closed="right")
    return pd.DataFrame({"o": g.first(), "h": g.max(), "l": g.min(), "c": g.last()}).dropna()


def process_day(bars, filt=None):
    EA, EB, EOD = time(10, 0), time(12, 15), time(15, 15)
    trades = []
    locked = False
    swing_high, running_low, swing_low, fib = -np.inf, np.inf, None, None
    below_044 = False
    in_t, tfib, ep, et, sk = False, None, None, None, None
    t1 = t2 = t3 = trailing = False
    peak, tcount, sl_done = -np.inf, 0, False

    ts_list = list(bars.index)
    for idx in range(len(ts_list)):
        ts = ts_list[idx]
        o, h, l, c = (float(bars.iloc[idx]["o"]), float(bars.iloc[idx]["h"]),
                      float(bars.iloc[idx]["l"]), float(bars.iloc[idx]["c"]))
        tt = ts.time()

        if tt >= EOD:
            if in_t:
                trades.append((et, ts, sk, "EOD", t3))
            break

        # ---- in-trade management ----
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
                if l <= peak - TRAIL_PREM / DELTA:
                    trades.append((et, ts, sk, "Trail SL", t3))
                    in_t, tcount, below_044 = False, tcount + 1, False
                    continue
            else:
                if l <= f["f022"]:
                    trades.append((et, ts, sk, "Stop Loss", t3))
                    in_t, tcount, sl_done = False, tcount + 1, True
                    break
            continue

        # ---- tracking phase: find the locked swing high ----
        if not locked:
            if h > swing_high:
                swing_high, running_low = h, l        # adopt new high, measure drop from it
            else:
                running_low = min(running_low, l)
            if swing_high - running_low >= MIN_DROP:
                locked, swing_low = True, running_low
                fib, below_044 = fib_levels(swing_high, swing_low), False
            continue

        # ---- locked: waiting for entry ----
        if h > swing_high:                              # Q1=b: invalidate, resume tracking
            locked, swing_high, running_low = False, h, l
            fib, below_044 = None, False
            continue
        if l < swing_low:                               # ratchet the low DOWN, re-arm
            swing_low = l
            fib, below_044 = fib_levels(swing_high, swing_low), False
        if l < fib["f044"]:
            below_044 = True
        if sl_done or tcount >= 2 or not below_044:
            continue
        if tt < EA or tt >= EB:
            continue
        if c > fib["f044"]:
            if filt is not None:
                ph = filt["prev_high"]
                if np.isnan(ph) or c >= ph - 100 or swing_high > ph or c >= swing_high - 30:
                    continue
                rng = h - l
                cp = (c - l) / rng if rng > 0 else 0.0
                if cp < 0.80:
                    continue
            ep, et, sk = c, ts, calc_strike(c)
            tfib, in_t = dict(fib), True
            t1 = t2 = t3 = trailing = False
            peak = ep

    return trades


def call_close(day, ts, strike):
    row = day[(day["DateTime"] == ts) & (day["StrikePrice"] == strike)]
    if row.empty:
        return None
    v = float(row.iloc[0]["CallClose"])
    return v if v > 0 else None


def price_trades(day, raw, miss, d):
    out = []
    for et, xt, sk, reason, t3 in raw:
        ein, exo = call_close(day, et, sk), call_close(day, xt, sk)
        if ein is None or exo is None:
            miss[0] += 1
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
    ctx = {r["date"].date(): (r["prev_high"], r["prev_close"], r["open"]) for _, r in daily.iterrows()}
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
            ph, pc, op = ctx.get(d, (np.nan, np.nan, np.nan))
            if d.weekday() == 2 or (not np.isnan(pc) and (op - pc) >= 50):
                continue
            rows_filt += price_trades(day, process_day(bars, {"prev_high": ph}), miss, d)

    pd.DataFrame(rows_core).to_csv("analysis/g12_ce_corrected_core.csv", index=False)
    print(">>> CORRECTED ANCHORING -- CORE FIB (no F-filters)")
    report(pd.DataFrame(rows_core))
    print("\n>>> CORRECTED ANCHORING -- FILTERED (compare to their +Rs163k / 106 trades)")
    report(pd.DataFrame(rows_filt))


def report(t):
    if len(t) == 0:
        print("no trades"); return
    n = len(t); net = t.net.sum()
    win = (t.net > 0).mean()
    pl = -t.loc[t.net <= 0, "net"].sum()
    pf = t.loc[t.net > 0, "net"].sum() / pl if pl else float("inf")
    r = t.net / t.premium
    tstat = r.mean() / (r.std() / np.sqrt(n)) if r.std() else 0.0
    yrs = (pd.Timestamp(t.date.max()) - pd.Timestamp(t.date.min())).days / 365.25
    top = t.sort_values("net", ascending=False)
    print(f"  {n} trades | NET Rs{net:,.0f} | {net/500000/yrs*100:+.1f}% ann | win {win:.0%} | "
          f"PF {pf:.2f} | t {tstat:.2f}")
    print(f"  tail: T3 Rs{t.loc[t.t3==True,'net'].sum():,.0f} | non-T3 Rs{t.loc[t.t3!=True,'net'].sum():,.0f} | "
          f"excl top-10 Rs{net-top.head(10).net.sum():,.0f}")
    print("  by year:", {int(y): int(s.net.sum()) for y, s in t.assign(y=pd.to_datetime(t.date).dt.year).groupby("y")})


if __name__ == "__main__":
    main()
