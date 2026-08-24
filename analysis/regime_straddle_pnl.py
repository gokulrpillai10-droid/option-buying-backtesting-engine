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

import math
import sys

import pandas as pd

from engine.execution.costs import CostModel
from engine.position.manager import SpreadLeg, SpreadPosition, Trade

from analysis.regime_data import load_closes
from engine.data.integrity import scoped_day


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


def vix_percentile_side(pct, threshold: float = 0.5):
    """VIX-percentile-only null: pick the straddle side from raw VIX rank alone,
    the naive one-signal proxy for the vol-state regime. High VIX percentile ->
    ``'long'`` (proxy for stormy/gamma), low -> ``'short'`` (calm/theta), split at
    ``threshold``. ``None`` when ``pct`` is missing/NaN (stand aside, don't force a
    side during the percentile warm-up)."""
    if pct is None or (isinstance(pct, float) and math.isnan(pct)):
        return None
    return "long" if pct >= threshold else "short"


def block_shuffle(seq, block_size: int, rng):
    """Return ``seq`` reordered in contiguous blocks: split into blocks of
    ``block_size``, shuffle the block ORDER (within-block order kept), flatten.
    Preserves local structure and the multiset of values while destroying global
    timing alignment -- a stricter null than i.i.d. shuffling for a calendar with
    autocorrelated regimes."""
    seq = list(seq)
    blocks = [seq[i:i + block_size] for i in range(0, len(seq), block_size)]
    rng.shuffle(blocks)
    return [x for b in blocks for x in b]


def sample_guard(n_pnl: int, n_episodes: int, min_pnl: int = 30, min_episodes: int = 3):
    """Spec §104: a bucket may drive the verdict only with ``>= min_pnl`` P&L
    observations AND ``>= min_episodes`` distinct episodes (power + independence).
    Returns ``(ok, reason)``."""
    if n_pnl < min_pnl:
        return False, f"INSUFFICIENT: {n_pnl} P&L < {min_pnl}"
    if n_episodes < min_episodes:
        return False, f"INSUFFICIENT: {n_episodes} episodes < {min_episodes}"
    return True, f"eligible ({n_pnl} P&L, {n_episodes} episodes)"


def straddle_trade(strike: int, entry_call: float, entry_put: float,
                   exit_call: float, exit_put: float, qty: int,
                   direction: str, cost_model: CostModel, slip: float = 0.0) -> Trade:
    """One ATM straddle round-trip as a costed ``Trade``.

    ``direction='long'`` buys both legs (profits when premium rises = gamma);
    ``'short'`` sells both legs (profits when premium falls = theta). Adverse
    slippage + the India F&O cost card are applied via ``SpreadPosition.close``.

    ``SpreadLeg.entry`` must be ALREADY entry-slipped by the caller (the engine
    convention -- ``close`` only slips the exit), so we pre-slip here: a buy pays
    UP, a sell receives DOWN. Skipping this drops ~half the round-trip slippage.
    """
    action = "buy" if direction == "long" else "sell"
    entry_slip = (1 + slip) if action == "buy" else (1 - slip)
    legs = (
        SpreadLeg(action=action, side="CALL", strike=strike,
                  entry=entry_call * entry_slip, qty=qty),
        SpreadLeg(action=action, side="PUT", strike=strike,
                  entry=entry_put * entry_slip, qty=qty),
    )
    return SpreadPosition(legs=legs).close(
        exit_prices=(exit_call, exit_put), cost_model=cost_model, slip=slip,
        reason=f"straddle_{direction}",
    )


# ------------------------------------------------------------------ #
# Real-data driver (research entry point; pure helpers above are tested)
# ------------------------------------------------------------------ #
ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
DATA_END = "2026-02-25"          # option chain ends here
HOLD_MIN_DTE = 21                # enter a contract with >=21 DTE, hold to its expiry
SLIP = 0.0025


def _episode_entries(labels):
    """One entry per regime episode: (entry_date, direction). calm->short (theta),
    stormy->long (gamma). Episodes are the independent statistical unit.

    Labels are LAGGED (``regime_finder.lagged``) so entry falls on the next
    executable session -- a live trader acts on yesterday's close-based label, not
    the same-day EOD. Sides route through ``direction_for`` (single source of truth
    for the vol-state -> side mapping)."""
    from analysis.regime_finder import episodes, lagged
    lab = lagged(labels)
    out = []
    for s, _e, _d in episodes(lab["stable_low_vol"]):
        out.append((s.date(), direction_for(stable_low_vol=True, high_vol=False)))
    for s, _e, _d in episodes(lab["high_vol"]):
        out.append((s.date(), direction_for(stable_low_vol=False, high_vol=True)))
    return sorted(out)


def _eod_snap(day_df):
    """(snapshot at the session's last minute) for a single-day chain frame.
    Delegates to the shared helper (DRY with the OI study)."""
    from analysis.chain_daily import eod_snapshot
    return eod_snapshot(day_df)


