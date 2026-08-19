"""OI predictive-power triage — the GATE before any OI subsystem.

The only question that matters (per the eng review + outside voice): does a daily
open-interest feature predict the thing that actually pays — next-session ATM
SHORT-straddle P&L — INCREMENTAL to just being always-short? The committed verdict
is that regime *timing* is NO-GO because short-vol premium dominates regardless of
regime; so OI must beat "always short vol", not merely correlate with a label.

Construction (look-ahead safe, settled-OI, quarantine-honored):
  * FEATURE at EOD(D) from the FRONT-WEEKLY contract (min_dte=0, keep the pin
    window — max-pain/walls are most informative in the last days).
  * TARGET = short ATM straddle entered EOD(D) on the overnight contract
    (min_dte=1, must survive to D+1), covered EOD(D+1). Per-unit premium points:
    short_pnl = (call+put)_D - (call+put)_{D+1}  (positive = premium decayed).
  * Baseline = unconditional mean short_pnl ("always short vol"). A feature earns
    its place only if a conditional subset beats that baseline with a consistent
    sign. Gross (no costs) — costs shift the baseline uniformly, so the INCREMENT
    is what we test.

Reuses: chain_daily (EOD snapshots), oi_features (pure features),
regime_straddle_pnl.atm_strike (both-legs-traded ATM), MarketData quarantine.
"""
from __future__ import annotations

import sys
from datetime import date

import numpy as np
import pandas as pd

from analysis.chain_daily import front_weekly_eod_snapshot
from analysis.oi_features import pcr, max_pain, oi_walls, classify_buildup
from analysis.regime_straddle_pnl import atm_strike

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
NIFTY_CSV = "analysis/nifty_daily.csv"
QUARANTINE_FILE = "analysis/quarantine_days.json"
START = date(2020, 2, 1)
DATA_END = date(2026, 2, 25)          # option chain ends here
BAND = 5                              # ATM +/- BAND strikes for near-money PCR
STEP = 50
ROWS_CSV = "analysis/oi_pnl_triage_rows.csv"   # cached feature/pnl rows (skip the walk)
T_GATE = 3.0                          # |t| bar for a real edge (~11 comparisons -> strict)


def _total_oi(snap):
    c = sum(snap.call(k).oi for k in snap.strikes())
    p = sum(snap.put(k).oi for k in snap.strikes())
    return float(c), float(p)


def _short_straddle_next_day(snap_d, snap_d1, ref):
    """Per-unit short ATM straddle P&L (premium points) from EOD(D) to EOD(D+1) on
    the same (overnight) contract, or None if not priceable."""
    k = atm_strike(snap_d, ref)
    if k is None or k not in snap_d1.strikes():
        return None
    cD, pD = snap_d.call(k), snap_d.put(k)
    cD1, pD1 = snap_d1.call(k), snap_d1.put(k)
    if not (cD.is_traded and pD.is_traded and cD1.is_traded and pD1.is_traded):
        return None
    return (cD.close + pD.close) - (cD1.close + pD1.close)   # short profits as premium falls


def _evict(cache, day):
    for e in [e for e in cache if date.fromisoformat(e) < day]:
        del cache[e]


def build_rows():
    from engine.data.catalog import Catalog
    from engine.data.market import MarketData, load_quarantine
    from engine.data.spot import SpotSeries

    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    q = load_quarantine(QUARANTINE_FILE)
    feat_md = MarketData(cat, spot, min_dte=0, quarantine=q)    # front weekly, pin window
    tgt_md = MarketData(cat, spot, min_dte=1, quarantine=q)     # overnight-survivable

    closes = pd.read_csv(NIFTY_CSV, parse_dates=["date"], index_col="date")["close"]
    days = [d.date() for d in closes.index
            if START <= d.date() <= DATA_END and d.date() not in q]

    feat_cache, tgt_cache = {}, {}
    prev = {}                                     # expiry_iso -> (day, call_oi, put_oi, spot, pcr)
    rows, skipped = [], 0
    for i, D in enumerate(days[:-1]):
        D1 = days[i + 1]
        _evict(feat_cache, D); _evict(tgt_cache, D)
        snap_f, E_f = front_weekly_eod_snapshot(D, feat_md, cat, feat_cache)
        if snap_f is None:
            skipped += 1; continue
        s = float(closes.loc[pd.Timestamp(D)])

        # --- features at D (front-weekly / pin window) ---
        atm = round(s / STEP) * STEP
        band = [atm + j * STEP for j in range(-BAND, BAND + 1)]
        pcr_all = pcr(snap_f)
        pcr_band = pcr(snap_f, strikes=band)
        mp = max_pain(snap_f)
        mp_dist = (mp - s) / s if mp else np.nan
        cw, pw = oi_walls(snap_f)
        cw_dist = (cw - s) / s if cw else np.nan
        pw_dist = (pw - s) / s if pw else np.nan
        call_oi, put_oi = _total_oi(snap_f)

        # --- change features vs prior day on the SAME contract (else NaN: roll/gap) ---
        d_pcr, buildup = np.nan, "flat"
        p = prev.get(E_f.isoformat())
        if p is not None and p[0] == days[i - 1]:          # contiguous prior trading day
            d_call, d_put = call_oi - p[1], put_oi - p[2]
            price_ret = s / p[3] - 1.0
            buildup = classify_buildup(d_call, d_put, price_ret)
            d_pcr = pcr_all - p[4]
        prev[E_f.isoformat()] = (D, call_oi, put_oi, s, pcr_all)

        # --- target: next-day short straddle (overnight contract) ---
        snap_t, E_t = front_weekly_eod_snapshot(D, tgt_md, cat, tgt_cache)
        snap_t1, E_t1 = front_weekly_eod_snapshot(D1, tgt_md, cat, tgt_cache)
        pnl = None
        if snap_t is not None and snap_t1 is not None and E_t == E_t1:
            pnl = _short_straddle_next_day(snap_t, snap_t1, s)
        if pnl is None:
            skipped += 1; continue

        rows.append({"date": D, "pnl": pnl, "pcr_all": pcr_all, "pcr_band": pcr_band,
                     "max_pain_dist": mp_dist, "call_wall_dist": cw_dist,
                     "put_wall_dist": pw_dist, "d_pcr": d_pcr, "buildup": buildup})
    return pd.DataFrame(rows), skipped, tgt_md.quarantined_between(START, DATA_END)


