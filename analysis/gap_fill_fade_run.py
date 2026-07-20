"""Run gap-fill arm F, arm R, and two nulls through the option engine ONCE.

Null 1 (timing): fade-side ATM, hold to EOD, no scale-out.
Null 2 (direction): the frozen exit on the CONTINUATION side (gap-up->CALL,
gap-down->PUT). A real edge must beat BOTH.
Report per-year and OOS-split (train 2020-24 vs test 2025-26).
Run: PYTHONPATH=. python3 analysis/gap_fill_fade_run.py
"""
from datetime import date

import pandas as pd

from engine.backtest import Backtest, Knobs
from engine.data.catalog import Catalog
from engine.data.market import MarketData
from engine.data.spot import SpotSeries
from engine.execution.costs import CostModel
from engine.strategy.exit import Exit
from strategies.gap_fill_fade import GapFillFade, SQUARE

ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = date(2020, 1, 1), date(2026, 12, 31)
SPLIT = pd.Timestamp("2024-12-31")


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
        flipped = "CALL" if side == "PUT" else "PUT"
        return flipped, strike


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


if __name__ == "__main__":
    main()
