"""9/21 EMA crossover (5-min) -- refined user spec, 3 lots.

Rules (see assumptions stated to user):
  - 5-min EMA9/21 cross, RESET daily. CONFIRMATION: the 5-min candle must close
    above BOTH emas (bullish->CALL) or below BOTH (bearish->PUT). Fill next 1-min open.
  - 3 lots = 195 qty.
  - Per-trade target +Rs7,020 (+36.0 premium pts). Per-trade stop -Rs2,340 (-12.0 pts).
  - Daily max loss Rs2,340: cumulative day P&L never allowed below -2,340 (so the
    effective per-trade loss room = min(2340, 2340 + day_pnl_so_far)).
  - Trailing: when premium reaches target-5 (=+31 pts), drop the hard target and
    trail the stop 5 pts below the running peak (let winners run past target).
  - Max 3 trades/day. Square-off 15:10. No look-ahead.
  - Full India F&O costs + 1% slippage/side.
"""
import numpy as np
import pandas as pd
from datetime import time

from engine.data.catalog import Catalog
from engine.execution.costs import CostModel

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
LOTS, LOT_SZ, SLIP, MIN_DTE = 3, 65, 0.01, 3
QTY = LOTS * LOT_SZ                      # 195
FAST, SLOW = 9, 21
TARGET_RS, STOP_RS, DAILY_MAX_LOSS = 7020.0, 2340.0, 2340.0
TARGET_PTS = TARGET_RS / QTY            # 36.0
TRAIL_ACT_PTS = TARGET_PTS - 5.0        # 31.0  activate trailing within 5 of target
TRAIL_GAP = 5.0
MAX_TRADES, TIME_EXIT = 3, time(15, 10)
CM = CostModel()


def opt_frame(day, strike, side):
    pfx = "Call" if side == "CALL" else "Put"
    s = day[day["StrikePrice"] == strike].set_index("DateTime").sort_index()
    return s[[pfx + "Open", pfx + "High", pfx + "Low", pfx + "Close"]], pfx


def atm(day, dt, fwd, side):
    snap = day[day["DateTime"] == dt]
    flag = "CallIsTradedStrike" if side == "CALL" else "PutIsTradedStrike"
    tr = snap[snap[flag]]
    if tr.empty:
        return None
    return int(tr.iloc[(tr["StrikePrice"] - fwd).abs().argmin()]["StrikePrice"])


def simulate_trade(aft, pfx, entry, allowed_loss):
    """Walk option bars after entry. Returns (exit_premium, reason, exit_dt)."""
    stop_prem = entry - allowed_loss / QTY      # daily/per-trade combined stop
    peak = entry
    trailing = False
    for dt, r in aft.iterrows():
        hi, lo, cl = float(r[pfx + "High"]), float(r[pfx + "Low"]), float(r[pfx + "Close"])
        if dt.time() >= TIME_EXIT:
            return cl, "eod", dt
        peak = max(peak, hi)
        if not trailing and peak >= entry + TRAIL_ACT_PTS:
            trailing = True
        cur_stop = stop_prem
        reason = "stop"
        if trailing:
            tstop = peak - TRAIL_GAP
            if tstop > cur_stop:
                cur_stop, reason = tstop, "trail"
        if lo <= cur_stop:
            return cur_stop, reason, dt
    last = aft.iloc[-1]
    return float(last[pfx + "Close"]), "close", aft.index[-1]


