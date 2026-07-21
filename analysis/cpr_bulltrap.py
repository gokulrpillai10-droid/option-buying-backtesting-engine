"""Bull-trap scan: days where price broke UP through the CPR top (a green
close-above), then reversed back down through the band and continued lower.

The mirror image of cpr_beartrap.py.  These are the structural whipsaw LOSSES
for CPRBreakout on the LONG side: the strategy enters a deep-ITM CALL on the
upside close-above, then a later close BELOW the bottom edge fires the far-edge
stop (cpr_breakout.py:127) -- and being one-trade-per-day it misses the
down-continuation entirely.

Pure spot price-action (no options chain). Definition, matched to the strategy's
own trigger semantics (cpr_breakout.py:102-105):
  - upside breakout   : FIRST breakout of the day (before 14:45 cutoff) is a CALL
                        trigger -- GREEN candle, low<=top, close>top.
                        (If a PUT trigger fires first, skip: strategy is short.)
  - reversal back down: a later candle CLOSES below the bottom edge (the CALL's
                        far-edge stop level).
  - continued downward: the session CLOSES below the bottom edge (held, not a poke).

Loads spot ONCE. CPR = prior-day H/L/C, same formula as the strategy.
"""
from datetime import time

import pandas as pd

from engine.data.spot import SpotSeries

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
CUTOFF = time(14, 45)          # strategy entry cutoff -- breakouts after this aren't tradeable
CONT_PCT = 0.50                # "continued that movement": post-reversal low must run this
                               # many % of price BELOW the bottom edge (not just a marginal close)

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
    if trig_side != "CALL":
        continue                                            # not an upside-first day
    bk = bars[trig_i]
    green = bk.Close > bk.Open

    # reversal: first candle after the break that CLOSES below the bottom edge
    recl = None
    for j in range(trig_i + 1, len(bars)):
        if bars[j].Close < bottom:
            recl = j; break

    day_close = bars[-1].Close
    reclaimed = recl is not None
    post_low = min(x.Low for x in bars[recl:]) if reclaimed else bk.Close
    run_bot = bottom - post_low                             # extension BELOW bottom after reversal (pts)
    close_bot = bottom - day_close                          # close vs bottom (pts, >0 = held below)
    run_pct = 100.0 * run_bot / bottom if reclaimed else 0.0  # extension as % of price
    # "continued that movement" = reversed, closed below the band, AND ran a real
    # distance below the bottom (not a marginal poke).
    continued = reclaimed and close_bot > 0 and run_pct >= CONT_PCT

    rows.append(dict(
        date=day.date(), green=green, ratio=ratio, reclaimed=reclaimed,
        brk_t=bk.DateTime.time().strftime("%H:%M"), brk_close=bk.Close,
        bottom=bottom, top=top, band=top - bottom,
        recl_t=(bars[recl].DateTime.time().strftime("%H:%M") if reclaimed else "-"),
        recl_close=(bars[recl].Close if reclaimed else float("nan")),
        day_close=day_close, post_low=post_low,
        height=bk.Close - top,                              # how far above top it closed (pts)
        run_bot=run_bot,                                    # continuation below bottom (pts)
        run_pct=run_pct,                                    # continuation below bottom (% of price)
        close_bot=close_bot,                                # day close below bottom (pts)
        missed_dn=bk.Close - post_low,                      # break-close -> post-reversal low (pts)
        continued=continued,
    ))

R = pd.DataFrame(rows)
if R.empty:
    print("no upside-first breakout days found"); raise SystemExit

# The full bull-trap set: green break-up + reversal + STRONG continuation down
grn = R[R.green]
grec = grn[grn.reclaimed]                                   # green + reversed below the bottom
M = R[R.green & R.continued].copy().sort_values("date")
print(f"=== UPSIDE-FIRST BREAKOUTS (first breakout of the day is a CALL trigger, before 14:45) ===")
print(f"   total upside-first days                            : {len(R)}   (green: {len(grn)})")
print(f"   -> broke the bottom edge (reversed down thru band) : {R.reclaimed.sum()}   "
      f"({R.reclaimed.mean()*100:.0f}%)")
print(f"   -> stayed up, never broke down (clean CALL wins)   : {(~R.reclaimed).sum()}")
print(f"   -> reversed AND closed day below bottom (held down): {(R.reclaimed & (R.close_bot>0)).sum()}\n")

print(f"=== CONTINUATION STRENGTH (green break-ups that broke the bottom: {len(grec)}) ===")
print(f"how far price RAN below the bottom edge after the reversal (% of price):")
for thr in (0.0, 0.30, 0.50, 0.75, 1.00, 1.50):
    n = ((grec.close_bot > 0) & (grec.run_pct >= thr)).sum()
    tag = "  <- marginal (just closes below)" if thr == 0.0 else ""
    print(f"   ran >= {thr:>4.2f}% below bottom : {n:>3} days{tag}")
print(f"\n>>> USING CONT_PCT >= {CONT_PCT:.2f}%  ->  {len(M)} strong-continuation bull-traps\n")

print("=== STRONG BULL-TRAP DAYS: green break-up -> reversal -> RAN well below the band ===")
print("(sorted by continuation size; each is a CALL stop-out that then misses a real down-move)\n")
MS = M.sort_values("run_pct", ascending=False)
hdr = (f"{'date':<12}{'brk':>6}{'brkClose':>10}{'bottom':>8}{'recl':>7}"
       f"{'runBot':>8}{'run%':>7}{'closeBot':>9}{'missedDn':>9}{'ratio':>7}")
print(hdr); print("-" * len(hdr))
for _, r in MS.iterrows():
    print(f"{str(r.date):<12}{r.brk_t:>6}{r.brk_close:>10.0f}{r.bottom:>8.0f}{r.recl_t:>7}"
          f"{r.run_bot:>8.0f}{r.run_pct:>7.2f}{r.close_bot:>9.0f}{r.missed_dn:>9.0f}{r.ratio:>7.2f}")

M["yr"] = pd.to_datetime(M.date).dt.year
print("\n=== BY YEAR (strong-continuation traps) ===")
for y, s in M.groupby("yr"):
    print(f"  {y}: {len(s):>3} days   avg run below bottom {s.run_bot.mean():>5.0f}pt "
          f"({s.run_pct.mean():>4.2f}%)   avg missed-dn {s.missed_dn.mean():>6.0f}pt")

print("\n=== CPR-WIDTH FILTER (narrow-band gate would skip WIDE choppy days) ===")
for thr in (0.25, 0.15):
    kept = (M.ratio <= thr).sum()
    print(f"  max_cpr_ratio<={thr}: {kept}/{len(M)} bull-traps survive the filter "
          f"({len(M)-kept} would be skipped as too-wide/choppy)")
