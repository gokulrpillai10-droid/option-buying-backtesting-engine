"""
IV, IV/RV, and Greeks study — option buying suitability on NIFTY.

Methodology:
  - Expiry window : nearest expiry with DTE in [7, 35] calendar days, closest to 14 days
  - Time          : 09:20 settled open (or 09:25 fallback)
  - Strike        : ATM = closest strike to the Forward price in the parquet
  - IV            : annualised implied vol of the ATM strike
  - RV            : 20-day rolling annualised realised vol from spot daily closes
  - Greeks pulled : Delta (absolute), Theta (Rs/day per lot), Vega (Rs per 1% IV move),
                    Gamma (Delta change per 1-pt move)
  - Derived       : IV/RV ratio, Theta/Premium% (daily bleed rate), Vega/Theta ratio
"""

import warnings, sys, datetime as _dt
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/gokul/Desktop/buildstrategies")

import numpy as np
import pandas as pd
from pathlib import Path
from engine.data.catalog import Catalog
from engine.instruments import lot_size_for

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
cat  = Catalog(ROOT)

# ── 1. Daily spot close → 20-day rolling RV ──────────────────────────────────
print("Loading spot …")
spot_path = (Path(ROOT) / "NIFTY50_MERGED" / "Data-v3"
             / "instrument=NIFTY50" / "INDEX" / "index_data.parquet")
spot_raw  = pd.read_parquet(spot_path)
spot_raw["DateTime"] = pd.to_datetime(spot_raw["DateTime"])
spot_raw  = spot_raw.sort_values("DateTime")

spot_daily = (spot_raw.groupby(spot_raw["DateTime"].dt.date)["Close"]
              .last().rename("close"))
spot_daily.index = pd.to_datetime(spot_daily.index)
log_ret = np.log(spot_daily / spot_daily.shift(1))
rv_20   = (log_ret.rolling(20).std() * np.sqrt(252)).rename("rv")
rv_df   = rv_20.reset_index(); rv_df.columns = ["date", "rv"]
print(f"  Spot: {spot_daily.index[0].date()} → {spot_daily.index[-1].date()}")

# ── 2. Pull ATM strike Greeks + IV from 7–35 DTE expiry at 09:20 ─────────────
print("Scanning chains for ATM strike Greeks …")

COLS = ["DateTime", "StrikePrice", "DaysToExpiry", "Forward", "IV",
        "Gamma", "Vega", "CallOpen", "CallDelta", "CallTheta",
        "PutDelta", "PutTheta", "CallIsTradedStrike"]
DTE_LO, DTE_HI = 7/365, 35/365
T920, T925 = _dt.time(9, 20), _dt.time(9, 25)

records = []
for exp in cat.expiries():
    df = pd.read_parquet(cat.path_for(exp), columns=COLS)
    df["DateTime"] = pd.to_datetime(df["DateTime"])
    df = df[(df["DaysToExpiry"] >= DTE_LO) & (df["DaysToExpiry"] <= DTE_HI)]
    if df.empty:
        continue
    df["date"] = df["DateTime"].dt.date

    for t in [T920, T925]:
        df_t = df[df["DateTime"].dt.time == t]
        if not df_t.empty:
            break
    else:
        df_t = df.groupby(["date","StrikePrice"]).first().reset_index()

    # For each date, find the ATM strike = closest to Forward
    for d, grp in df_t.groupby("date"):
        if grp.empty:
            continue
        fwd = grp["Forward"].iloc[0]
        # pick strike closest to forward
        idx = (grp["StrikePrice"] - fwd).abs().idxmin()
        atm = grp.loc[idx]
        lot = lot_size_for(d if isinstance(d, _dt.date)
                           else pd.Timestamp(d).date())
        prem = float(atm["CallOpen"]) if float(atm["CallOpen"]) > 0 else np.nan

        records.append({
            "date"       : d,
            "expiry"     : exp,
            "dte_days"   : round(float(atm["DaysToExpiry"]) * 365, 1),
            "forward"    : float(fwd),
            "strike"     : int(atm["StrikePrice"]),
            "iv"         : float(atm["IV"]),
            "gamma"      : float(atm["Gamma"]),
            "vega"       : float(atm["Vega"]),              # Rs per 1 vol-pt (annualised)
            "delta"      : abs(float(atm["CallDelta"])),    # absolute delta
            "theta_day"  : abs(float(atm["CallTheta"])) * lot,  # Rs per day per 1 lot
            "premium"    : prem * lot if prem else np.nan,  # Rs per lot
            "lot"        : lot,
        })

