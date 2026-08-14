"""T1 — two-sided straddle thermometer for the buy/sell regime detector.

The GO gate: does SHORTING a straddle on calm/SELL days (theta) and BUYING one
on stormy/BUY days (gamma) actually pay, net of costs, OOS, vs nulls? This module
is validation-only -- it does NOT route live orders.

P&L reuses the engine's proven ``SpreadPosition`` (a straddle is a 2-leg position;
short-straddle math is first-class there), so costs + adverse slippage are the
same primitives the backtest is trusted on.

Spec: docs/superpowers/specs/2026-08-13-regime-buysell-detector-design.md
"""
from __future__ import annotations

import sys

from engine.execution.costs import CostModel
from engine.position.manager import SpreadLeg, SpreadPosition, Trade


def atm_strike(snapshot, ref_price: float):
    """Nearest strike to ``ref_price`` where BOTH the call and put are traded,
    or ``None`` if no strike has both legs. A straddle needs both legs fillable at
    the same strike, so a strike missing one side is skipped (not silently used).
    """
    best, best_dist = None, None
    for k in snapshot.strikes():
        if not (snapshot.call(k).is_traded and snapshot.put(k).is_traded):
            continue
        dist = abs(k - ref_price)
        if best_dist is None or dist < best_dist:
            best, best_dist = k, dist
    return best


def straddle_from_snapshots(entry_snap, exit_snap, ref_price: float, qty: int,
                            direction: str, cost_model: CostModel, slip: float = 0.0):
    """Assemble one straddle ``Trade`` from an entry and an exit chain snapshot of
    the SAME expiry. Returns ``None`` (skip, never crash) if there is no ATM strike
    with both legs traded at entry, or that strike is not tradeable at exit.
    """
    strike = atm_strike(entry_snap, ref_price)
    if strike is None:
        return None
    if strike not in exit_snap.strikes():
        return None
    xc, xp = exit_snap.call(strike), exit_snap.put(strike)
    if not (xc.is_traded and xp.is_traded):
        return None
    ec, ep = entry_snap.call(strike), entry_snap.put(strike)
    return straddle_trade(strike, ec.close, ep.close, xc.close, xp.close,
                          qty, direction, cost_model, slip)


def summarize(trades) -> dict:
    """Aggregate a set of straddle ``Trade``s into thermometer stats.

    ``profit_factor`` = gross wins / |gross losses|; defined as 0.0 when there are
    no losses AND no wins (empty), avoiding inf/NaN. All figures are net of costs.
    """
    n = len(trades)
    net = sum(t.net for t in trades)
    wins = [t.net for t in trades if t.net > 0]
    losses = [t.net for t in trades if t.net < 0]
    gross_loss = -sum(losses)
    pf = (sum(wins) / gross_loss) if gross_loss > 0 else 0.0
    return {
        "n": n,
        "net": net,
        "wins": len(wins),
        "win_rate": (len(wins) / n) if n else 0.0,
        "profit_factor": pf,
    }


def direction_for(stable_low_vol: bool, high_vol: bool):
    """Map a day's vol state to the straddle side, or ``None`` (stand aside).

    Stormy (``high_vol``) -> ``'long'`` (buy gamma); calm (``stable_low_vol``) ->
    ``'short'`` (sell theta). The two are mutually exclusive on the data, but if
    they ever collide, danger wins -- go LONG vol rather than short into a spike.
    """
    if high_vol:
        return "long"
    if stable_low_vol:
        return "short"
    return None


def straddle_trade(strike: int, entry_call: float, entry_put: float,
                   exit_call: float, exit_put: float, qty: int,
                   direction: str, cost_model: CostModel, slip: float = 0.0) -> Trade:
    """One ATM straddle round-trip as a costed ``Trade``.

    ``direction='long'`` buys both legs (profits when premium rises = gamma);
    ``'short'`` sells both legs (profits when premium falls = theta). Adverse
    slippage + the India F&O cost card are applied via ``SpreadPosition.close``.
    """
    action = "buy" if direction == "long" else "sell"
    legs = (
        SpreadLeg(action=action, side="CALL", strike=strike, entry=entry_call, qty=qty),
        SpreadLeg(action=action, side="PUT", strike=strike, entry=entry_put, qty=qty),
    )
    return SpreadPosition(legs=legs).close(
        exit_prices=(exit_call, exit_put), cost_model=cost_model, slip=slip,
        reason=f"straddle_{direction}",
    )


# ------------------------------------------------------------------ #
# Real-data driver (research entry point; pure helpers above are tested)
# ------------------------------------------------------------------ #
ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
NIFTY_CSV = "analysis/nifty_daily.csv"
DATA_END = "2026-02-25"          # option chain ends here
HOLD_MIN_DTE = 21                # enter a contract with >=21 DTE, hold to its expiry
SLIP = 0.0025


def _episode_entries(labels):
    """One entry per regime episode: (entry_date, direction). calm->short (theta),
    stormy->long (gamma). Episodes are the independent statistical unit."""
    from analysis.regime_finder import episodes
    out = []
    for s, _e, _d in episodes(labels["stable_low_vol"]):
        out.append((s.date(), "short"))
    for s, _e, _d in episodes(labels["high_vol"]):
        out.append((s.date(), "long"))
    return sorted(out)


