"""Run gap-fill arm F, arm R, and two nulls through the option engine ONCE.

Null 1 (timing): fade-side ATM, hold to EOD, no scale-out.
Null 2 (direction): the frozen exit on the CONTINUATION side (gap-up->CALL,
gap-down->PUT). A real edge must beat BOTH.

Reporting (spec §118-123): full-period + per-year + OOS split, plus a per-cell
breakout by SIDE x GAP SUB-BUCKET (incl. below-FLOOR / above-CAP diagnostic
buckets, populated by a floor=0/cap=1 diagnostic arm). Per cell: trades, net,
PF, win%, t-stat, avg debit (entry premium pts), avg net/trade, exit-reason mix.
Run: PYTHONPATH=. python3 analysis/gap_fill_fade_run.py
"""
from datetime import date, datetime, time

import numpy as np
import pandas as pd

from engine.backtest import Backtest, Knobs
from engine.data.catalog import Catalog
from engine.data.market import MarketData
from engine.data.spot import SpotSeries
from engine.execution.costs import CostModel
from engine.strategy.exit import Exit
from strategies.gap_fill_fade import GapFillFade, SQUARE
from strategies import gap_fill_params as P

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)
SPLIT = pd.Timestamp("2024-12-31")

# |gap| sub-buckets. The traded band is [FLOOR, CAP]; "<FLOOR" and ">CAP" are
# diagnostic (only populated by the floor=0/cap=1 diagnostic arm).
MID1, MID2 = 0.0015, 0.0030
BUCKET_ORDER = ["<FLOOR", "F-0.15%", "0.15-0.30%", "0.30%-CAP", ">CAP"]


def bucket_of(gap):
    """Map a signed gap to its |gap| sub-bucket label (None -> '?')."""
    if gap is None:
        return "?"
    a = abs(gap)
    if a < P.FLOOR:
        return "<FLOOR"
    if a > P.CAP:
        return ">CAP"
    if a < MID1:
        return "F-0.15%"
    if a < MID2:
        return "0.15-0.30%"
    return "0.30%-CAP"


def cell_stats(df):
    """Per-cell metrics from a trades frame (cols: net, entry, reason). None if empty."""
    n = len(df)
    if n == 0:
        return None
    net = df.net
    loss = -net[net <= 0].sum()
    pf = (net[net > 0].sum() / loss) if loss > 0 else float("inf")
    sd = net.std(ddof=1) if n > 1 else 0.0
    t = (net.mean() / (sd / np.sqrt(n))) if sd > 0 else 0.0
    return {
        "n": n, "net": net.sum(), "pf": pf, "win": (net > 0).mean(),
        "t": t, "avg_debit": df.entry.mean(), "avg_net": net.mean(),
        "exits": dict(df.reason.value_counts()),
    }


def gap_for(spot, d):
    """Reproduce the strategy's gap for date d: settled open (09:15 bar close) over
    prior-session close (ctx.prev_close == spot.close_before). None if unavailable."""
    dt = datetime.combine(d, time(9, 15))
    bar = spot.bar_at(dt)
    pc = spot.close_before(dt)
    if bar is None or pc in (None, 0):
        return None
    return bar.close / pc - 1.0


def trades_frame(res, spot):
    rows = []
    for t in res.trades:
        g = gap_for(spot, pd.Timestamp(t.date).date())
        rows.append({"date": pd.Timestamp(t.date), "net": t.net, "reason": t.reason,
                     "side": t.side, "entry": t.entry, "gap": g, "bucket": bucket_of(g)})
    return pd.DataFrame(rows)


class HoldNull(GapFillFade):
    """Timing null: same fade entry, but hold to EOD (no scale-out/stop)."""
    def manage(self, ctx, pos):
        return Exit.market("eod") if ctx.now.time() >= SQUARE else None


class ContinuationNull(GapFillFade):
    """Direction null: flip the fade side (bet continuation), frozen exit."""
    def _select(self, ctx):
        sel = super()._select(ctx)
        if sel is None:
            return None
        side, strike = sel
        return ("CALL" if side == "PUT" else "PUT"), strike


