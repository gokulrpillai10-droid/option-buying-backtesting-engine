"""What happens AFTER a NIFTY gap-up / gap-down open — the intraday path.

Companion to nifty_gap_analysis.py (daily census). That showed WHETHER gaps
fill; this shows HOW the day unfolds from the open: the average trajectory,
how far it runs with vs against the gap, and whether the 09:30 checkpoint
("still driving or reclaimed?") predicts the rest of the session.

Data: /Users/gokul/Desktop/tradealgo/nifty_5min_5yr.csv (NIFTY 5-min, the
source behind gap.parquet), 2019-12 -> 2026-07-10.

Anchor = SETTLED open = close of the 09:15 bar (price at 09:20). This is the
first tradeable level and sidesteps the pre-open auction spikes that polluted
the daily-open tail (e.g. 2026-02-03). gap = anchor / prev_close - 1.
Intraday high/low measured from 09:20 on (the 09:15 bar excluded).

All returns are stated IN THE GAP'S DIRECTION: + = continued (down-gap fell
further / up-gap rose further), - = faded back toward the open.

Run: PYTHONPATH=<repo> python3 analysis/nifty_gap_path.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CSV = "/Users/gokul/Desktop/tradealgo/nifty_5min_5yr.csv"
FLAT = 0.0005
BUCKETS = [0.0005, 0.003, 0.005, 0.01, np.inf]
BLAB = ["0.05-0.30", "0.30-0.50", "0.50-1.0", ">1.0"]
CKPTS = ["09:30", "10:00", "11:00", "13:00", "14:30", "15:20"]


def build() -> pd.DataFrame:
    raw = pd.read_csv(CSV, parse_dates=["date"])
    raw["day"] = raw["date"].dt.normalize()
    raw["t"] = raw["date"].dt.strftime("%H:%M")
    # keep only real full sessions (data through at least 15:15)
    have_close = raw.groupby("day")["t"].max()
    full = have_close[have_close >= "15:15"].index
    raw = raw[raw["day"].isin(full)]

    first = raw.sort_values("date").groupby("day").first()
    last = raw.sort_values("date").groupby("day").last()
    intra = raw[raw["t"] != "09:15"]                 # drop the auction bar
    piv = raw.pivot_table(index="day", columns="t", values="close", aggfunc="last")

    d = pd.DataFrame(index=first.index)
    d["anchor"] = first["close"]                      # settled open (~09:20)
    d["dclose"] = last["close"]
    d["dhigh"] = intra.groupby("day")["high"].max()
    d["dlow"] = intra.groupby("day")["low"].min()
    d["pc"] = d["dclose"].shift(1)
    for c in CKPTS:
        d[c] = piv[c] if c in piv.columns else np.nan
    d = d.dropna(subset=["pc", "anchor"]).copy()

    d["gap"] = d["anchor"] / d["pc"] - 1.0
    d["sign"] = np.where(d["gap"] > 0, 1.0, -1.0)
    d["absgap"] = d["gap"].abs()
    d["year"] = d.index.year
    d["bucket"] = pd.cut(d["absgap"], BUCKETS, labels=BLAB, right=False)
    # returns from anchor, expressed in gap direction
    for c in CKPTS:
        d[f"r_{c}"] = (d[c] / d["anchor"] - 1.0) * d["sign"]
    # excursions from anchor
    d["mfe"] = np.where(d["sign"] > 0, d["dhigh"] / d["anchor"] - 1.0,
                        1.0 - d["dlow"] / d["anchor"])
    d["mae"] = np.where(d["sign"] > 0, 1.0 - d["dlow"] / d["anchor"],
                        d["dhigh"] / d["anchor"] - 1.0)
    d["fill"] = np.where(d["sign"] > 0, d["dlow"] <= d["pc"], d["dhigh"] >= d["pc"])
    # 09:30 checkpoint: still beyond the open in gap direction = "driving"
    d["drive0930"] = d["r_09:30"] > 0
    d["rest"] = (d["15:20"] / d["09:30"] - 1.0) * d["sign"]   # 09:30 -> late day, gap dir
    return d


def path_table(g: pd.DataFrame, name: str) -> None:
    print(f"\n== {name}: mean return from open IN GAP DIRECTION (+cont / -fade), n={len(g)} ==")
    hdr = "".join(f"{c:>9}" for c in CKPTS)
    print(f"  {'bucket%':<11}{'n':>5}{hdr}{'fill%':>8}")
    for lab in BLAB:
        b = g[g["bucket"] == lab]
        if b.empty:
            continue
        cells = "".join(f"{b[f'r_{c}'].mean()*100:>9.3f}" for c in CKPTS)
        print(f"  {lab:<11}{len(b):>5}{cells}{b['fill'].mean()*100:>7.0f}%")
    cells = "".join(f"{g[f'r_{c}'].mean()*100:>9.3f}" for c in CKPTS)
    print(f"  {'ALL':<11}{len(g):>5}{cells}{g['fill'].mean()*100:>7.0f}%")
    print(f"  excursion from open: mean run WITH gap {g['mfe'].mean()*100:.3f}%  "
          f"vs AGAINST {g['mae'].mean()*100:.3f}%")


def drive_signal(d: pd.DataFrame, side: pd.Series, name: str) -> None:
    g = d[side]
    drv, rec = g[g["drive0930"]], g[~g["drive0930"]]
    print(f"\n-- {name}: at 09:30, still driving vs reclaimed -> rest-of-day "
          f"(09:30->15:20, gap dir) --")
    print(f"   driving  {len(drv):>4} days ({len(drv)/len(g)*100:.0f}%)  "
          f"rest mean {drv['rest'].mean()*100:+.3f}%  win(cont) {(drv['rest']>0).mean()*100:.0f}%")
    print(f"   reclaim  {len(rec):>4} days ({len(rec)/len(g)*100:.0f}%)  "
          f"rest mean {rec['rest'].mean()*100:+.3f}%  win(cont) {(rec['rest']>0).mean()*100:.0f}%")


def main() -> None:
    d = build()
    print(f"NIFTY post-open path — {CSV.split('/')[-1]}")
    print(f"sessions {len(d)}   {d.index[0].date()} -> {d.index[-1].date()}")
    up, dn = d["gap"] > FLAT, d["gap"] < -FLAT
    path_table(d[up], "GAP UP")
    path_table(d[dn], "GAP DOWN")

    print("\n\n=== 09:30 CHECKPOINT SIGNAL (does the first 15 min predict the rest?) ===")
    drive_signal(d, up, "GAP UP")
    drive_signal(d, dn, "GAP DOWN")

    print("\n-- per-year: gap-down 'still driving at 09:30' -> rest-of-day mean% "
          "(the ADANIENT-style setup) --")
    gd = d[dn]
    for y, gy in gd.groupby("year"):
        drv = gy[gy["drive0930"]]
        if len(drv) < 3:
            print(f"   {y}: n={len(drv):>2} (thin)")
            continue
        print(f"   {y}: driving n={len(drv):>2}  rest {drv['rest'].mean()*100:+.3f}%  "
              f"cont% {(drv['rest']>0).mean()*100:.0f}%")


if __name__ == "__main__":
    main()
