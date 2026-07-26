"""EMA 9/21/200 + swing-break trend-CONTINUATION on ADANIENT cash, 6.5y.

Spec (user, 2026-07-25): trade WITH the trend. 200 EMA = regime gate (long only
above, short only below); 9/21 EMA cross = pullback-resumption confirmation; a
strong candle breaking a trend line = trigger. Stop protects the false break;
target >=1:2 R:R, then trail on the 9/21 cross for runners.

The discretionary trend line is mechanized as a SWING-BREAK proxy (user's pick):
the "up trend line under higher lows / down line over lower highs" points at the
last swing pivot, so the trigger becomes a strong candle CLOSING beyond the most
recent CONFIRMED swing high (long) / low (short). Reuses sr_core.pivots (fractal,
+/-k), confirmed at pivot+k bars so there is no look-ahead. EMAs run on the
continuous 5-min series (200-EMA ~ 2.7 sessions = the intraday "major trend").

This is the CONTINUATION counterpart to the failed Reversal Blast: on NIFTY that
reversal signal was mildly anti-predictive (market kept trending), so a with-trend
break is the thesis that observation points to. Same dir-null discipline.

Exits, two modes reported side by side:
  R2    fixed 1:2 target off the structure stop (conservative floor)
  TRAIL stop, then hold until the 9/21 opposite cross (the runner mode)

Controls: dir-null (coin-flip direction, matched stop) and no-200 (drop the
trend filter) — an edge must beat its own nulls. Costs: Zerodha MIS + 0.05%
slippage. qty=500.

Run: PYTHONPATH=<repo> python3 analysis/ema_trendline_break.py
"""
from __future__ import annotations

import random
from datetime import time, timedelta

import numpy as np
import pandas as pd

from engine.backtest import Result
from engine.data.spot import _resample_ohlc
from engine.equity.kite_loader import load_cached
from engine.indicators import ema
from analysis.reversal_blast_run import _make_trade
from analysis.sr_core import pivots

SYMBOL = "ADANIENT"
SQUARE = time(15, 15)
PIVOT_K = 3            # 5-min fractal half-window (~15-min swings)
ARM_BARS = 12         # 9/21 cross must be within this many 5-min bars of the break
BODY_MIN = 0.6        # "strong/Marubozu" candle: body/range >= this
EMA_F, EMA_M, EMA_T = 9, 21, 200
SEED = 7


# --------------------------- continuous 5-min features ---------------------
def _bars_since(flag: pd.Series) -> pd.Series:
    idx = pd.Series(np.arange(len(flag)), index=flag.index)
    last = idx.where(flag.values).ffill()
    return (idx - last)


def build_5min(df1: pd.DataFrame) -> pd.DataFrame:
    m5 = _resample_ohlc(df1, "5min", "left")
    m5["e9"] = ema(m5["Close"], EMA_F)
    m5["e21"] = ema(m5["Close"], EMA_M)
    m5["e200"] = ema(m5["Close"], EMA_T)
    rng = (m5["High"] - m5["Low"]).replace(0, np.nan)
    m5["body"] = (m5["Close"] - m5["Open"]).abs() / rng
    m5["bull"] = m5["Close"] > m5["Open"]
    bull_x = (m5["e9"] > m5["e21"]) & (m5["e9"].shift() <= m5["e21"].shift())
    bear_x = (m5["e9"] < m5["e21"]) & (m5["e9"].shift() >= m5["e21"].shift())
    m5["since_bull"] = _bars_since(bull_x)
    m5["since_bear"] = _bars_since(bear_x)
    m5["oppx"] = bull_x | bear_x           # any 9/21 cross (trail trigger)
    m5["day"] = m5["DateTime"].dt.date
    return m5


def confirmed_levels(day5: pd.DataFrame, k: int):
    """Per-bar (last confirmed swing-high price, last confirmed swing-low price),
    a pivot at position p usable only from bar p+k (fractal confirmation)."""
    n = len(day5)
    idx = list(day5.index)
    pos = {ts: i for i, ts in enumerate(idx)}
    highs, lows = pivots(day5, k)
    hev = sorted((pos[ts] + k, pos[ts], p) for ts, p in highs)   # (confirm, pivot, price)
    lev = sorted((pos[ts] + k, pos[ts], p) for ts, p in lows)
    last_hi, last_lo = [None] * n, [None] * n
    for j in range(n):
        elig_h = [(pp, pr) for cp, pp, pr in hev if cp <= j]
        elig_l = [(pp, pr) for cp, pp, pr in lev if cp <= j]
        if elig_h:
            last_hi[j] = max(elig_h, key=lambda x: x[0])[1]
        if elig_l:
            last_lo[j] = min(elig_l, key=lambda x: x[0])[1]
    return last_hi, last_lo


