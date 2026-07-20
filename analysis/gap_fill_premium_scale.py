"""Pre-study: derive the frozen gap-fill exit from 2020-2024 premium behavior.

For each in-band gap day in the TRAIN window, enter the ATM fade option at the
09:20 settled open, walk its premium to EOD, and record path-aware excursions
(Task 1). Freeze, per gap sub-bucket and pooled:
  STOP  = p75(MAE)                         # survives noise
  BOOK  = p40(MFE | candidate stop=STOP)   # reachable, not the peak
  TRAIL = 0.5 * BOOK
  FLOOR = smallest bucket whose median favorable premium excursion
          exceeds (SPREAD_ASSUMED + BOOK)
Run: PYTHONPATH=. python3 analysis/gap_fill_premium_scale.py
"""
from __future__ import annotations

from datetime import date, time

import numpy as np
import pandas as pd

from analysis.gap_fill_excursion import atm_strike, excursion_with_stop
from engine.data.catalog import Catalog
from engine.data.market import MarketData
from engine.data.spot import SpotSeries

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
TRAIN_START, TRAIN_END = date(2020, 1, 1), date(2024, 12, 31)
CAP = 0.005
SPREAD_ASSUMED = 3.0                    # assumed round-trip option spread, premium pts
BUCKETS = [0.0005, 0.0015, 0.003, 0.005]
BLAB = ["0.05-0.15", "0.15-0.30", "0.30-0.50"]
T0915, T0920, T1515 = time(9, 15), time(9, 20), time(15, 15)


def day_rows(spot, chain, day):
    """One in-band gap day -> (bucket, mfe, mae) or None (not in band / no data)."""
    bars = [t.to_pydatetime() for t in spot.timestamps()
            if t.to_pydatetime().date() == day]
    if len(bars) < 3:
        return None
    b0915 = next((b for b in bars if b.time() == T0915), None)
    if b0915 is None:
        return None
    pc = spot.close_before(b0915)
    if pc is None:
        return None
    settled = spot.bar_at(b0915).close          # 09:15 bar close ~= 09:20 price
    gap = settled / pc - 1.0
    if not (BUCKETS[0] <= abs(gap) <= CAP):
        return None
    side = "PUT" if gap > 0 else "CALL"         # fade
    entry_bar = next((b for b in bars if b.time() >= T0920), None)
    if entry_bar is None:
        return None
    snap = chain.snapshot_at(entry_bar)
    strikes = [s for s in snap.strikes()
               if (snap.call(s) if side == "CALL" else snap.put(s)).is_traded]
    if not strikes:
        return None
    strike = atm_strike(strikes, settled)
    # premium path from entry to EOD (sliced ONCE, no per-bar history).
    # Illiquid strikes can be untraded/absent from a given minute's snapshot
    # (confirmed: normal Data-v3 sparsity, not a data error -- the same strike
    # reappears a few minutes later); skip those bars rather than crash, mirroring
    # the engine's own missing-quote convention (Backtest._opt_quote / atm_strike
    # both treat "strike not in snap.strikes()" as skip-this-bar, not fatal).
    path = []
    for b in bars:
        if b.time() < T0920 or b.time() > T1515:
            continue
        q = chain.snapshot_at(b)
        if strike not in q.strikes():
            continue
        row = q.call(strike) if side == "CALL" else q.put(strike)
        path.append(row.close)
    if len(path) < 2:
        return None
    entry = path[0]
    absgap = abs(gap)
    bucket = BLAB[np.searchsorted(BUCKETS, absgap, side="right") - 1]
    return bucket, entry, path


def main():
    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    md = MarketData(cat, spot, min_dte=1)
    rows = []
    for _expiry, chains in md.stream(TRAIN_START, TRAIN_END):
        for day in sorted(chains):
            r = day_rows(spot, chains[day], day)
            if r is not None:
                rows.append(r)
    # first pass: STOP from pooled MAE (candidate stop = large so MFE untruncated here)
    maes = []
    for _bucket, entry, path in rows:
        _mfe, mae, _ = excursion_with_stop(path, entry, stop_pts=1e9)
        maes.append(mae)
    STOP = float(np.percentile(maes, 75))
    # second pass: MFE truncated at the frozen STOP
    per_bucket = {b: [] for b in BLAB}
    mfes = []
    for bucket, entry, path in rows:
        mfe, _mae, _ = excursion_with_stop(path, entry, stop_pts=STOP)
        per_bucket[bucket].append(mfe)
        mfes.append(mfe)
    BOOK = float(np.percentile(mfes, 40))
    TRAIL = 0.5 * BOOK
    # FLOOR: smallest bucket whose MEDIAN favorable excursion clears spread+book
    FLOOR = CAP
    for i, b in enumerate(BLAB):
        vals = per_bucket[b]
        if vals and float(np.median(vals)) > (SPREAD_ASSUMED + BOOK):
            FLOOR = BUCKETS[i]
            break
    print(f"gap-fill pre-study  train {TRAIN_START}..{TRAIN_END}  n={len(rows)}")
    print(f"  {'bucket%':<11}{'n':>5}{'medMFE':>9}{'medMAE?':>9}")
    for b in BLAB:
        v = per_bucket[b]
        med = float(np.median(v)) if v else float("nan")
        print(f"  {b:<11}{len(v):>5}{med:>9.2f}")
    print(f"FROZEN: STOP={STOP:.2f}  BOOK={BOOK:.2f}  TRAIL={TRAIL:.2f}  "
          f"FLOOR={FLOOR}  (SPREAD_ASSUMED={SPREAD_ASSUMED})")


if __name__ == "__main__":
    main()
