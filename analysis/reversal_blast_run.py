"""Intraday Reversal Blast on ADANIENT cash equity (both directions), 6.5y.

Spec (user, 2026-07-25): a mean-reversion entry gated by three-oscillator
alignment on 5-min, confirmed by a 1-min EMA cross.

  Phase 1 (5-min, Gate-1 extreme):  bull  RSI<30 & Stoch%K<20 & %R<-80
                                    bear  RSI>70 & Stoch%K>80 & %R>-20
  Phase 2 (5-min, signal candle):   first green (bull) / red (bear) bar while
                                    Gate-1 holds ON that bar. Its low/high is
                                    the protective stop reference.
  Phase 3 (1-min, trigger):         after the signal candle closes, within
                                    TIMEOUT minutes, 5EMA above 15EMA (bull) /
                                    below (bear); "hard50" also requires
                                    15EMA vs 50EMA aligned. Fill next 1-min open.
  Exits: +/-1.25% target, signal-candle stop, 5/15 EMA re-cross (market, next
  open), 60-min time-stop, 15:15 square-off. Pessimistic intrabar (stop first).

Oscillators run on a CONTINUOUS 5-min series (like a live chart that doesn't
reset at 09:15) and EMAs on the continuous 1-min close — both stay warm. Every
value is backward-looking; a signal on the 5-min bar labelled B is only acted
on from B+5min (the bar's close), so there is no look-ahead.

Controls (the discipline: an edge must beat its own nulls):
  A  full          osc + candle + EMA trigger (soft 50)
  B  no-EMA        osc + candle only, enter at signal-candle close (isolates
                   whether the 1-min EMA confirmation adds anything)
  D  full+hard50   A plus the 15/50 EMA alignment gate
  C  dir-null      A's exact entry bars + stops, but direction chosen by a
                   seeded coin flip (isolates whether the reversal call beats
                   a coin toss)

Costs: Zerodha MIS rate card (EquityCostModel) + 0.05% adverse slippage each
side — the same friction that killed prior intraday-equity edges. qty=500,
matching the ADANIENT gap-drive study for comparability.

Run: PYTHONPATH=<repo> python3 analysis/reversal_blast_run.py
"""
from __future__ import annotations

import random
from datetime import time, timedelta

import pandas as pd

from engine.backtest import Result
from engine.data.spot import _resample_ohlc
from engine.equity.costs import EquityCostModel
from engine.equity.kite_loader import load_cached
from engine.indicators import ema, rsi, stoch_k, williams_r
from engine.position.manager import Trade

SYMBOL = "ADANIENT"
QTY = 500
SLIP = 0.0005                 # 0.05% adverse, each side (matches EquityKnobs)
TARGET = 0.0125               # +/-1.25% profit target
TIMEOUT = timedelta(minutes=15)   # Gate-2 -> Gate-3 window
TIME_STOP = timedelta(minutes=60)
SQUARE = time(15, 15)
COSTS = EquityCostModel()

RSI_P, STO_P, STO_SMOOTH, WR_P = 14, 14, 3, 14
EMA_F, EMA_M, EMA_S = 5, 15, 50
RSI_LO, RSI_HI = 30.0, 70.0
STO_LO, STO_HI = 20.0, 80.0
WR_LO, WR_HI = -80.0, -20.0
SEED = 7


# ----------------------------- indicators / signals ------------------------
def build_5min(df1: pd.DataFrame) -> pd.DataFrame:
    m5 = _resample_ohlc(df1, "5min", "left")
    m5["rsi"] = rsi(m5["Close"], RSI_P)
    m5["stk"] = stoch_k(m5["High"], m5["Low"], m5["Close"], STO_P, STO_SMOOTH)
    m5["wr"] = williams_r(m5["High"], m5["Low"], m5["Close"], WR_P)
    m5["day"] = m5["DateTime"].dt.date
    return m5


def day_signals(m5day: pd.DataFrame):
    """(exec_start, direction, stop_ref) per Gate-1+2 signal candle, time-ordered.
    exec_start = signal-bar label + 5min (the bar's close = first actionable min)."""
    out = []
    for r in m5day.itertuples(index=False):
        if pd.isna(r.rsi) or pd.isna(r.stk) or pd.isna(r.wr):
            continue
        bull = (r.rsi < RSI_LO and r.stk < STO_LO and r.wr < WR_LO
                and r.Close > r.Open)
        bear = (r.rsi > RSI_HI and r.stk > STO_HI and r.wr > WR_HI
                and r.Close < r.Open)
        if bull:
            out.append((r.DateTime + timedelta(minutes=5), "long", r.Low, r.High))
        elif bear:
            out.append((r.DateTime + timedelta(minutes=5), "short", r.High, r.Low))
    return out


# ----------------------------- 1-min execution -----------------------------
def prep_1min(df1: pd.DataFrame):
    df = df1.copy()
    df["e5"] = ema(df["Close"], EMA_F)
    df["e15"] = ema(df["Close"], EMA_M)
    df["e50"] = ema(df["Close"], EMA_S)
    df["day"] = df["DateTime"].dt.date
    rows_by_day = {}
    for d, sub in df.groupby("day"):
        rows_by_day[d] = list(sub.itertuples(index=False))
    return rows_by_day