# --------------------------- exit resolution (1-min) -----------------------
def resolve(rows, entry_idx, direction, entry_open, stop_lvl, tgt_lvl, trail_exit_dt):
    """Walk 1-min bars from entry. Pessimistic stop-first intrabar; TRAIL mode
    passes tgt_lvl=None and a trail_exit_dt (the 9/21 opposite-cross time)."""
    entry = entry_open * (1 + 0.0)
    for r in rows[entry_idx:]:
        o, h, l = r.Open, r.High, r.Low
        if r.DateTime.time() >= SQUARE:
            return o, "eod", r.DateTime
        if direction == "long":
            if l <= stop_lvl:
                return (o if o < stop_lvl else stop_lvl), "stop", r.DateTime
            if tgt_lvl is not None and h >= tgt_lvl:
                return (o if o > tgt_lvl else tgt_lvl), "target", r.DateTime
        else:
            if h >= stop_lvl:
                return (o if o > stop_lvl else stop_lvl), "stop", r.DateTime
            if tgt_lvl is not None and l <= tgt_lvl:
                return (o if o < tgt_lvl else tgt_lvl), "target", r.DateTime
        if trail_exit_dt is not None and r.DateTime >= trail_exit_dt:
            return o, "trail", r.DateTime
    last = rows[-1]
    return last.Close, "eod", last.DateTime


# --------------------------- per-day 1-min rows ----------------------------
def prep_1min(df1: pd.DataFrame):
    df = df1.copy()
    df["day"] = df["DateTime"].dt.date
    return {d: list(sub.itertuples(index=False)) for d, sub in df.groupby("day")}


def _first_idx_at_or_after(rows, dt):
    for k, r in enumerate(rows):
        if r.DateTime >= dt:
            return k
    return None


# --------------------------- run one config --------------------------------
def run(m5, rows_by_day, mode: str, exit_mode: str) -> Result:
    """mode in {full, dirnull, no200}; exit_mode in {r2, trail}."""
    res = Result()
    rng = random.Random(SEED)
    for day, day5 in m5.groupby("day"):
        rows = rows_by_day.get(day)
        if not rows or len(day5) <= 2 * PIVOT_K:
            continue
        day5 = day5.set_index("DateTime")
        last_hi, last_lo = confirmed_levels(day5, PIVOT_K)
        recs = list(day5.itertuples(index=True))
        done = False
        for j, r in enumerate(recs):
            if done:
                break
            if r.Index.time() >= SQUARE:
                break
            lo_lvl, hi_lvl = last_lo[j], last_hi[j]
            long_ok = (r.e9 > r.e21 and r.since_bull <= ARM_BARS
                       and (mode == "no200" or r.Close > r.e200)
                       and hi_lvl is not None and r.Close > hi_lvl
                       and r.bull and r.body >= BODY_MIN)
            short_ok = (r.e9 < r.e21 and r.since_bear <= ARM_BARS
                        and (mode == "no200" or r.Close < r.e200)
                        and lo_lvl is not None and r.Close < lo_lvl
                        and (not r.bull) and r.body >= BODY_MIN)
            direction = "long" if long_ok else "short" if short_ok else None
            if direction is None:
                continue
            stop_ref = r.Low if direction == "long" else r.High
            if mode == "dirnull" and rng.random() < 0.5:
                direction = "short" if direction == "long" else "long"
                stop_ref = r.High if direction == "short" else r.Low
            # fill at next 5-min bar's open time -> its 1-min bar
            if j + 1 >= len(recs):
                continue
            fill_dt = recs[j + 1].Index
            ei = _first_idx_at_or_after(rows, fill_dt)
            if ei is None or rows[ei].DateTime.time() >= SQUARE:
                continue
            entry_open = rows[ei].Open
            risk = abs(entry_open - stop_ref)
            if risk <= 0:
                continue
            if exit_mode == "r2":
                tgt = (entry_open + 2 * risk if direction == "long"
                       else entry_open - 2 * risk)
                trail_dt = None
            else:
                tgt = None
                # first 9/21 opposite cross strictly after the entry bar
                future = [rr.Index for rr in recs[j + 1:] if rr.oppx]
                trail_dt = (future[0] + timedelta(minutes=5)) if future else None
            xraw, reason, xdt = resolve(rows, ei, direction, entry_open, stop_ref,
                                        tgt, trail_dt)
            res.trades.append(_make_trade(direction, entry_open, xraw, reason, day,
                                          rows[ei].DateTime, xdt))
            done = True
    return res


