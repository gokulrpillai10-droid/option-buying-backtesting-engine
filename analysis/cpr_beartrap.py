"""Bear-trap scan: days where price broke DOWN through the CPR bottom (a red
close-below), then reversed back up through the band and continued higher.

These are the structural whipsaw LOSSES for CPRBreakout: the strategy enters a
deep-ITM PUT on the downside close-below, then a later close ABOVE the top edge
fires the far-edge stop (cpr_breakout.py:127) -- and being one-trade-per-day it
misses the up-continuation entirely.

Pure spot price-action (no options chain). Definition, matched to the strategy's
own trigger semantics (cpr_breakout.py:102-105):
  - downside breakout : FIRST breakout of the day (before 14:45 cutoff) is a PUT
                        trigger -- RED candle, high>=bottom, close<bottom.
                        (If a CALL trigger fires first, skip: strategy is long.)
  - reversal back up  : a later candle CLOSES above the top edge (reclaim = the
                        PUT's far-edge stop level).
  - continued upward  : the session CLOSES above the top edge (held, not a poke).

Loads spot ONCE. CPR = prior-day H/L/C, same formula as the strategy.
"""
from datetime import time

import pandas as pd

from engine.data.spot import SpotSeries

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
CUTOFF = time(14, 45)          # strategy entry cutoff -- breakouts after this aren't tradeable
CONT_PCT = 0.50                # "continued that movement": post-reversal high must run this
                               # many % of price ABOVE the top edge (not just a marginal close)

print("Loading spot (5min) ...")
spot = SpotSeries.from_index(ROOT, "5min")
df = spot._df.copy()
df["day"] = df["DateTime"].dt.normalize()
print(f"spot: {len(df):,} bars, {df['day'].nunique():,} sessions, "
      f"{df['DateTime'].min().date()} -> {df['DateTime'].max().date()}\n")


def cpr(h, l, c):
    p = (h + l + c) / 3.0
    bc = (h + l) / 2.0
    tc = 2.0 * p - bc
    return max(tc, bc), min(tc, bc), p          # top, bottom, pivot


rows = []
for day, g in df.groupby("day"):
    g = g.sort_values("DateTime")
    dt0 = g["DateTime"].iloc[0].to_pydatetime()
    hlc = spot.prev_day_hlc(dt0)
    if hlc is None:
        continue
    ph, pl, pc = hlc
    top, bottom, pivot = cpr(ph, pl, pc)
    prng = ph - pl
    ratio = (top - bottom) / prng if prng > 0 else 1e9      # CPR width filter input
    bars = list(g.itertuples(index=False))

    # first breakout of EITHER side, before the entry cutoff (what the strategy trades)
    trig_i = trig_side = None
    for i, b in enumerate(bars):
        if b.DateTime.time() >= CUTOFF:
            break
        if b.Low <= top and b.Close > top:
            trig_i, trig_side = i, "CALL"; break
        if b.High >= bottom and b.Close < bottom:
            trig_i, trig_side = i, "PUT"; break
    if trig_side != "PUT":
        continue                                            # not a downside-first day
    bk = bars[trig_i]
    red = bk.Close < bk.Open

    # reversal: first candle after the break that CLOSES above the top edge
    recl = None
    for j in range(trig_i + 1, len(bars)):
        if bars[j].Close > top:
            recl = j; break

    day_close = bars[-1].Close
    reclaimed = recl is not None
    post_high = max(x.High for x in bars[recl:]) if reclaimed else bk.Close
    run_top = post_high - top                               # extension ABOVE top after reversal (pts)
    close_top = day_close - top                             # close vs top (pts, >0 = held above)
    run_pct = 100.0 * run_top / top if reclaimed else 0.0   # extension as % of price
    # "continued that movement" = reclaimed, closed above the band, AND ran a real
    # distance above the top (not a marginal poke).
    continued = reclaimed and close_top > 0 and run_pct >= CONT_PCT

    rows.append(dict(
        date=day.date(), red=red, ratio=ratio, reclaimed=reclaimed,
        brk_t=bk.DateTime.time().strftime("%H:%M"), brk_close=bk.Close,
        bottom=bottom, top=top, band=top - bottom,
        recl_t=(bars[recl].DateTime.time().strftime("%H:%M") if reclaimed else "-"),
        recl_close=(bars[recl].Close if reclaimed else float("nan")),
        day_close=day_close, post_high=post_high,
        depth=bottom - bk.Close,                            # how far below bottom it closed (pts)
        run_top=run_top,                                    # continuation above top (pts)
        run_pct=run_pct,                                    # continuation above top (% of price)
        close_top=close_top,                                # day close above top (pts)
        missed_up=post_high - bk.Close,                     # break-close -> post-reclaim high (pts)
        continued=continued,
    ))