iv_df = pd.DataFrame(records)
iv_df["date"] = pd.to_datetime(iv_df["date"])
# Keep expiry closest to 14-day DTE per date
iv_df["dte_err"] = (iv_df["dte_days"] - 14).abs()
atm = (iv_df.sort_values("dte_err").groupby("date").first().reset_index())
print(f"  Trading days: {len(atm)}")

# Derived Greeks metrics
atm["vega_1pct"]      = atm["vega"] * 0.01 * atm["lot"]  # Rs gain per 1% IV rise per lot
atm["theta_bleed_pct"]= atm["theta_day"] / atm["premium"] * 100  # daily bleed as % of premium
atm["vega_theta"]     = atm["vega_1pct"] / atm["theta_day"]       # vega/theta ratio

# ── 3. Merge with RV ──────────────────────────────────────────────────────────
merged = atm.merge(rv_df, on="date").dropna(subset=["rv"])
merged["iv_rv"]  = merged["iv"] / merged["rv"]
merged["year"]   = merged["date"].dt.year
N = len(merged)
print(f"  Days with IV + RV + Greeks: {N}")
print(f"  Date range: {merged['date'].min().date()} → {merged['date'].max().date()}\n")

# ── 4. Filter counts ──────────────────────────────────────────────────────────
IV_CHEAP   = 0.20   # < 20% = cheap absolute
IVRV_CHEAP = 1.0    # < 1.0 = options cheaper than recent moves

merged["pass_iv"]   = merged["iv"]    < IV_CHEAP
merged["pass_ivrv"] = merged["iv_rv"] < IVRV_CHEAP
merged["pass_both"] = merged["pass_iv"] & merged["pass_ivrv"]

def row(label, mask):
    n = mask.sum()
    return f"  {label:<45} {n:>5}  ({n/N*100:>5.1f}%)"

print("=" * 62)
print(f"TOTAL TRADING DAYS : {N}")
print("=" * 62)
print(row(f"IV < {IV_CHEAP:.0%}  (cheap absolute — good to buy)", merged["pass_iv"]))
print(row(f"IV/RV < {IVRV_CHEAP:.1f}  (cheap relative — priced below RV)", merged["pass_ivrv"]))
print(row("Both IV + IV/RV cheap", merged["pass_both"]))
print(row("Neither (expensive both ways — avoid buying)", ~merged["pass_iv"] & ~merged["pass_ivrv"]))

print("\n  --- IV threshold sweep ---")
for thr in [0.12, 0.15, 0.18, 0.20, 0.25, 0.30]:
    n = (merged["iv"] < thr).sum()
    print(f"    IV < {thr:.0%} : {n:>5} days  ({n/N*100:.1f}%)")

# ── 5. Year-by-year ───────────────────────────────────────────────────────────
print("\n" + "=" * 80)
print(f"{'Yr':<5}{'Days':>5} {'IV<20%':>8} {'IV<30%':>8} "
      f"{'IV/RV<1':>9} {'Both':>6}  {'med IV':>7} {'med IV/RV':>10}")