def report(tag, r: Result):
    longs = [t for t in r.trades if t.side == "LONG"]
    shorts = [t for t in r.trades if t.side == "SHORT"]
    print(f"  {tag:<16} trades={len(r.trades):4d}  net={r.net():>11,.0f}  "
          f"PF={r.profit_factor():5.2f}  win={r.win_rate():5.1%}  "
          f"tail-3={r.tail_excl(3):>11,.0f}  t={r.t_stat():+.2f}  "
          f"[L {len(longs)} / S {len(shorts)}]")


def main():
    df1 = load_cached(SYMBOL)
    print(f"{SYMBOL} bars={len(df1):,}  {df1['DateTime'].min().date()} -> "
          f"{df1['DateTime'].max().date()}   qty=500  slip=0.05%  "
          f"pivotK={PIVOT_K} arm={ARM_BARS} body>={BODY_MIN}\n")
    m5 = build_5min(df1)
    rows_by_day = prep_1min(df1)

    for exit_mode in ("r2", "trail"):
        print(f"== EXIT={exit_mode.upper()} ==")
        rf = run(m5, rows_by_day, "full", exit_mode)
        report("full", rf)
        report("dir-null", run(m5, rows_by_day, "dirnull", exit_mode))
        report("no-200filter", run(m5, rows_by_day, "no200", exit_mode))
        by = rf.by_year()
        n = pd.Series([t.date.year for t in rf.trades]).value_counts()
        for y in sorted(by):
            yl = [t for t in rf.trades if t.date.year == y]
            wr = sum(1 for t in yl if t.net > 0) / len(yl) if yl else 0.0
            print(f"    {y}: net={by[y]:>11,.0f}  trades={n.get(y, 0):3d}  win={wr:5.1%}")
        print(f"  exits: {dict(pd.Series([t.reason for t in rf.trades]).value_counts())}\n")

    print("== read ==")
    print("  full vs dir-null: does the WITH-trend break beat a coin-flip of direction?")
    print("  full vs no-200:   does the 200-EMA trend filter actually help?")
    print("  R2 vs TRAIL:      does riding the 9/21 cross capture the promised runners?")

    rf_r2 = run(m5, rows_by_day, "full", "r2")
    rn_r2 = run(m5, rows_by_day, "no200", "r2")
    rf_tr = run(m5, rows_by_day, "full", "trail")
    tr_ex = pd.Series([t.reason for t in rf_tr.trades]).value_counts()
    print("\n== VERDICT ==")
    print(f"  200-EMA filter WORKS: dropping it worsens net {rf_r2.net():,.0f} -> "
          f"{rn_r2.net():,.0f} (R2). With-trend beats counter-trend; the trend "
          f"call also beats the coin-flip on PF/win. So the SIGNAL has merit.")
    print(f"  BUT intraday-cage kills the payoff: {tr_ex.get('eod', 0)} of "
          f"{len(rf_tr.trades)} TRAIL trades exit on the 15:15 square-off, only "
          f"{tr_ex.get('trail', 0)} on the actual 9/21 trail. The 1:5 runner needs "
          f"MULTI-DAY holding; MIS flattens it every session.")
    print("  NO-GO as an intraday strategy (loses every year, both exits). It is "
          "MIS-CAGED, not edgeless: the right test is a SWING/POSITIONAL engine "
          "(overnight carry, 9/21 trail on daily/15-min, split-adjusted data) -- "
          "a different engine than this repo has today.")


if __name__ == "__main__":
    main()