R = pd.DataFrame(rows)
if R.empty:
    print("no downside-first breakout days found"); raise SystemExit

# The full bear-trap set: red break-down + reclaim + STRONG continuation up
red = R[R.red]
rrec = red[red.reclaimed]                                   # red + reclaimed the top
M = R[R.red & R.continued].copy().sort_values("date")
print(f"=== DOWNSIDE-FIRST BREAKOUTS (first breakout of the day is a PUT trigger, before 14:45) ===")
print(f"   total downside-first days                        : {len(R)}   (red: {len(red)})")
print(f"   -> reclaimed the top edge (reversed up thru band): {R.reclaimed.sum()}   "
      f"({R.reclaimed.mean()*100:.0f}%)")
print(f"   -> stayed down, never reclaimed (clean PUT wins)  : {(~R.reclaimed).sum()}")
print(f"   -> reclaimed AND closed day above top (held up)   : {(R.reclaimed & (R.close_top>0)).sum()}\n")

print(f"=== CONTINUATION STRENGTH (red break-downs that reclaimed the top: {len(rrec)}) ===")
print(f"how far price RAN above the top edge after the reversal (% of price):")
for thr in (0.0, 0.30, 0.50, 0.75, 1.00, 1.50):
    n = ((rrec.close_top > 0) & (rrec.run_pct >= thr)).sum()
    tag = "  <- marginal (just closes above)" if thr == 0.0 else ""
    print(f"   ran >= {thr:>4.2f}% above top : {n:>3} days{tag}")
print(f"\n>>> USING CONT_PCT >= {CONT_PCT:.2f}%  ->  {len(M)} strong-continuation bear-traps\n")

print("=== STRONG BEAR-TRAP DAYS: red break-down -> reversal -> RAN well above the band ===")
print("(sorted by continuation size; each is a PUT stop-out that then misses a real up-move)\n")
MS = M.sort_values("run_pct", ascending=False)
hdr = (f"{'date':<12}{'brk':>6}{'brkClose':>10}{'top':>8}{'recl':>7}"
       f"{'runTop':>8}{'run%':>7}{'closeTop':>9}{'missedUp':>9}{'ratio':>7}")
print(hdr); print("-" * len(hdr))
for _, r in MS.iterrows():
    print(f"{str(r.date):<12}{r.brk_t:>6}{r.brk_close:>10.0f}{r.top:>8.0f}{r.recl_t:>7}"
          f"{r.run_top:>8.0f}{r.run_pct:>7.2f}{r.close_top:>9.0f}{r.missed_up:>9.0f}{r.ratio:>7.2f}")

M["yr"] = pd.to_datetime(M.date).dt.year
print("\n=== BY YEAR (strong-continuation traps) ===")
for y, s in M.groupby("yr"):
    print(f"  {y}: {len(s):>3} days   avg run above top {s.run_top.mean():>5.0f}pt "
          f"({s.run_pct.mean():>4.2f}%)   avg missed-up {s.missed_up.mean():>6.0f}pt")

print("\n=== CPR-WIDTH FILTER (narrow-band gate would skip WIDE choppy days) ===")
for thr in (0.25, 0.15):
    kept = (M.ratio <= thr).sum()
    print(f"  max_cpr_ratio<={thr}: {kept}/{len(M)} bear-traps survive the filter "
          f"({len(M)-kept} would be skipped as too-wide/choppy)")