# ------------------------------------------------------------------ #
# analysis (pure, testable)
# ------------------------------------------------------------------ #
def tercile_spread(feat: pd.Series, pnl: pd.Series):
    """(top-tercile mean pnl, bottom-tercile mean pnl, spread) by feature value.
    NaN-safe; returns (nan,nan,nan) if too few valid points."""
    d = pd.DataFrame({"f": feat, "p": pnl}).dropna()
    if len(d) < 9:
        return np.nan, np.nan, np.nan
    lo, hi = d["f"].quantile(1 / 3), d["f"].quantile(2 / 3)
    top = d.loc[d["f"] >= hi, "p"].mean()
    bot = d.loc[d["f"] <= lo, "p"].mean()
    return top, bot, top - bot


def corr(feat: pd.Series, pnl: pd.Series) -> float:
    d = pd.DataFrame({"f": feat, "p": pnl}).dropna()
    if len(d) < 3 or d["f"].std() == 0:
        return np.nan
    return float(np.corrcoef(d["f"], d["p"])[0, 1])


def welch_t(a, b) -> float:
    """Welch two-sample t-stat of mean(a)-mean(b). NaN-safe; the honest
    significance test the crude 'spread > threshold' gate lacked."""
    a = np.asarray(a, float); a = a[~np.isnan(a)]
    b = np.asarray(b, float); b = b[~np.isnan(b)]
    if len(a) < 2 or len(b) < 2:
        return np.nan
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return float((a.mean() - b.mean()) / se) if se > 0 else np.nan


def _load_or_build():
    import os
    if os.path.exists(ROWS_CSV) and os.path.getsize(ROWS_CSV) > 0:
        df = pd.read_csv(ROWS_CSV, parse_dates=["date"])
        return df, "cached", "cached"
    df, skipped, quar = build_rows()
    df.to_csv(ROWS_CSV, index=False)          # cache so re-analysis skips the 16-min walk
    return df, skipped, quar


def main():
    df, skipped, quar = _load_or_build()
    n = len(df)
    if n == 0:
        print("OI TRIAGE: no priceable rows — check data window/paths.")
        return 1
    base = df["pnl"].mean()
    base_t = base / (df["pnl"].std(ddof=1) / np.sqrt(n)) if n > 1 and df["pnl"].std() else 0.0
    winrate = (df["pnl"] > 0).mean()

    print("OI PREDICTIVE-POWER TRIAGE — daily OI feature vs next-day SHORT-straddle "
          "P&L (premium pts)")
    print(f"window {START}..{DATA_END}  |  n={n} priced days, {skipped} skipped, "
          f"{quar} quarantined")
    print(f"BASELINE (always-short, unconditional): mean={base:+.2f} pts  "
          f"win%={winrate:.0%}  t={base_t:+.1f}  "
          f"(the edge OI must BEAT; note it is itself only marginally significant)")
    print(f"GATE: a feature earns a GO only at |t| >= {T_GATE:.0f} on the top-vs-bottom "
          f"tercile (or class-vs-rest) spread — ~11 comparisons, so a strict bar.\n")

    print(f"{'feature':<16}{'corr':>7}{'top-3ile':>10}{'bot-3ile':>10}{'spread':>9}"
          f"{'t':>7}  verdict")
    cont = ["pcr_all", "pcr_band", "max_pain_dist", "call_wall_dist",
            "put_wall_dist", "d_pcr"]
    any_edge = False
    for f in cont:
        c = corr(df[f], df["pnl"])
        top, bot, spread = tercile_spread(df[f], df["pnl"])
        d = df[[f, "pnl"]].dropna()
        lo, hi = d[f].quantile(1 / 3), d[f].quantile(2 / 3)
        t = welch_t(d.loc[d[f] >= hi, "pnl"], d.loc[d[f] <= lo, "pnl"])
        beat = (not np.isnan(t)) and abs(t) >= T_GATE
        any_edge = any_edge or beat
        print(f"{f:<16}{c:>7.2f}{top:>10.1f}{bot:>10.1f}{spread:>9.1f}{t:>7.1f}  "
              f"{'** significant' if beat else 'noise (|t|<%.0f)' % T_GATE}")

    print("\nBUILDUP class (mean next-day short P&L; t = class vs the rest):")
    for cls, g in df.groupby("buildup"):
        rest = df.loc[df["buildup"] != cls, "pnl"]
        t = welch_t(g["pnl"], rest)
        flag = "  ** significant" if (not np.isnan(t)) and abs(t) >= T_GATE else ""
        any_edge = any_edge or bool(flag)
        print(f"  {cls:<16} n={len(g):>4}  mean={g['pnl'].mean():+.2f}  "
              f"(t vs rest {t:+.1f}){flag}")

    print(f"\nVERDICT: {'GO — an OI feature clears |t|>=%.0f; worth building the subsystem' % T_GATE if any_edge else 'NO-GO — no OI feature clears the significance bar; do NOT build the subsystem. Linear power ~0; tercile/class spreads are within noise.'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
