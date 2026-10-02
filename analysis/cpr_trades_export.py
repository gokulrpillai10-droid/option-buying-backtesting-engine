"""Export CPR trade-level data to JSON for the dashboard."""
import json
from datetime import date

import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from engine.instruments import lot_size_for
from strategies.cpr_breakout import CPRBreakout

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 3, 30)
SLIP = 0.0025

ENTRY_VOL = []

class Rec(CPRBreakout):
    def on_bar(self, ctx):
        orders = super().on_bar(ctx)
        if orders:
            o = orders[0]
            try:
                q = (ctx.snapshot().call(o.strike) if o.side == "CALL"
                     else ctx.snapshot().put(o.strike))
                vol = q.volume if q.is_traded else 0
            except Exception:
                vol = 0
            ENTRY_VOL.append({"date": str(ctx.now.date()), "evol": vol, "lots": o.lots})
        return orders

print("Loading data...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=SLIP))

print("Running backtest...")
res = bt.run(Rec(book_prem=50, book_lots=4, trail_prem=12, stop_mode="far",
                 stop_prem=None, max_iv=0.12), START, END)

# Build trade frame
rows = []
for t in res.trades:
    entry_dt = t.entry_dt
    exit_dt  = t.exit_dt
    rows.append({
        "date":     str(t.date),
        "entry_dt": str(entry_dt) if entry_dt else "",
        "exit_dt":  str(exit_dt)  if exit_dt  else "",
        "side":     t.side,
        "strike":   int(t.strike),
        "entry":    round(float(t.entry), 1),
        "exit":     round(float(t.exit),  1),
        "qty":      int(t.qty),
        "gross":    round(float(t.net + t.cost), 1),
        "cost":     round(float(t.cost), 1),
        "net":      round(float(t.net),  1),
        "reason":   getattr(t, "reason", ""),
    })

tr = pd.DataFrame(rows)
tr["year"] = pd.to_datetime(tr["date"]).dt.year

# Volume mult for live-fill filter
ev = pd.DataFrame(ENTRY_VOL).drop_duplicates("date")
ev["req"] = ev.apply(lambda r: r["lots"] * lot_size_for(date.fromisoformat(r["date"])), axis=1)
ev["vmult"] = ev["evol"] / ev["req"]
vmult = dict(zip(ev["date"], ev["vmult"]))
tr["vmult"] = tr["date"].map(vmult).fillna(0)

out = {
    "all":  tr.to_dict(orient="records"),
    "live": tr[tr["vmult"] >= 10].to_dict(orient="records"),
}

path = "analysis/cpr_trades.json"
with open(path, "w") as f:
    json.dump(out, f, separators=(",", ":"))

print(f"Saved {len(out['all'])} all-entries trades, {len(out['live'])} live-fill trades → {path}")
