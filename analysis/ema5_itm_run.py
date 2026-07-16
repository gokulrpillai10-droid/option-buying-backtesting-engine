"""5-EMA deviation strategy on NIFTY via ITM option buying — engine backtest.

PUT side on 5-min signals, CALL side on 15-min signals (separate runs, per
the source strategy's timeframe split). Validated knobs: next-open fills,
0.25% slippage/side, 10x signal-bar volume floor, min_dte=3.

Run: PYTHONPATH=<repo> python3 analysis/ema5_itm_run.py [START_YEAR] [END_YEAR]
Env: PREM_TGT=<points> replaces the 3R spot target with a premium target
     (exit when the option is up that many points over entry) on both sides.
     PREM_TRAIL=<arm>/<gap> replaces the target with an armed premium trail:
     once the option is up <arm> points, trail the premium by <gap> points.
     SCALE_TRAIL=<tgt>/<gap>[/<frac>] books <frac> (default 0.5) of the
     position at +<tgt> premium points, then trails the rest by <gap> points.
     PREM_SL=<points> replaces the spot stop with a premium stop that many
     points below entry (needs PREM_TGT or SCALE_TRAIL to bracket with).
     LOTS=<n> position size in lots (default 1).
"""
import os
import sys
from datetime import date

import pandas as pd

from engine.backtest import Backtest, Knobs
from engine.data.catalog import Catalog
from engine.data.market import MarketData
from engine.data.spot import SpotSeries
from engine.execution.costs import CostModel
from strategies.ema5_itm import Ema5Itm

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START = date(int(sys.argv[1]) if len(sys.argv) > 1 else 2020, 1, 1)
END = date(int(sys.argv[2]) if len(sys.argv) > 2 else 2026, 12, 31)


def _env_floats(name, want, shape, pad=None):
    """NAME=a/b[/c] -> float tuple of length ``want`` (or None if unset);
    fail fast with a clear message instead of unpacking mid-backtest."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    parts = raw.split("/")
    if pad is not None and len(parts) == want - 1:
        parts.append(str(pad))
    try:
        vals = tuple(float(x) for x in parts)
    except ValueError:
        sys.exit(f"{name}={raw!r}: values must be numbers, like {shape}")
    if len(vals) != want:
        sys.exit(f"{name}={raw!r}: expected {want} values, like {shape}")
    return vals


PREM_TGT = float(os.environ["PREM_TGT"]) if "PREM_TGT" in os.environ else None
PREM_TRAIL = _env_floats("PREM_TRAIL", 2, "25/12")
SCALE_TRAIL = _env_floats("SCALE_TRAIL", 3, "30/12/0.5", pad=0.5)
PREM_SL = float(os.environ["PREM_SL"]) if "PREM_SL" in os.environ else None
LOTS = int(os.environ.get("LOTS", 1))


def run_side(side, timeframe, rr):
    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, timeframe)
    md = MarketData(cat, spot, min_dte=3)
    bt = Backtest(md, CostModel(),
                  Knobs(fill_price="open", slippage_pct=0.0025,
                        timeframe=timeframe, min_volume_mult=10.0))
    res = bt.run(Ema5Itm(side=side, itm_offset=100, rr=rr, lots=LOTS,
                         premium_tgt=PREM_TGT, prem_trail=PREM_TRAIL,
                         scale_trail=SCALE_TRAIL, prem_sl=PREM_SL), START, END)
    return res


def report(tag, res):
    t = pd.DataFrame([{"date": x.date, "net": x.net, "reason": x.reason}
                      for x in res.trades])
    if len(t) == 0:
        print(f"{tag}: no trades"); return
    t["yr"] = pd.to_datetime(t["date"]).dt.year
    print(f"\n{tag}  ({len(t)} trades)")
    print(f"{'Year':<6}{'Trades':>7}{'Win%':>6}{'Net Rs':>12}{'PF':>7}")
    for y, s in t.groupby("yr"):
        W = s[s.net > 0]
        pf = (W.net.sum() / -s[s.net <= 0].net.sum()
              if (s.net <= 0).any() else float("inf"))
        print(f"{y:<6}{len(s):>7}{len(W)/len(s)*100:>5.0f}%{s.net.sum():>12,.0f}{pf:>7.2f}")
    print(f"{'ALL':<6}{len(t):>7}{res.win_rate()*100:>5.0f}%{res.net():>12,.0f}"
          f"{res.profit_factor():>7.2f}")
    print(f"t-stat {res.t_stat():.2f} | tail (excl top-10) Rs{res.tail_excl(10):,.0f}")
    print("exits:", dict(t.reason.value_counts()))


if __name__ == "__main__":
    if PREM_TGT is not None:
        tgt = f"prem_tgt +{PREM_TGT:g}pts"
    elif PREM_TRAIL is not None:
        tgt = f"trail +{PREM_TRAIL[0]:g}pts arm / {PREM_TRAIL[1]:g}pts gap"
    elif SCALE_TRAIL is not None:
        tgt = (f"scale {SCALE_TRAIL[2]:g} out at +{SCALE_TRAIL[0]:g}pts, "
               f"trail rest {SCALE_TRAIL[1]:g}pts")
    else:
        tgt = "RR 1:3"
    if PREM_SL is not None:
        tgt += f", prem SL -{PREM_SL:g}pts"
    if LOTS != 1:
        tgt += f", {LOTS} lots"
    for side, tf in [("PUT", "5min"), ("CALL", "15min")]:
        print(f"\n{'='*60}\nRunning {side} side on {tf} ({tgt}) ...")
        report(f"5-EMA ITM {side} ({tf}, {tgt})", run_side(side, tf, rr=3.0))