print("-" * 80)
for yr, g in merged.groupby("year"):
    n   = len(g)
    a   = (g["iv"] < IV_CHEAP).sum()
    a30 = (g["iv"] < 0.30).sum()
    b   = g["pass_ivrv"].sum()
    ab  = g["pass_both"].sum()
    print(f"{yr:<5}{n:>5} {a:>6} {a/n*100:>4.0f}%  "
          f"{a30:>5} {a30/n*100:>4.0f}%  "
          f"{b:>6} {b/n*100:>4.0f}%  "
          f"{ab:>4} {ab/n*100:>3.0f}%  "
          f"{g['iv'].median()*100:>6.1f}%  "
          f"{g['iv_rv'].median():>7.2f}x")

# ── 6. Greeks distributions ───────────────────────────────────────────────────
print("\n" + "=" * 62)
print("ATM GREEKS — MEDIANS (1 lot, 14-day expiry, at 09:20)")
print("=" * 62)
print(f"  IV (annualised)          : {merged['iv'].median()*100:.1f}%  "
      f"  [p10={merged['iv'].quantile(.1)*100:.1f}%  p90={merged['iv'].quantile(.9)*100:.1f}%]")
print(f"  IV/RV ratio              : {merged['iv_rv'].median():.2f}x  "
      f"  [p10={merged['iv_rv'].quantile(.1):.2f}x  p90={merged['iv_rv'].quantile(.9):.2f}x]")
print(f"  Delta (ATM call)         : {merged['delta'].median():.3f}  "
      f"  [p10={merged['delta'].quantile(.1):.3f}  p90={merged['delta'].quantile(.9):.3f}]")
print(f"  Gamma (Δ per 1pt move)   : {merged['gamma'].median():.6f}")
print(f"  Theta Rs/day (1 lot)     : Rs {merged['theta_day'].median():.0f}  "
      f"  [p10=Rs {merged['theta_day'].quantile(.1):.0f}  p90=Rs {merged['theta_day'].quantile(.9):.0f}]")
print(f"  Premium Rs (1 lot)       : Rs {merged['premium'].median():.0f}  "
      f"  [p10=Rs {merged['premium'].quantile(.1):.0f}  p90=Rs {merged['premium'].quantile(.9):.0f}]")
print(f"  Theta bleed %/day        : {merged['theta_bleed_pct'].median():.2f}%/day of premium")
print(f"  Vega Rs per 1% IV rise   : Rs {merged['vega_1pct'].median():.0f}  (1 lot)")
print(f"  Vega/Theta ratio         : {merged['vega_theta'].median():.2f}x  "
      f"  (>1 = IV expansion more powerful than daily bleed)")

# ── 7. Greeks by IV regime ────────────────────────────────────────────────────
print("\n" + "=" * 62)
print("GREEKS BY IV REGIME (cheap vs expensive)")
print("=" * 62)
cheap = merged[merged["pass_iv"]]
expen = merged[~merged["pass_iv"]]
print(f"{'Metric':<28} {'Cheap IV<20%':>14} {'Expensive IV≥20%':>17}")
print("-" * 62)
for col, lbl, fmt in [
    ("iv",               "IV",             "{:.1%}"),
    ("iv_rv",            "IV/RV ratio",    "{:.2f}x"),
    ("delta",            "Delta",          "{:.3f}"),
    ("theta_day",        "Theta Rs/day",   "Rs {:.0f}"),
    ("theta_bleed_pct",  "Theta bleed/day","{:.2f}%"),
    ("vega_1pct",        "Vega Rs/1%IV",   "Rs {:.0f}"),
    ("vega_theta",       "Vega/Theta",     "{:.2f}x"),
]:
    vc = cheap[col].median() if col != "iv" else cheap[col].median()
    ve = expen[col].median()
    lc = fmt.format(vc); le = fmt.format(ve)
    print(f"  {lbl:<26} {lc:>14} {le:>17}")

print(f"\n  Days in cheap regime  : {len(cheap):>5}  ({len(cheap)/N*100:.1f}%)")
print(f"  Days in expensive regime: {len(expen):>5}  ({len(expen)/N*100:.1f}%)")

print("\n→ Done.")
