"""
iv_rv_intraday.py — Minute-by-minute IV/RV classifier for NIFTY option buying.

Methodology
-----------
RV  : Per-day 1-min log returns (starting 09:17) → rolling 30-bar and 45-bar
      trailing std, annualised by √(252 × 375).
      Rolling is computed per-day so the window never crosses midnight.
      First bar of each day has no previous bar → log_ret = NaN.
      First valid rv_30 fires at 09:47 (exactly 30 non-NaN bars: 09:18–09:47).
      First valid rv_45 fires at 10:02 (45 bars: 09:18–10:02).
      No look-ahead: all bars in window are past or current (observed before entry).

IV  : ATM strike of nearest weekly expiry (DTE ≥ 1 calendar day) at each minute.
      ATM = strike closest to Forward price. IV from parquet is BS annualised.

Ratio : IV(t) / RV_30(t)  and  IV(t) / RV_45(t).

Entry gate (for downstream strategies)
---------------------------------------
Observation window : 09:17–09:47  (rv_30 not yet valid; no entries)
Entry window       : 09:47–14:00  (first minute buy_flag fires → trade entry)

Output
------
analysis/iv_rv_intraday.parquet
  columns: DateTime, iv, rv_30, rv_45, iv_rv_30, iv_rv_45,
           forward, strike, dte_days, call_prem, put_prem, expiry
"""

import warnings, datetime as _dt
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from engine.data.catalog import Catalog

ROOT         = "/Users/gokul/Desktop/tradealgo/data nifty "
cat          = Catalog(ROOT)
OUT_PATH     = Path(__file__).parent / "iv_rv_intraday.parquet"

SESSION_START = _dt.time(9, 17)   # first bar included in rolling window
SESSION_END   = _dt.time(15, 15)
ENTRY_START   = _dt.time(9, 47)   # first valid rv_30 + start of entry window
MIN_DTE_DAYS  = 1                  # skip same-day expiry options
BARS_PER_DAY  = 375                # ~09:15–15:30 NSE session
ANN_FACTOR    = np.sqrt(252 * BARS_PER_DAY)

CHAIN_COLS = [
    "DateTime", "StrikePrice", "DaysToExpiry", "Forward", "IV",
    "CallOpen", "PutOpen",
]

# ── 1. Spot 1-min → per-day rolling RV ───────────────────────────────────────
print("Loading spot 1-min …")
spot_path = (Path(ROOT) / "NIFTY50_MERGED" / "Data-v3"
             / "instrument=NIFTY50" / "INDEX" / "index_data.parquet")
raw = pd.read_parquet(spot_path, columns=["DateTime", "Close"])
raw["DateTime"] = pd.to_datetime(raw["DateTime"])
raw = raw.sort_values("DateTime")

# Filter to session hours (09:17–15:15)
raw = raw[(raw["DateTime"].dt.time >= SESSION_START) &
          (raw["DateTime"].dt.time <= SESSION_END)].copy()

# Per-day rolling to prevent cross-day contamination
parts = []
for _, day in raw.groupby(raw["DateTime"].dt.date):
    d = day.sort_values("DateTime").copy()
    d["log_ret"] = np.log(d["Close"] / d["Close"].shift(1))
    # trailing window — current bar included; first bar NaN → rv_30 valid at bar 31
    d["rv_30"] = d["log_ret"].rolling(30, min_periods=30).std() * ANN_FACTOR
    d["rv_45"] = d["log_ret"].rolling(45, min_periods=45).std() * ANN_FACTOR
    parts.append(d[["DateTime", "Close", "rv_30", "rv_45"]])

spot_rv = pd.concat(parts, ignore_index=True)
trade_dates = sorted(spot_rv["DateTime"].dt.date.unique())
print(f"  {len(trade_dates)} trading days, "
      f"{len(spot_rv):,} bars  "
      f"({trade_dates[0]} → {trade_dates[-1]})")

# ── 2. date → nearest valid expiry map ───────────────────────────────────────
print("Building date → expiry map …")
expiry_strs = sorted(cat.expiries())                         # keep as str for path_for()
expiry_tsps  = [pd.Timestamp(e) for e in expiry_strs]       # timestamps for date comparison

date_to_exp: dict = {}
for d in trade_dates:
    cutoff = pd.Timestamp(d) + pd.Timedelta(days=MIN_DTE_DAYS)
    for exp_str, exp_ts in zip(expiry_strs, expiry_tsps):
        if exp_ts >= cutoff:
            date_to_exp[d] = exp_str   # string key so cat.path_for() works
            break

exp_to_dates: dict = defaultdict(list)
for d, exp in date_to_exp.items():
    exp_to_dates[exp].append(d)

print(f"  {len(date_to_exp)} dates mapped to {len(exp_to_dates)} unique expiries")

# ── 3. ATM IV per minute from options chain ───────────────────────────────────
print("Scanning options chain for per-minute ATM IV …")
atm_rows = []
n_exp = len(exp_to_dates)

