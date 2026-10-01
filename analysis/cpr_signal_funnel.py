"""Year-by-year CPR signal funnel.

For each year shows:
  - calendar trading days in data
  - days with a valid CPR cross  (max_iv=None, no filter)
  - days kept by the IV<=0.12 filter  (deployed config)
  - % of crosses killed by IV filter

Diagnoses the 2023→2024 signal-frequency drop (105→37 trade-days).
"""
from datetime import date

import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from strategies.cpr_breakout import CPRBreakout

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 3, 30)

print("Loading data ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.0025))

# --- run 1: no IV filter (counts all CPR crosses) ---
res_no_iv = bt.run(CPRBreakout(max_iv=None), START, END)
# --- run 2: deployed config (IV<=0.12) ---
res_iv = bt.run(CPRBreakout(max_iv=0.12), START, END)


def trade_days_by_year(res):
    """Set of unique trade dates per year."""
    dates = {t.date for t in res.trades}
    df = pd.DataFrame({"date": sorted(dates)})
    df["yr"] = pd.to_datetime(df["date"]).dt.year
    return df.groupby("yr")["date"].apply(set).to_dict()


no_iv_by_yr = trade_days_by_year(res_no_iv)
iv_by_yr = trade_days_by_year(res_iv)

all_years = sorted(set(list(no_iv_by_yr) + list(iv_by_yr)))

# calendar trading days per year from the market data
cal_days = md.trading_days(START, END)
cal_by_yr: dict[int, int] = {}
for d in cal_days:
    cal_by_yr[d.year] = cal_by_yr.get(d.year, 0) + 1

print("\n=== CPR SIGNAL FUNNEL BY YEAR ===")
print(f"{'yr':<6}{'cal':>6}{'crosses':>9}{'cross%':>8}{'kept':>7}{'kept%cross':>12}{'IV-killed%':>12}")
print("-" * 60)

total_cal = total_cross = total_kept = 0
for yr in all_years:
    cal = cal_by_yr.get(yr, 0)
    cross = len(no_iv_by_yr.get(yr, set()))
    kept = len(iv_by_yr.get(yr, set()))
    killed_pct = (cross - kept) / cross * 100 if cross else 0
    cross_pct = cross / cal * 100 if cal else 0
    kept_pct = kept / cross * 100 if cross else 0
    total_cal += cal; total_cross += cross; total_kept += kept
    print(f"{yr:<6}{cal:>6}{cross:>9}{cross_pct:>7.0f}%{kept:>7}{kept_pct:>11.0f}%{killed_pct:>11.0f}%")

print("-" * 60)
print(f"{'TOT':<6}{total_cal:>6}{total_cross:>9}{'':>8}{total_kept:>7}")

print("\n=== NOTES ===")
print("crosses  = days with a valid CPR cross (no IV filter, min_dte=3 still applies)")
print("kept     = days after IV<=0.12 filter (deployed config)")
print("IV-killed% = % of crossing days removed by the IV filter in that year")
print("\nIf IV-killed% is high in 2024, elevated VIX is the culprit.")
print("If crosses itself dropped vs 2023, it's a market structure / data issue.")
