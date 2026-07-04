"""Reconcile G12-CE through the ENGINE vs the analysis-script core output.

Runs strategies.g12_ce.G12CE through the build-strategies engine on real data, and
lines its trades up against analysis/g12_ce_corrected_core.csv (the script's CORE
trades). fill_price='close', slippage 0 -> engine entry/exit are the RAW CallClose at
the fill minutes (script entry = raw+1.5 slip; compare signal first, prices in ballpark).
"""
import sys
from datetime import date

import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from strategies.g12_ce import G12CE

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START = date(int(sys.argv[1]) if len(sys.argv) > 1 else 2024, 1, 1)
END = date(int(sys.argv[2]) if len(sys.argv) > 2 else 2024, 12, 31)

print(f"Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, timeframe="5min")
md = MarketData(cat, spot, min_dte=0)   # match g12_ce_spot's rule: nearest expiry e>=d (P3-3)
bt = Backtest(md, CostModel(), Knobs(fill_price="close", slippage_pct=0.0))

print(f"Running G12CE through engine {START}..{END} ...")
res = bt.run(G12CE(), START, END)
eng = pd.DataFrame([{"date": t.date, "strike": t.strike, "reason": t.reason,
                     "entry": round(t.entry, 2), "exit": round(t.exit, 2),
                     "entry_dt": t.entry_dt} for t in res.trades])
eng.to_csv("analysis/g12_engine_trades.csv", index=False)
print(f"\nENGINE trades: {len(eng)}")

# compare vs the SPOT-based script reference (same INDEX underlying as the engine)
script = pd.read_csv("analysis/g12_spot_core_trades.csv", parse_dates=["date"])
script = script[(script.date.dt.date >= START) & (script.date.dt.date <= END)]
print(f"SCRIPT (g12_ce_spot core) trades in window: {len(script)}")

print("\n--- ENGINE (first 8) ---")
print(eng.head(8).to_string(index=False) if len(eng) else "  none")
print("\n--- SCRIPT (first 8) ---")
print(script[["date", "strike", "reason", "entry", "exit"]].head(8).to_string(index=False))

# per-day PAIRED comparison: engine.entry should == script.entry - 1.5 (engine slip=0)
script["d"] = script.date.dt.date
exact, mism, n = 0, [], 0
for d in sorted(set(eng.date) | set(script.d)):
    e = eng[eng.date == d].sort_values("entry").reset_index(drop=True)
    s = script[script.d == d].sort_values("entry").reset_index(drop=True)
    if len(e) != len(s):
        mism.append((d, f"count {len(e)} vs {len(s)}"))
        continue
    for i in range(len(e)):
        n += 1
        de = abs(e.entry[i] - (s.entry[i] - 1.5))      # entry diff net of 1.5 slip
        if e.strike[i] == s.strike[i] and e.reason[i] == s.reason[i] and de < 0.6:
            exact += 1
        else:
            mism.append((d, f"strike {e.strike[i]}/{s.strike[i]} reason {e.reason[i]}/{s.reason[i]} "
                            f"entry {e.entry[i]:.1f}/{s.entry[i]:.1f} (diff {de:.1f})"))
print(f"\nTRADE-BY-TRADE: {exact}/{n} exact (strike+reason+entry within slip).  mismatches: {len(mism)}")
for d, why in mism[:15]:
    print(f"  {d}: {why}")