def _line(label, s):
    if len(s) == 0:
        print(f"    {label:<12} n=0"); return
    loss = -s.net[s.net <= 0].sum()
    pf = (s.net[s.net > 0].sum() / loss) if loss > 0 else float("inf")
    print(f"    {label:<12} n={len(s):>3} net={s.net.sum():>10,.0f} "
          f"PF={pf:5.2f} win={(s.net > 0).mean() * 100:4.0f}%")


def summarize(tag, res):
    tr = pd.DataFrame([{"date": pd.Timestamp(t.date), "net": t.net,
                        "reason": t.reason, "side": t.side} for t in res.trades])
    if tr.empty:
        print(f"  {tag}: no trades"); return
    print(f"  {tag}: n={len(tr)} net={res.net():,.0f} PF={res.profit_factor():.2f} "
          f"win={res.win_rate() * 100:.0f}% t={res.t_stat():+.2f} "
          f"exits={dict(tr.reason.value_counts())}")
    tr["yr"] = tr.date.dt.year
    for y, s in tr.groupby("yr"):
        _line(str(y), s)
    _line("TRAIN<=24", tr[tr.date <= SPLIT])
    _line("TEST 25-26", tr[tr.date > SPLIT])


def print_breakout(df, buckets=None):
    """Per-cell table: SIDE x GAP-BUCKET with full metrics (spec §118-123)."""
    buckets = buckets or [b for b in BUCKET_ORDER if b not in ("<FLOOR", ">CAP")]
    print(f"    {'side':<4} {'bucket':<11} {'n':>3} {'net':>10} {'PF':>5} "
          f"{'win':>4} {'t':>6} {'avgDebit':>8} {'avgNet':>8}  exits")
    for side in ("PUT", "CALL"):
        for b in buckets:
            c = cell_stats(df[(df.side == side) & (df.bucket == b)])
            if c is None:
                continue
            pf = "inf" if c["pf"] == float("inf") else f"{c['pf']:.2f}"
            print(f"    {side:<4} {b:<11} {c['n']:>3} {c['net']:>10,.0f} {pf:>5} "
                  f"{c['win']*100:>3.0f}% {c['t']:>+6.2f} {c['avg_debit']:>8.1f} "
                  f"{c['avg_net']:>8,.0f}  {c['exits']}")


def main():
    print("Loading spot + catalog ...")
    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    md = MarketData(cat, spot, min_dte=1)
    knobs = Knobs(fill_price="open", slippage_pct=0.0025)
    arms = {
        "ARM-F fade": GapFillFade(arm="fade"),
        "ARM-R reversal": GapFillFade(arm="reversal"),
        "NULL timing (hold)": HoldNull(arm="fade"),
        "NULL direction (cont)": ContinuationNull(arm="fade"),
    }
    for tag, strat in arms.items():
        bt = Backtest(md, CostModel(), knobs)          # md reused; loaded once
        res = bt.run(strat, START, END)
        print(f"\n== {tag} ==")
        summarize("FULL", res)
        df = trades_frame(res, spot)
        if not df.empty:
            print("  BY SIDE x GAP-BUCKET:")
            print_breakout(df)

    # Diagnostic arm: trade EVERY gap (floor=0, cap=1) so the below-FLOOR and
    # above-CAP buckets are populated -- shows whether FLOOR/CAP are well-placed.
    print("\n== DIAGNOSTIC fade (floor=0, cap=1) — is the FLOOR/CAP band right? ==")
    bt = Backtest(md, CostModel(), knobs)
    dres = bt.run(GapFillFade(arm="fade", floor=0.0, cap=1.0), START, END)
    ddf = trades_frame(dres, spot)
    if not ddf.empty:
        print_breakout(ddf, buckets=BUCKET_ORDER)      # includes <FLOOR and >CAP


if __name__ == "__main__":
    main()
