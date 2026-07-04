"""Gap-down continuation: an EARLY, no-look-ahead downside entry test.

From the down-day study: big down days 78% gap down, 94% close red from open,
70% continue after the open. Hypothesis: gap-down + a break of the opening-range
low identifies a trend-down day early enough to capture 25-50 index points.

Tested on ALL days (not just down days) so false positives / stop-outs are real.
Measured in INDEX points on the Forward (the move). Option P&L mapping comes later.

Entry: gap = open(09:15) - prev_close. If gap <= -GAP and price breaks below the
       opening-range (09:15-09:29) low after 09:29 within the entry window ->
       SHORT (buy put) at that minute's price.
Outcome (short): favourable = price falls; we record how far it falls vs how far
       it bounces against us, and simulate book-25 / target-50 / trail-rest.
"""
import sys
from datetime import time

import pandas as pd

from engine.data.catalog import Catalog

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
OR_END = time(9, 29)
ENTRY_CUTOFF = time(11, 30)
SQUARE_OFF = time(15, 15)
GAP = 20.0        # min gap-down (pts) to arm the setup
BREAK_BUF = 2.0   # pts below OR low to confirm breakdown
STOP = 30.0       # pts adverse (index) stop
BOOK1 = 25.0      # first target
BOOK2 = 50.0      # second target
TRAIL = 20.0      # pts trail off the favourable extreme after BOOK2


def run_day(fwd, prev_close):
    or_mask = fwd.index.time <= OR_END
    if or_mask.sum() < 5 or pd.isna(prev_close):
        return None
    open_px = fwd.iloc[0]
    gap = open_px - prev_close
    if gap > -GAP:
        return None  # not a gap-down -> setup not armed
    or_low = fwd[or_mask].min()
    post = fwd[(fwd.index.time > OR_END) & (fwd.index.time <= ENTRY_CUTOFF)]
    entry_dt = None
    for dt, px in post.items():
        if px < or_low - BREAK_BUF:
            entry_dt, entry = dt, px
            break
    if entry_dt is None:
        return None
    path = fwd[fwd.index > entry_dt]
    path = path[path.index.time <= SQUARE_OFF]
    if path.empty:
        return None
    # simulate short: stop above, trail after second target
    booked = 0.0
    extreme = entry  # lowest seen
    stage = 0  # 0 none, 1 hit book1, 2 hit book2 (trailing)
    exit_reason = None
    for dt, px in path.items():
        extreme = min(extreme, px)
        fav = entry - px      # +ve = profit (price fell)
        adverse = px - entry  # +ve = against us
        if stage < 2 and adverse >= STOP:
            booked = -STOP; exit_reason = "stop"; break
        if stage == 0 and fav >= BOOK1:
            stage = 1
        if stage == 1 and fav >= BOOK2:
            stage = 2
        if stage == 2:
            trail_stop = (entry - extreme) - TRAIL  # lock favourable minus trail
            if fav <= trail_stop:
                booked = trail_stop; exit_reason = "trail"; break
    if exit_reason is None:
        booked = entry - path.iloc[-1]; exit_reason = "eod"
    return dict(gap=round(gap, 0), entry=round(entry, 0),
                max_fav=round(entry - path.min(), 0),
                max_adv=round(path.max() - entry, 0),
                captured=round(booked, 0), reason=exit_reason,
                hit25=(entry - path.min()) >= BOOK1,
                hit50=(entry - path.min()) >= BOOK2)


def main(_unused):
    cat = Catalog(ROOT)
    daily = pd.read_csv("analysis/daily_underlying.csv", parse_dates=["date"])
    daily["d"] = daily["date"].dt.date
    pclose = dict(zip(daily["d"], daily["prev_close"]))
    # each date -> its front-month (nearest) expiry, so every day is processed once
    by_expiry = daily.groupby("expiry")["d"].apply(list).to_dict()
    rows = []
    for exp, dates in by_expiry.items():
        p = cat.path_for(exp)
        if p is None:
            continue
        df = pd.read_parquet(p, columns=["DateTime", "Forward"])
        f = df.groupby("DateTime")["Forward"].first().sort_index()
        f_date = f.index.date
        want = set(dates)
        for d in dates:
            day = f[f_date == d]
            if len(day) < 20:
                continue
            r = run_day(day, pclose.get(d))
            if r:
                r["date"] = d
                rows.append(r)
    t = pd.DataFrame(rows).drop_duplicates("date")
    if t.empty:
        print("no signals"); return
    n = len(t)
    print(f"\n=== Gap-down continuation: {n} signal-days (gap<=-{GAP:.0f} + OR-low break) ===")
    print(f"reached +25 pts fav : {t.hit25.mean():.0%}")
    print(f"reached +50 pts fav : {t.hit50.mean():.0%}")
    print(f"stopped out (-{STOP:.0f}): {(t.reason=='stop').mean():.0%}")
    print(f"median max favourable: {t.max_fav.median():+.0f} pts | median max adverse: {t.max_adv.median():+.0f} pts")
    print(f"avg captured/trade   : {t.captured.mean():+.1f} pts (book25/target50/trail{TRAIL:.0f}, stop{STOP:.0f})")
    print(f"total captured       : {t.captured.sum():+.0f} pts over {n} trades")
    win = (t.captured > 0).mean()
    pf_l = -t.loc[t.captured <= 0, "captured"].sum()
    pf = t.loc[t.captured > 0, "captured"].sum() / pf_l if pf_l else 99
    print(f"win rate {win:.0%} | profit factor {pf:.2f}")
    print(f"exit reasons: {t['reason'].value_counts().to_dict()}")
    print("\nby gap size:")
    for lo, hi, name in [(20, 50, "gap 20-50"), (50, 100, "gap 50-100"), (100, 1e9, "gap >100")]:
        s = t[(-t.gap >= lo) & (-t.gap < hi)]
        if len(s) >= 8:
            w = (s.captured > 0).mean()
            print(f"  {name:12s} n={len(s):3d} hit25={s.hit25.mean():4.0%} hit50={s.hit50.mean():4.0%} avgcap={s.captured.mean():+6.1f} win={w:4.0%}")
    t.to_csv("analysis/gap_down_trades.csv", index=False)


if __name__ == "__main__":
    main(sys.argv[1:])