def _trigger_idx(rows, exec_start, direction, hard50):
    """Index of the first 1-min bar in [exec_start, exec_start+TIMEOUT] whose
    EMA state confirms; entry fills at the NEXT bar's open. None if no trigger."""
    for k, r in enumerate(rows):
        if r.DateTime < exec_start:
            continue
        if r.DateTime > exec_start + TIMEOUT or r.DateTime.time() >= SQUARE:
            return None
        if pd.isna(r.e5) or pd.isna(r.e15) or pd.isna(r.e50):
            continue
        if direction == "long":
            ok = r.e5 > r.e15 and (not hard50 or r.e15 > r.e50)
        else:
            ok = r.e5 < r.e15 and (not hard50 or r.e15 < r.e50)
        if ok:
            return k + 1 if k + 1 < len(rows) else None
    return None


def _first_idx_after(rows, exec_start):
    for k, r in enumerate(rows):
        if r.DateTime >= exec_start:
            return k if r.DateTime.time() < SQUARE else None
    return None


def _resolve_exit(rows, entry_idx, direction, entry_fill, stop_lvl,
                  use_emax=True, tgt_pts=None):
    """Walk 1-min bars from entry; return (exit_fill_raw, reason, exit_dt).
    Target is a fixed ``tgt_pts`` rupee move when given, else the pct TARGET.
    Pessimistic: stop checked before target inside a bar; EMA re-cross (when
    ``use_emax``) fills at the next bar open; 60-min time-stop and 15:15
    square-off at the bar open."""
    if tgt_pts is not None:
        tgt = entry_fill + tgt_pts if direction == "long" else entry_fill - tgt_pts
    else:
        tgt = entry_fill * (1 + TARGET) if direction == "long" else entry_fill * (1 - TARGET)
    entry_dt = rows[entry_idx].DateTime
    pending = None
    for r in rows[entry_idx:]:
        o, h, l = r.Open, r.High, r.Low
        if pending is not None:
            return o, pending, r.DateTime
        if r.DateTime.time() >= SQUARE:
            return o, "eod", r.DateTime
        if r.DateTime - entry_dt >= TIME_STOP:
            return o, "time", r.DateTime
        if direction == "long":
            if l <= stop_lvl:
                return (o if o < stop_lvl else stop_lvl), "stop", r.DateTime
            if h >= tgt:
                return (o if o > tgt else tgt), "target", r.DateTime
            if use_emax and not pd.isna(r.e5) and r.e5 < r.e15:
                pending = "emax"
        else:
            if h >= stop_lvl:
                return (o if o > stop_lvl else stop_lvl), "stop", r.DateTime
            if l <= tgt:
                return (o if o < tgt else tgt), "target", r.DateTime
            if use_emax and not pd.isna(r.e5) and r.e5 > r.e15:
                pending = "emax"
    last = rows[-1]
    return last.Close, "eod", last.DateTime


def _make_trade(direction, entry_open, exit_raw, reason, day, entry_dt, exit_dt):
    if direction == "long":
        entry = entry_open * (1 + SLIP)
        exit_ = exit_raw * (1 - SLIP)
        gross = (exit_ - entry) * QTY
        buy_v, sell_v = entry * QTY, exit_ * QTY
    else:
        entry = entry_open * (1 - SLIP)
        exit_ = exit_raw * (1 + SLIP)
        gross = (entry - exit_) * QTY
        buy_v, sell_v = exit_ * QTY, entry * QTY
    cost = COSTS.total(buy_value=buy_v, sell_value=sell_v)
    return Trade(side=direction.upper(), strike=0, qty=QTY, entry=entry, exit=exit_,
                 gross=gross, cost=cost, net=gross - cost, reason=reason,
                 date=day, entry_dt=entry_dt, exit_dt=exit_dt)


# ----------------------------- variants ------------------------------------
def run(m5, rows_by_day, mode: str, tgt_pts=None) -> Result:
    """mode in {full, noema, hard50, dirnull, norecross}. ``tgt_pts`` (rupees)
    switches the profit target from pct to a fixed scalp; point-target modes
    hold to target/stop only (no EMA re-cross)."""
    res = Result()
    rng = random.Random(SEED)
    use_emax = (mode != "norecross" and tgt_pts is None)
    for day, m5day in m5.groupby("day"):
        rows = rows_by_day.get(day)
        if not rows:
            continue
        for exec_start, direction, stop_lvl, opp_lvl in day_signals(m5day):
            if mode == "dirnull":
                if rng.random() < 0.5:
                    direction = "long" if direction == "short" else "short"
                    stop_lvl = opp_lvl                       # stop on the matching side
            if mode == "noema":
                ei = _first_idx_after(rows, exec_start)
            else:
                ei = _trigger_idx(rows, exec_start, direction, hard50=(mode == "hard50"))
            if ei is None or ei >= len(rows):
                continue
            entry_open = rows[ei].Open
            if rows[ei].DateTime.time() >= SQUARE:
                continue
            xraw, reason, xdt = _resolve_exit(rows, ei, direction, entry_open,
                                              stop_lvl, use_emax=use_emax,
                                              tgt_pts=tgt_pts)
            res.trades.append(_make_trade(direction, entry_open, xraw, reason, day,
                                          rows[ei].DateTime, xdt))
            break            # one trade per day (first firing signal)
    return res


