"""DEEP-ITM FILL CHECK -- the pre-pilot liquidity audit for the deployed CPR config.

The engine's execution gate confirms is_traded (CallIsTradedStrike) but its volume/OI
floor defaults to 0, so the 280 deployed trades were never checked for whether the
~150pt-deep-ITM strike actually has enough MINUTE-level volume/OI to absorb a 6-lot order.

This re-runs the DEPLOYED config (max_iv=0.12) and at every entry records the deep-ITM
quote AS-OF the signal minute (what the gate sees): is_traded, minute volume, OI, delta,
premium. Then audits fillability of 6 lots (= 6 * date-keyed lot size).

Reports: traded-strike rate, minute-volume vs required qty (can one bar absorb the order?),
OI depth, delta (is it really ~0.8?), premium/capital, and modeled slippage cost in Rs.
slip 0.25%. Loads data ONCE.
"""
from datetime import date

import numpy as np
import pandas as pd

from engine.data.catalog import Catalog
from engine.data.spot import SpotSeries
from engine.data.market import MarketData
from engine.execution.costs import CostModel
from engine.backtest import Backtest, Knobs
from engine.instruments import lot_size_for
from strategies.cpr_breakout import CPRBreakout

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)
SLIP = 0.0025
LOTS = 6

ENTRIES = []


class Audit(CPRBreakout):
    def on_bar(self, ctx):
        orders = super().on_bar(ctx)
        if orders:
            o = orders[0]
            rec = {"date": pd.Timestamp(ctx.now.date()), "side": o.side,
                   "strike": o.strike, "lots": o.lots}
            try:
                snap = ctx.snapshot()
                q = snap.call(o.strike) if o.side == "CALL" else snap.put(o.strike)
                rec.update(is_traded=q.is_traded, volume=q.volume, oi=q.oi,
                           delta=q.delta, premium=q.close, miss=False)
            except Exception:
                rec.update(is_traded=False, volume=0, oi=0, delta=np.nan,
                           premium=np.nan, miss=True)
            ENTRIES.append(rec)
        return orders


print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=SLIP))

print("Running DEPLOYED CPR config (max_iv=0.12), auditing every entry ...")
res = bt.run(Audit(book_prem=50, book_lots=4, trail_prem=12, stop_mode="far",
                   stop_prem=None, max_iv=0.12), START, END)

e = pd.DataFrame(ENTRIES)
e["lotsize"] = e.date.dt.date.map(lot_size_for)
e["req_qty"] = e.lots * e.lotsize                      # qty we must fill (6 lots)
e["vol_mult"] = e.volume / e.req_qty                   # minute volume / our order size
e["capital"] = e.premium * e.req_qty                   # Rs deployed (abs delta-1 premium notional)
e["slip_cost"] = e.premium * SLIP * e.req_qty          # modeled adverse slippage, Rs

n = len(e)
print(f"\n=== DEEP-ITM FILL AUDIT  ({n} entry signals; trades realised in engine = {len(res.trades)} legs' positions) ===")

print(f"\n1. TRADEABILITY")
print(f"   on a traded strike (is_traded)    : {e.is_traded.mean()*100:5.1f}%   ({e.is_traded.sum()}/{n})")
print(f"   strike missing from chain         : {e.miss.sum()}")
print(f"   not-traded (engine would reject)  : {(~e.is_traded).sum()}")

print(f"\n2. MINUTE-LEVEL LIQUIDITY (signal-bar volume vs our 6-lot order)")
print(f"   required qty / order (6 lots)     : {int(e.req_qty.min())}-{int(e.req_qty.max())}")
print(f"   median signal-minute volume       : {e.volume.median():,.0f}")
print(f"   median volume / order size        : {e.vol_mult.median():,.1f}x")
for thr in (1, 5, 10, 50):
    pct = (e.vol_mult >= thr).mean() * 100
    print(f"   entries with vol >= {thr:>2}x order    : {pct:5.1f}%")
print(f"   entries with vol <  1x order      : {(e.vol_mult < 1).sum()}  (one bar can't fully absorb)")

print(f"\n3. OPEN-INTEREST DEPTH")
print(f"   median OI at strike               : {e.oi.median():,.0f}")
print(f"   our order as % of OI (median)     : {(e.req_qty/e.oi).median()*100:.3f}%")

print(f"\n4. DELTA (is the strike really deep-ITM ~0.8?)")
print(f"   median |delta|                    : {e.delta.abs().median():.2f}")
print(f"   delta in [0.70, 0.95]             : {((e.delta.abs()>=0.70)&(e.delta.abs()<=0.95)).mean()*100:.1f}%")

print(f"\n5. CAPITAL & SLIPPAGE per trade")
print(f"   median premium (points)           : {e.premium.median():,.1f}")
print(f"   median capital deployed (Rs)      : {e.capital.median():,.0f}")
print(f"   median modeled slip cost (Rs)     : {e.slip_cost.median():,.0f}")
print(f"   total modeled slip cost (Rs)      : {e.slip_cost.sum():,.0f}")

# worst-liquidity entries (the ones to worry about)
print(f"\n6. WORST 8 ENTRIES BY VOLUME/ORDER RATIO")
w = e.nsmallest(8, "vol_mult")[["date", "side", "strike", "volume", "req_qty", "vol_mult", "oi", "delta"]]
with pd.option_context("display.max_rows", None, "display.width", 120):
    print(w.to_string(index=False))
