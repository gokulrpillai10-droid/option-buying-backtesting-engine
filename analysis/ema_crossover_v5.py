"""9/21 EMA crossover (5-min) v5 -- user spec (9:40-2:58, symmetric +/-Rs2000).

  - 5-min EMA9/21 cross, reset daily. CONFIRMATION: 5-min candle closes above BOTH
    emas (CALL) / below BOTH (PUT). Fill next 1-min open. No look-ahead.
  - ENTRY WINDOW 09:40 -> 14:58. Square-off 14:58.
  - 1 lot = 65 qty.
  - Exit: P&L >= +Rs2,000 (+30.8 pts) or <= -Rs2,000 (-30.8 pts). Symmetric, no trail.
  - Max 3 trades/day (carried). No daily cap.
  - Full India F&O costs + 1% slippage/side.
"""
import numpy as np
import pandas as pd
from datetime import time

from engine.data.catalog import Catalog
from engine.execution.costs import CostModel

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
QTY, SLIP, MIN_DTE = 65, 0.01, 3
FAST, SLOW = 9, 21
EXIT_RS = 2000.0
EXIT_PTS = EXIT_RS / QTY                 # 30.77
ENTRY_START, ENTRY_LAST, TIME_EXIT = time(9, 40), time(14, 58), time(14, 58)
MAX_TRADES = 3
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


def simulate(aft, pfx, entry):
    tp, sl = entry + EXIT_PTS, entry - EXIT_PTS
    for dt, r in aft.iterrows():
        hi, lo, cl = float(r[pfx + "High"]), float(r[pfx + "Low"]), float(r[pfx + "Close"])
        if dt.time() >= TIME_EXIT:
            return cl, "eod", dt
        if lo <= sl:                       # adverse first (conservative)
            return sl, "stop", dt
        if hi >= tp:
            return tp, "target", dt
    return float(aft.iloc[-1][pfx + "Close"]), "close", aft.index[-1]


def run_day(day, fwd1):
    fwd5 = fwd1.resample("5min", label="right", closed="right").last().dropna()
    if len(fwd5) < 3:
        return []
    ema_f = fwd5.ewm(span=FAST, adjust=False).mean()
    ema_s = fwd5.ewm(span=SLOW, adjust=False).mean()
    diff = ema_f - ema_s
    sgn = np.sign(diff)
    b5, times1 = list(fwd5.index), list(fwd1.index)
    trades, busy = [], None
    for i in range(1, len(b5)):
        if len(trades) >= MAX_TRADES:
            break
        T = b5[i]
        if sgn.iloc[i] == 0 or sgn.iloc[i] == sgn.iloc[i - 1]:
            continue
        if busy is not None and T <= busy:
            continue
        c5, ef, es = float(fwd5.iloc[i]), float(ema_f.iloc[i]), float(ema_s.iloc[i])
        side = "CALL" if diff.iloc[i] > 0 else "PUT"
        if side == "CALL" and not (c5 > ef and c5 > es):
            continue
        if side == "PUT" and not (c5 < ef and c5 < es):
            continue
        later = [t for t in times1 if t > T]
        if not later:
            continue
        fill_dt = later[0]
        if not (ENTRY_START <= fill_dt.time() <= ENTRY_LAST):
            continue
        k = atm(day, T, c5, side)
        if k is None:
            continue
        of, pfx = opt_frame(day, k, side)
        aft = of[of.index >= fill_dt]
        if aft.empty or float(aft.iloc[0][pfx + "Open"]) <= 0:
            continue
        entry = float(aft.iloc[0][pfx + "Open"]) * (1 + SLIP)
        exit_lvl, reason, exit_dt = simulate(aft, pfx, entry)
        sell = exit_lvl * (1 - SLIP)
        net = (sell - entry) * QTY - CM.total(entry * QTY, sell * QTY, 2)
        trades.append(dict(side=side, strike=k, entry=entry, exit=sell, premium=entry * QTY,
                           gross=(sell - entry) * QTY,
                           cost=CM.total(entry * QTY, sell * QTY, 2), net=net, reason=reason,
                           hold_min=(exit_dt - fill_dt).total_seconds() / 60.0))
        busy = exit_dt
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
    t.to_csv("analysis/ema_v5_trades.csv", index=False)
    report(t, days, day_pnls)


def report(t, days, day_pnls):
    print("=" * 92)
    print("  9/21 EMA (5-min) v5  1 lot | symmetric +/-Rs2000 (+/-30.8pts) | entry 09:40-14:58")
    print(f"  max 3/day | candle-close confirm | {days} days, {len(t)} trades")
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
    print("-" * 92)
    print("Exit reasons:")
    print(t["reason"].value_counts().to_string())
    print("-" * 92)
    print(f"PER-DAY (traded days={len(dp)}): mean=Rs{dp.mean():,.0f}  median=Rs{dp.median():,.0f}  "
          f"win-days={ (dp>0).mean():.0%}  best=Rs{dp.max():,.0f}  worst=Rs{dp.min():,.0f}")
    print("-" * 92)
    print("By year (net):")
    t2 = t.copy(); t2["yr"] = pd.to_datetime(t2["date"]).dt.year
    for y, ss in t2.groupby("yr"):
        pf2 = ss.loc[ss.net > 0, "net"].sum() / max(1e-9, -ss.loc[ss.net <= 0, "net"].sum())
        print(f"  {y}: n={len(ss):4d}  net=Rs{ss.net.sum():>9,.0f}  PF={pf2:.2f}")
    print("=" * 92)


if __name__ == "__main__":
    main()