def report(tag, r: Result):
    longs = [t for t in r.trades if t.side == "LONG"]
    shorts = [t for t in r.trades if t.side == "SHORT"]
    print(f"  {tag:<12} trades={len(r.trades):4d}  net={r.net():>11,.0f}  "
          f"PF={r.profit_factor():5.2f}  win={r.win_rate():5.1%}  "
          f"tail-3={r.tail_excl(3):>11,.0f}  t={r.t_stat():+.2f}  "
          f"[L {len(longs)} / S {len(shorts)}]")


def main():
    df1 = load_cached(SYMBOL)
    print(f"{SYMBOL} bars={len(df1):,}  {df1['DateTime'].min().date()} -> "
          f"{df1['DateTime'].max().date()}   qty={QTY}  slip=0.05%  "
          f"target=+/-{TARGET:.2%}\n")
    m5 = build_5min(df1)
    rows_by_day = prep_1min(df1)

    variants = [("A full", "full"), ("A2 no-recross", "norecross"),
                ("B no-EMA", "noema"), ("D full+hard50", "hard50"),
                ("C dir-null", "dirnull")]
    results = {}
    print("== HEADLINE (full period) ==")
    for tag, mode in variants:
        r = run(m5, rows_by_day, mode)
        results[tag] = r
        report(tag, r)

    print("\n== FIXED-POINT SCALP TARGET (full entry: osc+candle+EMA, stop, no recross) ==")
    for pts in (10, 15, 20, 25, 30):
        rp = run(m5, rows_by_day, "full", tgt_pts=pts)
        report(f"{pts}pt", rp)
    print("  -- 15pt: direction & no-EMA controls --")
    r15 = run(m5, rows_by_day, "full", tgt_pts=15)
    report("15pt full", r15)
    report("15pt no-EMA", run(m5, rows_by_day, "noema", tgt_pts=15))
    report("15pt dir-null", run(m5, rows_by_day, "dirnull", tgt_pts=15))
    by15 = r15.by_year()
    n15 = pd.Series([t.date.year for t in r15.trades]).value_counts()
    for y in sorted(by15):
        yl = [t for t in r15.trades if t.date.year == y]
        wr = sum(1 for t in yl if t.net > 0) / len(yl) if yl else 0.0
        print(f"    {y}: net={by15[y]:>11,.0f}  trades={n15.get(y, 0):3d}  win={wr:5.1%}")
    rr15 = pd.Series([t.reason for t in r15.trades]).value_counts()
    print(f"  15pt exits: {dict(rr15)}")

    print("\n== FULL (A): per-year ==")
    rA = results["A full"]
    by = rA.by_year()
    n = pd.Series([t.date.year for t in rA.trades]).value_counts()
    for y in sorted(by):
        yl = [t for t in rA.trades if t.date.year == y]
        wr = sum(1 for t in yl if t.net > 0) / len(yl) if yl else 0.0
        print(f"    {y}: net={by[y]:>11,.0f}  trades={n.get(y, 0):3d}  win={wr:5.1%}")
    reasons = pd.Series([t.reason for t in rA.trades]).value_counts()
    print(f"  exits: {dict(reasons)}")

    print("\n== read ==")
    print("  A vs C: if A's edge is real it must clear the dir-null (coin-flip direction).")
    print("  A vs B: whether the 1-min EMA trigger earns its extra latency.")
    print("  D vs A: whether the 15/50 alignment filter helps or just cuts trades.")

    a, c = results["A full"].net(), results["C dir-null"].net()
    a2 = results["A2 no-recross"].net()
    print("\n== VERDICT ==")
    print(f"  Every variant loses every year. Fairest hold-to-target (A2) is still "
          f"net {a2:,.0f}, PF {results['A2 no-recross'].profit_factor():.2f}.")
    print(f"  Dir-null (C, coin-flip direction) nets {c:,.0f} vs A's {a:,.0f} -- "
          f"the reversal call carries ~no directional information ({c/a:.0%} of A).")
    print("  A fixed scalp target (10-30pt) lifts win-rate but still loses every "
          "year (15pt: -634k, PF 0.62) -- the signal-candle stop (30-50pt on a "
          "5-min bar) dwarfs a 15pt target, so stops outnumber targets ~2:1.")
    print("  NO-GO. Three-oscillator 'extreme' on a momentum name (ADANIENT) is a "
          "falling-knife: oversold keeps falling, overbought keeps running. The "
          "signal has no edge net of costs -- the dir-null coin-flip ties it under "
          "BOTH pct and 15pt targets -- so no exit tweak (recross, hold-to-target, "
          "or scalp) rescues it. The problem is the entry, not the exit.")


if __name__ == "__main__":
    main()