def _eod_snap(day_df):
    """(snapshot, dt) at the session's last minute for a single-day chain frame."""
    from engine.data.chain import Chain
    if day_df.empty:
        return None
    eod = day_df["DateTime"].max()
    return Chain(day_df).snapshot_at(eod)


def _collect_episode_snapshots(entries, md, cat):
    """For each episode, resolve its held contract and return
    (entry_date, regime_dir, entry_snap, exit_snap, ref_spot, qty). Skips (logs)
    episodes with no contract/day/data."""
    import pandas as pd
    from engine.instruments import lot_size_for

    closes = pd.read_csv(NIFTY_CSV, parse_dates=["date"], index_col="date")["close"]
    cache = {}
    rows, skipped = [], 0
    for entry_date, direction in entries:
        E = md.expiry_for(entry_date)
        if E is None:
            skipped += 1; continue
        e_iso = E.isoformat()
        path = cat.path_for(e_iso)
        if path is None:
            skipped += 1; continue
        if e_iso not in cache:
            df = pd.read_parquet(path)
            df["DateTime"] = pd.to_datetime(df["DateTime"])
            df["_d"] = df["DateTime"].dt.date
            cache[e_iso] = df
        df = cache[e_iso]
        entry_df = df[df["_d"] == entry_date].drop(columns="_d")
        if entry_df.empty:
            skipped += 1; continue
        exit_date = df["_d"].max()                      # contract's last traded day (~expiry)
        exit_df = df[df["_d"] == exit_date].drop(columns="_d")
        entry_snap, exit_snap = _eod_snap(entry_df), _eod_snap(exit_df)
        ref = closes.get(pd.Timestamp(entry_date))
        if entry_snap is None or exit_snap is None or ref is None:
            skipped += 1; continue
        rows.append((entry_date, direction, entry_snap, exit_snap, float(ref),
                     lot_size_for(entry_date)))
    return rows, skipped


def _price_side(rows, side_fn, cost):
    """Price every episode under a side policy ``side_fn(regime_dir, i)->'long'/'short'``."""
    trades = []
    for i, (d, rdir, en, ex, ref, qty) in enumerate(rows):
        t = straddle_from_snapshots(en, ex, ref, qty, side_fn(rdir, i), cost, SLIP)
        if t is not None:
            trades.append((d, rdir, t))
    return trades


def main():
    import pandas as pd
    from engine.data.catalog import Catalog
    from engine.data.market import MarketData
    from engine.data.spot import SpotSeries
    from analysis.regime_finder import label_regimes

    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    md = MarketData(cat, spot, min_dte=HOLD_MIN_DTE)
    closes = pd.read_csv(NIFTY_CSV, parse_dates=["date"], index_col="date")["close"]
    labels = label_regimes(closes).loc[:DATA_END]
    entries = _episode_entries(labels)
    rows, skipped = _collect_episode_snapshots(entries, md, cat)
    cost = CostModel()

    print(f"STRADDLE THERMOMETER — regime GO gate  (hold to >= {HOLD_MIN_DTE}-DTE expiry, "
          f"slip {SLIP:.2%}, window .. {DATA_END})")
    print(f"episodes: {len(entries)} labeled, {len(rows)} priced, {skipped} skipped\n")

    # regime strategy: calm->short, stormy->long
    strat = _price_side(rows, lambda rdir, i: rdir, cost)
    sells = [t for _d, rd, t in strat if rd == "short"]
    buys = [t for _d, rd, t in strat if rd == "long"]

    def line(name, trades):
        s = summarize(trades)
        print(f"  {name:<16} n={s['n']:>3}  net={s['net']:>12,.0f}  "
              f"win%={s['win_rate']:>5.0%}  PF={s['profit_factor']:>4.2f}")

    print("REGIME STRATEGY (theta on calm, gamma on storm):")
    line("SELL/short (calm)", sells)
    line("BUY/long (storm)", buys)
    line("COMBINED", [t for _d, _rd, t in strat])

    print("\nNULLS (same episodes, forced side):")
    line("always-long", [t for _d, _rd, t in _price_side(rows, lambda rd, i: "long", cost)])
    line("always-short", [t for _d, _rd, t in _price_side(rows, lambda rd, i: "short", cost)])
    line("random", [t for _d, _rd, t in _price_side(
        rows, lambda rd, i: "long" if (i % 2 == 0) else "short", cost)])

    # split-half (episodes by entry year)
    print("\nSPLIT-HALF (combined regime strategy):")
    early = [t for d, _rd, t in strat if d.year <= 2022]
    late = [t for d, _rd, t in strat if d.year >= 2023]
    line("2020-2022", early)
    line("2023-2026", late)

    # sample guard
    print(f"\nSAMPLE GUARD (>=3 episodes AND >=... per side): "
          f"SELL n={len(sells)}, BUY n={len(buys)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