def run_day(day, fwd1):
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
    day_pnl = 0.0
    busy_until = None

    for i in range(1, len(b5)):
        if len(trades) >= MAX_TRADES or day_pnl <= -DAILY_MAX_LOSS:
            break
        T = b5[i]
        if sgn.iloc[i] == 0 or sgn.iloc[i] == sgn.iloc[i - 1]:
            continue
        if busy_until is not None and T <= busy_until:
            continue
        close5 = float(fwd5.iloc[i])
        ef, es = float(ema_f.iloc[i]), float(ema_s.iloc[i])
        if diff.iloc[i] > 0:
            side = "CALL"
            if not (close5 > ef and close5 > es):    # candle must close above both
                continue
        else:
            side = "PUT"
            if not (close5 < ef and close5 < es):     # candle must close below both
                continue

        later = [t for t in times1 if t > T]
        if not later:
            continue
        fill_dt = later[0]
        if fill_dt.time() >= TIME_EXIT:
            continue
        k = atm(day, T, close5, side)
        if k is None:
            continue
        of, pfx = opt_frame(day, k, side)
        aft = of[of.index >= fill_dt]
        if aft.empty or float(aft.iloc[0][pfx + "Open"]) <= 0:
            continue
        entry = float(aft.iloc[0][pfx + "Open"]) * (1 + SLIP)
        allowed_loss = min(STOP_RS, DAILY_MAX_LOSS + day_pnl)   # daily cap tightens it
        if allowed_loss <= 0:
            break
        exit_lvl, reason, exit_dt = simulate_trade(aft, pfx, entry, allowed_loss)
        sell = exit_lvl * (1 - SLIP)
        cost = CM.total(entry * QTY, sell * QTY, 2)
        net = (sell - entry) * QTY - cost
        day_pnl += net
        trades.append(dict(side=side, strike=k, entry=entry, exit=sell, premium=entry * QTY,
                           gross=(sell - entry) * QTY, cost=cost, net=net, reason=reason,
                           hold_min=(exit_dt - fill_dt).total_seconds() / 60.0))
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
    rows, days, day_pnls = [], 0, []
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
            if tr:
                day_pnls.append(sum(x["net"] for x in tr))

    t = pd.DataFrame(rows)
    t.to_csv("analysis/ema_v3_trades.csv", index=False)
    report(t, days, day_pnls)


def report(t, days, day_pnls):
    print("=" * 92)
    print(f"  9/21 EMA (5-min) v3  3 lots | tgt +7020 / stop -2340 / dailyMaxLoss 2340 / trail")
    print(f"  candle-close confirm | max 3 trades/day | {days} days, {len(t)} trades")
    print("=" * 92)
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
    dp = pd.Series(day_pnls)
    print(f"PER-TRADE: n={n}  trades/day={n/days:.2f}  avg hold={t.hold_min.mean():.0f}min  win={win:.0%}")
    print(f"RESULT: net=Rs{t.net.sum():,.0f}  avg=Rs{t.net.mean():,.0f}  PF={pf:.2f}  t-stat={tstat:.2f}")
    print(f"  gross=Rs{gross:,.0f}  costs=Rs{cost:,.0f} ({cost/abs(gross)*100 if gross else 0:.0f}% of gross)")
    print(f"  tail check: net excl top-10 winners = Rs{ex10:,.0f}")
    print(f"  median premium/trade = Rs{t.premium.median():,.0f}  (3 lots)")
    print("-" * 92)
    print("Exit reasons:")
    print(t["reason"].value_counts().to_string())
    print("-" * 92)
    print(f"PER-DAY (days that traded={len(dp)}): mean=Rs{dp.mean():,.0f}  median=Rs{dp.median():,.0f}  "
          f"win-days={ (dp>0).mean():.0%}")
    print(f"  best day=Rs{dp.max():,.0f}  worst day=Rs{dp.min():,.0f}  (daily cap -2340)")
    print("-" * 92)
    print("By year (net):")
    t2 = t.copy(); t2["yr"] = pd.to_datetime(t2["date"]).dt.year
    for y, ss in t2.groupby("yr"):
        print(f"  {y}: n={len(ss):4d}  net=Rs{ss.net.sum():>9,.0f}  PF="
              f"{ss.loc[ss.net>0,'net'].sum()/max(1e-9,-ss.loc[ss.net<=0,'net'].sum()):.2f}")
    print("=" * 92)


if __name__ == "__main__":
    main()
