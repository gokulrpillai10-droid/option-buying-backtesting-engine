"""Data-driven regime filter for the EMA v4 strategy.

Two deliverables:
  1. PROFILE green vs red days (and winning vs losing trades) using only features
     knowable by 10:00 -> "what do profit days have in common?"
  2. BUILD & TEST candidate regime filters by re-running v4 gated on them.

All regime features use data <= 10:00 and entries start at 10:00, so gating is
look-ahead-safe. Baseline strategy = v4 (1 lot, stop 20 / tgt 40 / trail, entry
10:00-15:00, dailyMaxLoss 2300, max 3/day, candle-close confirm).
"""
import numpy as np
import pandas as pd
from datetime import time

from engine.data.catalog import Catalog
from engine.execution.costs import CostModel

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
QTY, SLIP, MIN_DTE = 65, 0.01, 3
FAST, SLOW = 9, 21
STOP_PTS, TARGET_PTS = 20.0, 40.0
TRAIL_ACT_PTS, TRAIL_GAP = 35.0, 5.0
DAILY_MAX_LOSS, MAX_TRADES = 2300.0, 3
ENTRY_START, ENTRY_LAST, TIME_EXIT = time(10, 0), time(15, 0), time(15, 10)
REGIME_BY = time(10, 0)
RV_WINDOW = 10
CM = CostModel()


def realised_vol():
    d = pd.read_csv("analysis/daily_underlying.csv", parse_dates=["date"]).sort_values("date")
    ret = np.log(d["close"] / d["close"].shift(1))
    d["rv"] = (ret.rolling(RV_WINDOW).std() * np.sqrt(252)).shift(1)
    pc = d["close"].shift(1)
    return (dict(zip(d["date"].dt.date, d["rv"])),
            dict(zip(d["date"].dt.date, pc)),
            dict(zip(d["date"].dt.date, (d["close"] / pc - 1))))


def day_features(day, fwd1, rv_d, prev_close):
    """Regime fingerprint known by 10:00 (look-ahead-safe for entries >=10:00)."""
    early = fwd1[fwd1.index.time <= REGIME_BY]
    if len(early) < 20:
        return None
    p0, p_end = float(early.iloc[0]), float(early.iloc[-1])
    rng = float(early.max() - early.min())
    path = float(early.diff().abs().sum())
    eff = abs(p_end - p0) / path if path > 0 else 0.0    # Kaufman efficiency ratio
    # 5-min EMA crosses before 10:00 (chop proxy)
    f5 = early.resample("5min", label="right", closed="right").last().dropna()
    nx = 0
    if len(f5) >= 3:
        s = np.sign(f5.ewm(span=FAST, adjust=False).mean() - f5.ewm(span=SLOW, adjust=False).mean())
        nx = int((s.diff().fillna(0) != 0).sum())
    iv = np.nan
    ivbar = day[day["DateTime"].dt.time == time(9, 20)]
    if not ivbar.empty:
        iv = float(ivbar.iloc[(ivbar["StrikePrice"] - p0).abs().argmin()]["IV"])
    return dict(
        gap_pct=(p0 / prev_close - 1) if prev_close and not np.isnan(prev_close) else np.nan,
        or45_pct=rng / p0,
        mom45=(p_end - p0) / p0,
        abs_mom45=abs(p_end - p0) / p0,
        eff45=eff,
        ncross45=nx,
        iv=iv,
        iv_rv=(iv / rv_d) if (rv_d and not np.isnan(iv)) else np.nan,
        dow=fwd1.index[0].dayofweek,
    )


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
    stop_prem, peak, trailing = entry - STOP_PTS, entry, False
    for dt, r in aft.iterrows():
        hi, lo, cl = float(r[pfx + "High"]), float(r[pfx + "Low"]), float(r[pfx + "Close"])
        if dt.time() >= TIME_EXIT:
            return cl, dt
        peak = max(peak, hi)
        if not trailing and peak >= entry + TRAIL_ACT_PTS:
            trailing = True
        cur = stop_prem
        if trailing and peak - TRAIL_GAP > cur:
            cur = peak - TRAIL_GAP
        if lo <= cur:
            return cur, dt
    return float(aft.iloc[-1][pfx + "Close"]), aft.index[-1]


def run_day(day, fwd1, regime_ok, feats):
    fwd5 = fwd1.resample("5min", label="right", closed="right").last().dropna()
    if len(fwd5) < 3:
        return []
    ema_f = fwd5.ewm(span=FAST, adjust=False).mean()
    ema_s = fwd5.ewm(span=SLOW, adjust=False).mean()
    diff = ema_f - ema_s
    sgn = np.sign(diff)
    b5, times1 = list(fwd5.index), list(fwd1.index)
    trades, day_pnl, busy = [], 0.0, None
    for i in range(1, len(b5)):
        if len(trades) >= MAX_TRADES or day_pnl <= -DAILY_MAX_LOSS:
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
        if not regime_ok(side, feats):          # <-- the regime filter
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
        exit_lvl, exit_dt = simulate(aft, pfx, entry)
        sell = exit_lvl * (1 - SLIP)
        net = (sell - entry) * QTY - CM.total(entry * QTY, sell * QTY, 2)
        day_pnl += net
        trades.append(dict(side=side, net=net, entry_min=(fill_dt - times1[0]).total_seconds() / 60.0))
        busy = exit_dt
    return trades


# ---- candidate regime filters ----
def _absgap(f):
    return abs(f["gap_pct"]) if not np.isnan(f["gap_pct"]) else 0.0


