"""Liquidity reality check for the profitable IV<=0.13 CPR config.

No bid/ask in the store, so use Volume + OI as the liquidity proxy. For each entry,
capture the deep-ITM leg's premium (-> what 1% costs in Rs), delta, OI, and the DAY's
total traded volume at that strike; benchmark vs the ATM strike (where liquidity sits);
and size our 6-lot order against that volume/OI. Thin strikes => wide spread => the
modeled 1% is realistic-or-optimistic. Loads data ONCE.
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
from strategies.cpr_breakout import CPRBreakout, LOTS

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)

LIQ = []


class LiqCPR(CPRBreakout):
    def on_bar(self, ctx):
        orders = super().on_bar(ctx)
        if orders:
            o = orders[0]
            try:
                snap = ctx.snapshot()
                q = snap.call(o.strike) if o.side == "CALL" else snap.put(o.strike)
                df = ctx._chain._df
                vcol = "CallVolume" if o.side == "CALL" else "PutVolume"
                day_vol = int(df[df["StrikePrice"] == o.strike][vcol].sum())
                atmk = ctx.atm_strike(o.side)
                qa = (snap.call(atmk) if o.side == "CALL" else snap.put(atmk)) if atmk else None
                atm_vol = int(df[df["StrikePrice"] == atmk][vcol].sum()) if atmk else 0
                qty = LOTS * lot_size_for(ctx.now.date())
                LIQ.append({
                    "premium": q.close, "delta": abs(q.delta), "oi": q.oi,
                    "is_traded": int(q.is_traded), "day_vol": day_vol,
                    "atm_oi": qa.oi if qa else np.nan, "atm_vol": atm_vol, "qty": qty,
                })
            except Exception:
                pass
        return orders


print("Loading spot + catalog ...")
cat = Catalog(ROOT)
spot = SpotSeries.from_index(ROOT, "5min")
md = MarketData(cat, spot, min_dte=3)
bt = Backtest(md, CostModel(), Knobs(fill_price="open", slippage_pct=0.01))

bt.run(LiqCPR(book_prem=50, book_lots=2, trail_prem=12, stop_mode="far",
              stop_prem=None, max_iv=0.13), START, END)

d = pd.DataFrame(LIQ)
print(f"\n=== Liquidity of the deep-ITM legs (IV<=0.13 config, {len(d)} entries) ===")


def pct(s):
    return f"p10 {np.nanpercentile(s,10):>10,.0f}   median {np.nanmedian(s):>10,.0f}   p90 {np.nanpercentile(s,90):>10,.0f}"


print(f"entry premium (Rs) : {pct(d.premium)}")
print(f"  -> 1% per side    : median Rs{np.nanmedian(d.premium)*0.01:.1f}/unit  "
      f"round-trip Rs{np.nanmedian(d.premium)*0.02*np.nanmedian(d.qty):,.0f} on {int(np.nanmedian(d.qty))} qty")
print(f"delta (|.|)        : {pct(d.delta)}")
print(f"strike OI          : {pct(d.oi)}")
print(f"strike day-volume  : {pct(d.day_vol)}")
print(f"ATM day-volume     : {pct(d.atm_vol)}")
print(f"ATM OI             : {pct(d.atm_oi)}")

d["qty_vs_dayvol"] = d.qty / d.day_vol.replace(0, np.nan) * 100
d["qty_vs_oi"] = d.qty / d.oi.replace(0, np.nan) * 100
d["deepITM_vs_atm_vol"] = d.day_vol / d.atm_vol.replace(0, np.nan) * 100
print(f"\nour 6-lot order ({int(np.nanmedian(d.qty))} qty) as % of strike DAY volume: {pct(d.qty_vs_dayvol)}")
print(f"our 6-lot order as % of strike OI                  : {pct(d.qty_vs_oi)}")
print(f"deep-ITM volume as % of ATM volume (liquidity gap) : {pct(d.deepITM_vs_atm_vol)}")
print(f"\nis_traded flag = 1 on {d.is_traded.mean()*100:.0f}% of entries")
print(f"entries with strike day-volume < 1000 contracts    : {(d.day_vol < 1000).mean()*100:.0f}%")
print(f"entries with strike day-volume == 0 (untraded)     : {(d.day_vol == 0).mean()*100:.0f}%")