def _collect_episode_snapshots(entries, md, cat):
    """For each episode, resolve its held contract and return
    (entry_date, regime_dir, entry_snap, exit_snap, ref_spot, qty). Skips (logs)
    episodes with no contract/day/data."""
    from engine.instruments import lot_size_for

    closes = load_closes()
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
            cache[e_iso] = df
        df = cache[e_iso]
        entry_df = scoped_day(df, entry_date)          # guarded: single-day + session invariants
        if entry_df.empty:
            skipped += 1; continue
        exit_date = df["DateTime"].dt.date.max()        # contract's last traded day (~expiry)
        exit_df = scoped_day(df, exit_date)
        entry_snap, exit_snap = _eod_snap(entry_df), _eod_snap(exit_df)
        ref = closes.get(pd.Timestamp(entry_date))
        if entry_snap is None or exit_snap is None or ref is None:
            skipped += 1; continue
        rows.append((entry_date, direction, entry_snap, exit_snap, float(ref),
                     lot_size_for(entry_date)))
    return rows, skipped


def _price_side(rows, side_fn, cost):
    """Price every episode under a side policy ``side_fn(regime_dir, i)->'long'/'short'``.
    A ``None`` side means stand aside (the VIX-percentile null during warm-up)."""
    trades = []
    for i, (d, rdir, en, ex, ref, qty) in enumerate(rows):
        side = side_fn(rdir, i)
        if side is None:
            continue
        t = straddle_from_snapshots(en, ex, ref, qty, side, cost, SLIP)
        if t is not None:
            trades.append((d, rdir, t))
    return trades


def main():
    import random

    from engine.data.catalog import Catalog
    from engine.data.market import MarketData
    from engine.data.spot import SpotSeries
    from analysis.regime_finder import label_regimes
    from analysis.regime_data import load_vix

    cat = Catalog(ROOT)
    spot = SpotSeries.from_index(ROOT, "5min")
    md = MarketData(cat, spot, min_dte=HOLD_MIN_DTE)
    closes = load_closes()
    labels = label_regimes(closes).loc[:DATA_END]
    entries = _episode_entries(labels)
    rows, skipped = _collect_episode_snapshots(entries, md, cat)
    cost = CostModel()

    # regime strategy: calm->short, stormy->long (price first so the header can
    # report the ACTUAL priced count -- rows with data can still drop for no
    # ATM/exit leg inside _price_side).
    strat = _price_side(rows, lambda rdir, i: rdir, cost)

    print(f"STRADDLE THERMOMETER — regime GO gate  (hold to >= {HOLD_MIN_DTE}-DTE expiry, "
          f"slip {SLIP:.2%}, window .. {DATA_END})")
    print(f"episodes: {len(entries)} labeled, {len(rows)} with data, "
          f"{len(strat)} priced, {skipped} skipped (no contract/day/spot)\n")
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

    print("\nNULLS (same episodes, forced side) — regime must beat ALL five:")
    line("always-long", [t for _d, _rd, t in _price_side(rows, lambda rd, i: "long", cost)])
    line("always-short", [t for _d, _rd, t in _price_side(rows, lambda rd, i: "short", cost)])
    line("random", [t for _d, _rd, t in _price_side(
        rows, lambda rd, i: "long" if (i % 2 == 0) else "short", cost)])
    # VIX-percentile-only: side from the expanding VIX percentile at entry (the
    # naive one-signal proxy for the vol-state call; look-ahead safe, warm-up -> skip)
    vixpct = load_vix().expanding(min_periods=252).rank(pct=True, method="max")
    vix_pct_ep = [vixpct.get(pd.Timestamp(r[0])) for r in rows]
    line("vix-pctile-only", [t for _d, _rd, t in _price_side(
        rows, lambda rd, i: vix_percentile_side(vix_pct_ep[i]), cost)])
    # block-shuffled regime calendar: keep the long/short mix + block structure,
    # scramble the timing alignment (stricter than i.i.d. random)
    shuffled = block_shuffle([r[1] for r in rows], block_size=4, rng=random.Random(12345))
    line("block-shuffled", [t for _d, _rd, t in _price_side(
        rows, lambda rd, i: shuffled[i], cost)])

    # split-half (episodes by entry year)
    print("\nSPLIT-HALF (combined regime strategy):")
    early = [t for d, _rd, t in strat if d.year <= 2022]
    late = [t for d, _rd, t in strat if d.year >= 2023]
    line("2020-2022", early)
    line("2023-2026", late)

    # sample guard (spec §104): a side is eligible to drive the verdict only with
    # >=30 P&L observations AND >=3 distinct episodes.
    sell_eps = {d for d, rd, _t in strat if rd == "short"}
    buy_eps = {d for d, rd, _t in strat if rd == "long"}
    print("\nSAMPLE GUARD (spec §104: >=30 P&L AND >=3 episodes per side):")
    for name, trades, eps in (("SELL/short", sells, sell_eps), ("BUY/long", buys, buy_eps)):
        _ok, reason = sample_guard(len(trades), len(eps))
        print(f"  {name:<12} {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