for i, (exp, dates) in enumerate(sorted(exp_to_dates.items()), 1):
    if i % 50 == 0 or i == n_exp:
        print(f"  {i}/{n_exp} expiries processed …")
    try:
        df = pd.read_parquet(cat.path_for(exp), columns=CHAIN_COLS)
    except Exception as e:
        print(f"  SKIP {exp.date()}: {e}")
        continue

    df["DateTime"] = pd.to_datetime(df["DateTime"])
    dates_set = set(dates)

    df = df[df["DateTime"].dt.date.isin(dates_set)]
    df = df[(df["DateTime"].dt.time >= SESSION_START) &
            (df["DateTime"].dt.time <= SESSION_END)]
    df = df[df["DaysToExpiry"] > MIN_DTE_DAYS / 365]
    if df.empty:
        continue

    df = df.copy()
    df["atm_dist"] = (df["StrikePrice"] - df["Forward"]).abs()
    atm = df.loc[df.groupby("DateTime")["atm_dist"].idxmin()].copy()
    atm["expiry"] = exp
    atm["dte_days"] = (atm["DaysToExpiry"] * 365).round(1)
    atm_rows.append(
        atm[["DateTime", "IV", "Forward", "StrikePrice",
             "CallOpen", "PutOpen", "dte_days", "expiry"]]
        .rename(columns={"StrikePrice": "strike",
                         "CallOpen":    "call_prem",
                         "PutOpen":     "put_prem",
                         "IV":          "iv",
                         "Forward":     "forward"})
    )

iv_df = pd.concat(atm_rows, ignore_index=True)
print(f"  ATM rows: {len(iv_df):,}")

# ── 4. Merge spot RV + options IV ─────────────────────────────────────────────
print("Merging …")
merged = spot_rv.merge(iv_df, on="DateTime", how="inner")
merged = merged.dropna(subset=["rv_30", "iv"])
merged["iv_rv_30"] = merged["iv"] / merged["rv_30"]
merged["iv_rv_45"] = merged["iv"] / merged["rv_45"]
merged = merged.sort_values("DateTime").reset_index(drop=True)
print(f"  {len(merged):,} bars after merge  "
      f"({merged['DateTime'].min().date()} → {merged['DateTime'].max().date()})")

# ── 5. Analysis report ────────────────────────────────────────────────────────
IV_THR   = 0.20
IVRV_THR = 1.0

merged["_time"] = merged["DateTime"].dt.time
merged["_date"] = merged["DateTime"].dt.date
merged["_year"] = merged["DateTime"].dt.year

entry = merged[merged["_time"] >= ENTRY_START].copy()
N_bars = len(entry)
N_days = entry["_date"].nunique()

entry["buy_iv"]     = entry["iv"]       < IV_THR
entry["buy_ivrv30"] = entry["iv_rv_30"] < IVRV_THR
entry["buy_either"] = entry["buy_iv"] | entry["buy_ivrv30"]

print("\n" + "=" * 66)
print(f"ENTRY WINDOW (09:47–15:15)   {N_days} days   {N_bars:,} bars")
print("=" * 66)
print(f"  IV < 20%         : {entry['buy_iv'].sum():>8,}  ({entry['buy_iv'].mean()*100:>5.1f}%)")
print(f"  IV/RV_30 < 1.0   : {entry['buy_ivrv30'].sum():>8,}  ({entry['buy_ivrv30'].mean()*100:>5.1f}%)")
print(f"  Either           : {entry['buy_either'].sum():>8,}  ({entry['buy_either'].mean()*100:>5.1f}%)")

# Checkpoints
for label, t in [("09:47 (30-min)", _dt.time(9, 47)), ("10:02 (45-min)", _dt.time(10, 2))]:
    cp = merged[merged["_time"] == t]
    if cp.empty:
        continue
    print(f"\n  Checkpoint {label}  ({len(cp)} days):")
    print(f"    IV < 20%        : {(cp['iv']<IV_THR).sum():>4}  ({(cp['iv']<IV_THR).mean()*100:.1f}%)")
    print(f"    IV/RV_30 < 1.0  : {(cp['iv_rv_30']<IVRV_THR).sum():>4}  ({(cp['iv_rv_30']<IVRV_THR).mean()*100:.1f}%)")
    print(f"    Median IV       : {cp['iv'].median()*100:.1f}%")
    print(f"    Median IV/RV_30 : {cp['iv_rv_30'].median():.2f}x")

# Time-of-day distribution (30-min buckets)
print("\n  --- Cheap IV/RV by 30-min bucket (entry window) ---")
entry["_bucket"] = entry["DateTime"].dt.floor("30min").dt.time
bucket = (entry.groupby("_bucket")[["buy_iv", "buy_ivrv30"]]
          .mean().mul(100))
for t, row in bucket.iterrows():
    bar_iv   = "#" * int(row["buy_iv"]   / 2)
    bar_ivrv = "#" * int(row["buy_ivrv30"] / 2)
    print(f"    {str(t):<8}  IV<20%: {row['buy_iv']:>5.1f}% {bar_iv:<25}  "
          f"IV/RV<1: {row['buy_ivrv30']:>5.1f}% {bar_ivrv}")

# Year-by-year
print(f"\n  {'Yr':<5} {'Days':>5} {'Bars':>7} {'IV<20%':>8} {'IV/RV30<1':>10} "
      f"{'medIV':>7} {'medIV/RV30':>11}")
print("  " + "-" * 58)
for yr, g in entry.groupby("_year"):
    d   = g["_date"].nunique()
    b   = len(g)
    aiv = g["buy_iv"].mean() * 100
    arv = g["buy_ivrv30"].mean() * 100
    miv = g["iv"].median() * 100
    mrv = g["iv_rv_30"].median()
    print(f"  {yr:<5} {d:>5} {b:>7,}  {aiv:>6.1f}%   {arv:>7.1f}%   "
          f"{miv:>5.1f}%    {mrv:>7.2f}x")

# ── 6. Save parquet ───────────────────────────────────────────────────────────
out = merged.drop(columns=["_time", "_date", "_year", "Close"])
out.to_parquet(OUT_PATH, index=False)
print(f"\n→ Saved {len(out):,} rows → {OUT_PATH}")
print("→ Done.")
