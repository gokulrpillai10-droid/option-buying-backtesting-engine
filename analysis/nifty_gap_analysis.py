"""Census of every NIFTY gap day, 2019->present, from the Kite NSE daily feed.

Source: analysis/nifty_daily_kite.csv (NIFTY 50 index, instrument 256265,
pulled via kite historical `day` candles — the authoritative NSE open, not
Yahoo ^NSEI). gap = today.open / prev.close - 1.

Per session we classify direction (up/down) and magnitude bucket, then measure
how the day RESOLVED:
  - fill?      did price trade back to prev close intraday
               (gap-up: low <= pc; gap-down: high >= pc)
  - o2c        open->close move; "continued" = closed further in the gap's
               direction (up gap closes green / down gap closes red)
  - c2c        close vs prev close = the day's net move incl. the gap
Everything is reported PER YEAR as well, because a full-sample mean can hide a
single-regime effect (house rule from the ORB/gap studies).

Run: PYTHONPATH=<repo> python3 analysis/nifty_gap_analysis.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CSV = "analysis/nifty_daily_kite.csv"
FLAT = 0.0005                        # |gap| < 0.05% = effectively no gap
BUCKETS = [0.0005, 0.0015, 0.003, 0.005, 0.01, 0.02, np.inf]
BLAB = ["0.05-0.15", "0.15-0.30", "0.30-0.50", "0.50-1.0", "1.0-2.0", ">2.0"]


def load() -> pd.DataFrame:
    df = pd.read_csv(CSV, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    df["pc"] = df["close"].shift(1)
    df = df.dropna(subset=["pc"]).copy()
    df["gap"] = df["open"] / df["pc"] - 1.0
    df["o2c"] = df["close"] / df["open"] - 1.0
    df["c2c"] = df["close"] / df["pc"] - 1.0
    df["up"] = df["gap"] > 0
    # fill: price returns to prev close intraday
    df["fill"] = np.where(df["up"], df["low"] <= df["pc"], df["high"] >= df["pc"])
    # continued: closed further in the gap direction than the open
    df["cont"] = np.where(df["up"], df["o2c"] > 0, df["o2c"] < 0)
    df["year"] = df["date"].dt.year
    df["absgap"] = df["gap"].abs()
    df["bucket"] = pd.cut(df["absgap"], BUCKETS, labels=BLAB, right=False)
    return df


def side_table(g: pd.DataFrame, name: str) -> None:
    print(f"\n== {name}  (n={len(g)}) ==")
    print(f"  {'bucket%':<10}{'n':>5}{'fill%':>8}{'cont%':>8}"
          f"{'mean o2c%':>11}{'mean c2c%':>11}")
    for lab in BLAB:
        b = g[g["bucket"] == lab]
        if b.empty:
            continue
        print(f"  {lab:<10}{len(b):>5}{b['fill'].mean()*100:>7.0f}%"
              f"{b['cont'].mean()*100:>7.0f}%{b['o2c'].mean()*100:>11.3f}"
              f"{b['c2c'].mean()*100:>11.3f}")
    print(f"  {'ALL':<10}{len(g):>5}{g['fill'].mean()*100:>7.0f}%"
          f"{g['cont'].mean()*100:>7.0f}%{g['o2c'].mean()*100:>11.3f}"
          f"{g['c2c'].mean()*100:>11.3f}")


def main() -> None:
    df = load()
    live = df.iloc[-1]
    print(f"NIFTY gap census — source {CSV}")
    print(f"sessions {len(df)}   {df['date'].iloc[0].date()} -> "
          f"{df['date'].iloc[-1].date()}   (last row {live['date'].date()} "
          f"is TODAY = live/partial: behavior stats exclude it)")
    df = df.iloc[:-1] if live["date"].date().isoformat() == "2026-07-20" else df

    up, dn = df[df["gap"] > FLAT], df[df["gap"] < -FLAT]
    flat = df[df["absgap"] <= FLAT]
    print(f"\ngap-up   {len(up):>5}  ({len(up)/len(df)*100:.1f}%)")
    print(f"gap-down {len(dn):>5}  ({len(dn)/len(df)*100:.1f}%)")
    print(f"flat     {len(flat):>5}  ({len(flat)/len(df)*100:.1f}%)  |gap|<{FLAT*100:.2f}%")

    q = df["gap"].mul(100).quantile([.01, .05, .25, .5, .75, .95, .99])
    print("\ngap% distribution (signed):")
    for k, v in q.items():
        print(f"  p{int(k*100):<3} {v:+.3f}%")
    print(f"  biggest UP   {df['gap'].max()*100:+.2f}%   "
          f"biggest DOWN {df['gap'].min()*100:+.2f}%")

    side_table(up, "GAP UP")
    side_table(dn, "GAP DOWN")

    print("\n== per-year gap counts / mean gap ==")
    print(f"  {'yr':<6}{'ups':>5}{'dns':>5}{'up_fill%':>10}{'dn_fill%':>10}"
          f"{'up_cont%':>10}{'dn_cont%':>10}")
    for y, gy in df.groupby("year"):
        u, d = gy[gy["gap"] > FLAT], gy[gy["gap"] < -FLAT]
        print(f"  {y:<6}{len(u):>5}{len(d):>5}"
              f"{u['fill'].mean()*100:>9.0f}%{d['fill'].mean()*100:>9.0f}%"
              f"{u['cont'].mean()*100:>9.0f}%{d['cont'].mean()*100:>9.0f}%")

    print("\n== 8 biggest gap-ups ==")
    for _, r in df.nlargest(8, "gap").iterrows():
        print(f"  {r['date'].date()}  {r['gap']*100:+.2f}%  o2c {r['o2c']*100:+.2f}%"
              f"  {'FILLED' if r['fill'] else 'held '}  c2c {r['c2c']*100:+.2f}%")
    print("\n== 8 biggest gap-downs ==")
    for _, r in df.nsmallest(8, "gap").iterrows():
        print(f"  {r['date'].date()}  {r['gap']*100:+.2f}%  o2c {r['o2c']*100:+.2f}%"
              f"  {'FILLED' if r['fill'] else 'held '}  c2c {r['c2c']*100:+.2f}%")


if __name__ == "__main__":
    main()
