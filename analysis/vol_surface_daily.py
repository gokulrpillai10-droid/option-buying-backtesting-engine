"""Daily vol-surface factor series for the second regime detector + a fast
forward-vol validation gate (the OI-triage lesson: check the cheap factors forecast
anything before building the composite + the breadth data-pull).

Walks Data-v3 for the front expiry (min-DTE guard so 0-DTE IV blow-ups stay out),
takes each day's EOD snapshot, and computes:
  * atm_iv  — at-the-money implied vol (surface level)
  * skew    — 25-delta risk reversal (put IV - call IV)  [fear/hedging]
  * vrp     — atm_iv / annualized realized vol            [rich vs cheap]
  * term    — atm_iv - India VIX(30d)                      [near-term vs 30d stress]
  * accel   — 5-day change in India VIX                    [vol-of-vol / acceleration]
Emits analysis/vol_surface_daily.csv (cached; re-analysis skips the ~15-min walk),
then reports whether each factor forecasts next-10d realized vol (Welch t on the
top-vs-bottom tercile). GO only if something clears |t|>=3.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import numpy as np
import pandas as pd

from analysis.chain_daily import front_weekly_eod_snapshot
from analysis.vol_surface_features import atm_iv, skew, annualized_realized_vol
from analysis.regime_accuracy import welch_t, forward_realized_vol

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
NIFTY_CSV = "analysis/nifty_daily.csv"
VIX_CSV = "analysis/india_vix.csv"
QUARANTINE_FILE = "analysis/quarantine_days.json"
OUT_CSV = "analysis/vol_surface_daily.csv"
START = date(2020, 2, 1)
DATA_END = date(2026, 2, 25)
MIN_DTE = 3                 # skip 0-2 DTE (IV explodes near expiry)
RV_WINDOW = 20
T_GATE = 3.0


def _sane_iv(x) -> bool:
    return isinstance(x, float) and 0.01 < x < 2.0 and not np.isnan(x)


def _evict(cache, day):
    for e in [e for e in cache if date.fromisoformat(e) < day]:
        del cache[e]


def build():
    from engine.data.catalog import Catalog
    from engine.data.market import MarketData, load_quarantine
    from engine.data.spot import SpotSeries

    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    q = load_quarantine(QUARANTINE_FILE)
    md = MarketData(cat, spot, min_dte=MIN_DTE, quarantine=q)

    closes = pd.read_csv(NIFTY_CSV, parse_dates=["date"], index_col="date")["close"]
    vix = pd.read_csv(VIX_CSV, parse_dates=["date"], index_col="date")["close"] / 100.0
    rvol = annualized_realized_vol(closes, RV_WINDOW)
    days = [d.date() for d in closes.index
            if START <= d.date() <= DATA_END and d.date() not in q]

    cache, rows = {}, []
    for D in days:
        _evict(cache, D)
        snap, E = front_weekly_eod_snapshot(D, md, cat, cache)
        if snap is None:
            continue
        fwd = float(closes.loc[pd.Timestamp(D)])
        aiv = atm_iv(snap, fwd)
        if not _sane_iv(aiv):
            continue
        sk = skew(snap)
        rows.append({"date": D, "atm_iv": aiv,
                     "skew": sk if _sane_iv(abs(sk) + 0.5) else np.nan})
    df = pd.DataFrame(rows).set_index("date")
    idx = pd.to_datetime(df.index)
    df["vrp"] = df["atm_iv"].values / rvol.reindex(idx).values
    df["term"] = df["atm_iv"].values - vix.reindex(idx).values
    df["accel"] = vix.diff(5).reindex(idx).values
    return df


def _load_or_build():
    if os.path.exists(OUT_CSV) and os.path.getsize(OUT_CSV) > 0:
        return pd.read_csv(OUT_CSV, parse_dates=["date"]).set_index("date")
    df = build()
    df.to_csv(OUT_CSV)
    return df


def main():
    df = _load_or_build()
    closes = pd.read_csv(NIFTY_CSV, parse_dates=["date"], index_col="date")["close"]
    fvol = forward_realized_vol(closes, 10)
    fvol.index = fvol.index.date
    f = fvol.reindex(df.index)

    print("VOL-SURFACE FACTORS — forward-vol predictive gate (next-10d realized vol)")
    print(f"n={len(df)} days {df.index.min()}..{df.index.max()}  (min_dte={MIN_DTE})")
    print(f"GATE: a factor earns further work only at |t| >= {T_GATE:.0f} on its "
          f"top-vs-bottom tercile of forward vol.\n")
    print(f"{'factor':<10}{'mean':>9}{'top-3ile fvol':>15}{'bot-3ile fvol':>15}{'t':>7}  verdict")
    any_edge = False
    for col in ["atm_iv", "skew", "vrp", "term", "accel"]:
        d = pd.DataFrame({"x": df[col], "f": f}).dropna()
        if len(d) < 30:
            print(f"{col:<10}  (too few points)"); continue
        lo, hi = d["x"].quantile(1 / 3), d["x"].quantile(2 / 3)
        top = d.loc[d["x"] >= hi, "f"]; bot = d.loc[d["x"] <= lo, "f"]
        t = welch_t(top, bot)
        edge = (not np.isnan(t)) and abs(t) >= T_GATE
        any_edge = any_edge or edge
        print(f"{col:<10}{d['x'].mean():>9.3f}{top.mean()*100:>14.2f}%{bot.mean()*100:>14.2f}%"
              f"{t:>7.1f}  {'** forecasts vol' if edge else 'noise (|t|<%.0f)' % T_GATE}")

    print(f"\nVERDICT: {'GO — a vol-surface factor forecasts vol; proceed to breadth + composite' if any_edge else 'NO-GO — no vol-surface factor clears the bar on Data-v3 alone'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