FILTERS = {
    "baseline (no filter)": lambda side, f: True,
    "flat open: |gap| <= 0.3%": lambda side, f: _absgap(f) <= 0.003,
    "flat open: |gap| <= 0.5%": lambda side, f: _absgap(f) <= 0.005,
    "clean trend: eff45 >= 0.35": lambda side, f: f["eff45"] >= 0.35,
    "flat gap + clean trend": lambda side, f: _absgap(f) <= 0.005 and f["eff45"] >= 0.35,
    "flat gap + calm morning": lambda side, f: _absgap(f) <= 0.003 and f["ncross45"] <= 1,
    "flat gap + align early dir": lambda side, f: _absgap(f) <= 0.005 and
        ((f["mom45"] > 0) if side == "CALL" else (f["mom45"] < 0)),
}


def main():
    cat = Catalog(ROOT)
    exp_dates = [pd.Timestamp(e).date() for e in cat.expiries()]
    rv, pclose, _ = realised_vol()
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

    feat_rows, base_trades = [], []
    filt_net = {k: [] for k in FILTERS}      # per-filter list of per-day net
    filt_tr = {k: [0, 0, 0.0, 0.0] for k in FILTERS}  # n, wins, grosswin, grossloss
    filt_years = {k: {} for k in FILTERS}

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
            feats = day_features(day, fwd1, rv.get(d), pclose.get(d))
            if feats is None:
                continue
            yr = pd.Timestamp(d).year
            for name, fn in FILTERS.items():
                tr = run_day(day, fwd1, fn, feats)
                daynet = sum(x["net"] for x in tr)
                if tr:
                    filt_net[name].append(daynet)
                    filt_years[name].setdefault(yr, 0.0)
                    filt_years[name][yr] += daynet
                    filt_tr[name][0] += len(tr)
                    filt_tr[name][1] += sum(1 for x in tr if x["net"] > 0)
                    filt_tr[name][2] += sum(x["net"] for x in tr if x["net"] > 0)
                    filt_tr[name][3] += -sum(x["net"] for x in tr if x["net"] <= 0)
                if name == "baseline (no filter)":
                    fr = dict(feats); fr["date"] = d; fr["daynet"] = daynet; fr["ntr"] = len(tr)
                    feat_rows.append(fr)
                    for x in tr:
                        base_trades.append(dict(net=x["net"], side=x["side"],
                                                entry_min=x["entry_min"], **feats))

    F = pd.DataFrame(feat_rows)
    F.to_csv("analysis/regime_day_features.csv", index=False)
    profile(F, pd.DataFrame(base_trades))
    filter_results(filt_net, filt_tr, filt_years)


def profile(F, T):
    Ft = F[F.ntr > 0]
    g, r = Ft[Ft.daynet > 0], Ft[Ft.daynet <= 0]
    print("=" * 96)
    print(f"  GREEN vs RED DAY PROFILE  (baseline v4; traded days={len(Ft)}, green={len(g)}, red={len(r)})")
    print("=" * 96)
    feats = ["gap_pct", "or45_pct", "abs_mom45", "eff45", "ncross45", "iv", "iv_rv"]
    print(f"{'feature':<12}{'GREEN mean':>14}{'RED mean':>14}{'  delta':>12}")
    for c in feats:
        gm, rm = g[c].mean(), r[c].mean()
        print(f"{c:<12}{gm:>14.4f}{rm:>14.4f}{gm-rm:>12.4f}")
    print("-" * 96)
    # day-of-week green rate
    print("Green-day rate by weekday:", {int(k): round(v, 2)
          for k, v in (Ft.assign(w=Ft.dow).groupby("w").apply(lambda x: (x.daynet > 0).mean())).items()})
    print("-" * 96)
    print(f"  WINNING vs LOSING TRADES  (baseline; n={len(T)})")
    w, l = T[T.net > 0], T[T.net <= 0]
    for c in ["eff45", "abs_mom45", "or45_pct", "ncross45", "iv_rv", "entry_min"]:
        print(f"  {c:<11} win={w[c].mean():>9.4f}  loss={l[c].mean():>9.4f}")
    # does cross aligning with mom45 help win rate?
    T2 = T.copy()
    T2["aligned"] = np.where(T2.side == "CALL", T2.mom45 > 0, T2.mom45 < 0)
    print(f"  aligned trades win%={ (T2[T2.aligned].net>0).mean():.1%} (n={T2.aligned.sum()})  "
          f"vs counter-trend win%={ (T2[~T2.aligned].net>0).mean():.1%} (n={(~T2.aligned).sum()})")
    print("=" * 96)


def filter_results(filt_net, filt_tr, filt_years):
    print("  REGIME-FILTER BACKTESTS (each re-runs v4 gated on the filter)")
    print("=" * 96)
    print(f"{'filter':<40}{'trades':>8}{'win%':>7}{'net Rs':>13}{'PF':>7}{'grn-yrs':>9}")
    for name in filt_net:
        dp = filt_net[name]
        n, wins, gw, gl = filt_tr[name]
        if n == 0:
            print(f"{name:<40}{'no trades':>8}"); continue
        net = sum(dp)
        pf = gw / gl if gl else float("inf")
        winr = wins / n
        gy = sum(1 for y, v in filt_years[name].items() if v > 0)
        ty = len(filt_years[name])
        print(f"{name:<40}{n:>8}{winr:>7.0%}{net:>13,.0f}{pf:>7.2f}{gy:>6}/{ty}")
    print("=" * 96)
    print("(grn-yrs = profitable calendar years out of total)")


if __name__ == "__main__":
    main()
