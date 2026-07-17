"""Data-integrity scan: quarantine chain days a backtest should not trust.

Bad data silently becomes a "trade", so every day gets four checks; a day
failing any threshold lands in the quarantine file, which MarketData can
consume (quarantined days are skipped AND counted, never silently traded).

Checks (all vectorized per day; thresholds deliberately conservative so a
quarantine means something):
- intrinsic (QUARANTINES): a traded option closing below intrinsic against
  the chain's own FORWARD (options price off futures, not spot — in 2020 the
  futures basis ran hundreds of points below spot, so a spot-based check
  falsely flags most of the year), checked ONLY within +-500 pts of the
  forward — the band the engine actually trades. Far-ITM strikes routinely
  print 1-2% below intrinsic for real (European exercise + margined-futures
  reversal frictions; verified on 2025-05-21: every violation sat 1,400-4,300
  pts ITM), so flagging them would quarantine regimes, not bad data. Inside
  the liquid band, below F-K (calls) / K-F (puts) beyond
  ``strike * 0.0012 + 2`` is an arbitrage violation = bad data.
- ohlc (QUARANTINES): High < max(Open, Close) or Low > min(Open, Close).
- coverage (QUARANTINES): < 300 distinct chain minutes in a session (~375).
- ghost (ADVISORY only): a >50% 1-min premium jump that immediately retraces
  >33% with premium > 20. Reported per day but does NOT quarantine — in vol
  regimes (COVID) violent real prints are indistinguishable from bad ones,
  and quarantining a whole regime would bias every backtest that reads this.

Run: PYTHONPATH=<repo> python3 analysis/data_integrity.py [START_YEAR] [END_YEAR]
Writes analysis/quarantine_days.json; MarketData loads it via
``quarantine=load_quarantine(path)``.
"""
import json
import os
import sys
from datetime import date

import pandas as pd

from engine.data.catalog import Catalog
from engine.data.market import MarketData
from engine.data.spot import SpotSeries

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
OUT = os.path.join(os.path.dirname(__file__), "quarantine_days.json")

THRESHOLDS = {"intrinsic": 10, "ohlc": 10, "ghost": 5, "min_minutes": 300}


BAND = 500.0   # only strikes within +-BAND of the forward are integrity-checked


def intrinsic_violations(day_df: pd.DataFrame) -> int:
    """Rows where a TRADED option closes below intrinsic vs the chain's own
    FORWARD, beyond tolerance, within +-BAND pts of the forward — a true
    arbitrage violation in the liquid band the engine trades. (Far-ITM
    strikes legitimately print below intrinsic; see module docstring.)"""
    m = day_df[(day_df["StrikePrice"] - day_df["Forward"]).abs() <= BAND]
    if m.empty:
        return 0
    tol = m["StrikePrice"] * 0.0012 + 2.0
    call_intr = (m["Forward"] - m["StrikePrice"]).clip(lower=0.0)
    put_intr = (m["StrikePrice"] - m["Forward"]).clip(lower=0.0)
    bad_call = m["CallIsTradedStrike"] & (m["CallClose"] < call_intr - tol)
    bad_put = m["PutIsTradedStrike"] & (m["PutClose"] < put_intr - tol)
    return int(bad_call.sum() + bad_put.sum())


def ohlc_violations(day_df: pd.DataFrame) -> int:
    """Impossible bars on either option side."""
    n = 0
    for pfx in ("Call", "Put"):
        o, h = day_df[pfx + "Open"], day_df[pfx + "High"]
        l, c = day_df[pfx + "Low"], day_df[pfx + "Close"]
        n += int(((h < o.combine(c, max)) | (l > o.combine(c, min))).sum())
    return n


def ghost_prints(day_df: pd.DataFrame) -> int:
    """Per strike+side: a >50% 1-min close jump that retraces >33% on the very
    next bar, with premium > 20 — a bad print, not a market move."""
    n = 0
    for pfx in ("Call", "Put"):
        g = day_df.sort_values("DateTime").groupby("StrikePrice")[pfx + "Close"]
        prev, nxt = g.shift(1), g.shift(-1)
        cur = day_df.sort_values("DateTime")[pfx + "Close"]
        jump = (prev > 20) & ((cur / prev - 1).abs() > 0.5)
        revert = ((nxt / cur - 1).abs() > 0.33)
        n += int((jump & revert).sum())
    return n


def day_flags(day_df: pd.DataFrame) -> dict:
    """All counts for one day's chain frame."""
    return {"intrinsic": intrinsic_violations(day_df),
            "ohlc": ohlc_violations(day_df),
            "ghost": ghost_prints(day_df),
            "minutes": int(day_df["DateTime"].nunique())}


def verdict(flags: dict, thresholds: dict = THRESHOLDS) -> list:
    """Which QUARANTINING thresholds the day breaks ([] = clean).
    ghost is advisory — in the report, never a quarantine reason."""
    reasons = [k for k in ("intrinsic", "ohlc") if flags[k] >= thresholds[k]]
    if flags["minutes"] < thresholds["min_minutes"]:
        reasons.append("coverage")
    return reasons


def scan(start: date, end: date):
    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "1min")
    md = MarketData(cat, spot, min_dte=3)
    report, quarantined = [], []
    for _expiry, chains in md.stream(start, end):
        for day in sorted(chains):
            flags = day_flags(chains[day]._df)
            reasons = verdict(flags)
            report.append({"date": day.isoformat(), **flags, "reasons": reasons})
            if reasons:
                quarantined.append(day.isoformat())
                print(f"QUARANTINE {day}  {flags}  <- {reasons}")
    with open(OUT, "w") as f:
        json.dump({"thresholds": THRESHOLDS, "quarantined": sorted(quarantined),
                   "days": report}, f, indent=1)
    print(f"\nscanned {len(report)} days, quarantined {len(quarantined)} -> {OUT}")


if __name__ == "__main__":
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2020
    y1 = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    scan(date(y0, 1, 1), date(y1, 12, 31))
