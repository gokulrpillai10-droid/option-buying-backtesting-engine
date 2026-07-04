"""Backtest driver — runs any code-first Strategy across all accurate data.

Per day: on_day_start (reset) -> iterate the signal-timeframe spot bars -> when flat,
on_bar(ctx) may return an Order (filled at B+timeframe, the fill contract, ARCH-1) ->
while open, manage(ctx, pos) may return an Exit -> EOD square-off at the day's last
option bar. Costs via CostModel; size via the date-keyed lot (engine-owned, never
strategy-supplied).

This is the first slice: enter -> hold -> Exit.market / EOD. Exit.at(level) intrabar
resolution and carry-forward are layered on in following increments.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import List, Optional

import pandas as pd

from engine.data.chain import Chain
from engine.execution.costs import CostModel
from engine.instruments import lot_size_for
from engine.position.manager import Position, SpreadLeg, SpreadPosition, Trade
from engine.strategy.base import BarContext
from engine.strategy.exit import resolve_level_exit


@dataclass
class Knobs:
    """Sensitivity dials (the reframe): fill timing, slippage, timeframe.

    ``fill_price`` selects which 1-min price the fill uses at B+timeframe:
    'open' (principled next-open) or 'close' (matches the G12 scripts' convention).
    """
    slippage_pct: float = 0.0
    timeframe: str = "5min"
    fill_price: str = "open"
    min_volume_mult: float = 0.0   # reject an entry if signal-bar option volume < this * order qty (0 = off)


@dataclass
class Result:
    trades: List[Trade] = field(default_factory=list)
    carry_forwards: int = 0          # data-quality: option-price gaps carried forward

    def net(self) -> float:
        return sum(t.net for t in self.trades)

    def win_rate(self) -> float:
        n = len(self.trades)
        return sum(1 for t in self.trades if t.net > 0) / n if n else 0.0

    def profit_factor(self) -> float:
        wins = sum(t.net for t in self.trades if t.net > 0)
        loss = -sum(t.net for t in self.trades if t.net <= 0)
        return (wins / loss) if loss else float("inf")

    def tail_excl(self, n: int) -> float:
        """Net with the top-n winners removed (tail-dependence check)."""
        top = sorted((t.net for t in self.trades), reverse=True)[:n]
        return self.net() - sum(top)

    def by_year(self) -> dict:
        out: dict = {}
        for t in self.trades:
            y = t.date.year
            out[y] = out.get(y, 0.0) + t.net
        return out

    def t_stat(self) -> float:
        """t-stat of mean per-trade return on premium deployed."""
        import math
        rs = [t.net / (t.entry * t.qty) for t in self.trades if t.entry and t.qty]
        n = len(rs)
        if n < 2:
            return 0.0
        mean = sum(rs) / n
        var = sum((r - mean) ** 2 for r in rs) / (n - 1)
        sd = math.sqrt(var)
        return (mean / (sd / math.sqrt(n))) if sd else 0.0


class Backtest:
    def __init__(self, market, cost_model: CostModel, knobs: Optional[Knobs] = None):
        self._market = market
        self._costs = cost_model
        self._k = knobs or Knobs()
        self._tf = pd.Timedelta(self._k.timeframe)

    def run(self, strategy, start: date, end: date) -> Result:
        result = Result()
        for _expiry, chains in self._market.stream(start, end):
            for day in sorted(chains):
                result.trades.extend(self._run_day(strategy, day, chains[day]))
        return result

    # ---- one day ----
    def _run_day(self, strategy, day: date, chain: Chain) -> List[Trade]:
        spot = self._market.spot()
        bars = [t for t in spot.timestamps() if t.to_pydatetime().date() == day]
        if not bars:
            return []
        slip = self._k.slippage_pct
        strategy.on_day_start(BarContext(now=bars[0].to_pydatetime(), chain=chain, spot=spot))

        trades: List[Trade] = []
        pos: Optional[Position] = None
        entry_dt = None
        peak = 0.0                                            # running premium peak (for trails)
        for b in bars:
            now = b.to_pydatetime()
            ctx = BarContext(now=now, chain=chain, spot=spot)
            if pos is None:
                orders = strategy.on_bar(ctx)
                if orders:
                    fill_dt = b + self._tf
                    if len(orders) > 1 or any(o.action == "sell" for o in orders):
                        pos = self._open_spread(orders, chain, fill_dt, day, slip)  # multi-leg
                        if pos is not None:
                            entry_dt = fill_dt.to_pydatetime()
                        continue
                    px = self._opt_price(chain, fill_dt, orders[0].strike, orders[0].side, self._k.fill_price)
                    if px is not None:
                        qty = orders[0].qty if orders[0].qty is not None else orders[0].lots * lot_size_for(day)
                        if self._volume_ok(chain, now, orders[0].strike, orders[0].side, qty):
                            pos = Position(orders[0].side, orders[0].strike, px * (1 + slip), qty)
                            entry_dt = fill_dt.to_pydatetime()
                            peak = px                          # premium peak starts at the raw fill
                        # else: signal-bar volume too thin to fill the order -> skip (no re-entry,
                        # the strategy already marked the day entered)
            elif isinstance(pos, SpreadPosition):              # ---- spread management ----
                ex = strategy.manage(ctx, pos)
                if ex is not None and ex.kind == "market":
                    xdt = b + self._tf
                    xprices = [self._opt_price(chain, xdt, lg.strike, lg.side, self._k.fill_price)
                               for lg in pos.legs]
                    if all(p is not None for p in xprices):
                        t = pos.close(xprices, self._costs, slip, ex.reason)
                        trades.append(replace(t, date=day, entry_dt=entry_dt,
                                              exit_dt=xdt.to_pydatetime()))
                        pos = None
            else:
                ex = strategy.manage(ctx, pos)
                if ex is None:
                    continue
                if ex.kind == "market":
                    xdt = b + self._tf
                    px = self._opt_price(chain, xdt, pos.strike, pos.side, self._k.fill_price)
                    if px is not None:
                        pos = self._book(pos, px, ex.reason, ex.fraction, slip,
                                         day, entry_dt, xdt.to_pydatetime(), trades)
                elif ex.kind == "level":             # single premium stop/target, intrabar, partial-aware
                    for xdt, o, h, l in self._opt_window(chain, b, b + self._tf, pos.strike, pos.side):
                        fill, hit = resolve_level_exit(ex, o, h, l)
                        if hit:
                            pos = self._book(pos, fill, ex.reason, ex.fraction, slip,
                                             day, entry_dt, xdt.to_pydatetime(), trades)
                            break
                elif ex.kind == "bracket":           # premium stop/target, intrabar on option bars
                    for xdt, o, h, l in self._opt_window(chain, b, b + self._tf, pos.strike, pos.side):
                        fill, reason, frac = ex.resolve(o, h, l)
                        if fill is not None:
                            pos = self._book(pos, fill, reason, frac, slip,
                                             day, entry_dt, xdt.to_pydatetime(), trades)
                            break
                elif ex.kind == "trail":             # premium trailing stop, intrabar peak
                    for xdt, o, h, l in self._opt_window(chain, b, b + self._tf, pos.strike, pos.side):
                        peak = max(peak, h)
                        stop = peak - ex.gap
                        if l <= stop:
                            fill = o if o < stop else stop    # gap-through -> worse open
                            pos = self._book(pos, fill, ex.reason, ex.fraction, slip,
                                             day, entry_dt, xdt.to_pydatetime(), trades)
                            break

        if isinstance(pos, SpreadPosition):                  # EOD square-off (spread)
            last = chain._df["DateTime"].max()
            xprices = [self._last_close(chain, lg.strike, lg.side) for lg in pos.legs]
            if all(p is not None for p in xprices):
                t = pos.close(xprices, self._costs, slip, reason="eod")
                trades.append(replace(t, date=day, entry_dt=entry_dt,
                                      exit_dt=pd.Timestamp(last).to_pydatetime()))
        elif pos is not None:                                # EOD square-off (single leg)
            last = chain._df["DateTime"].max()
            px = self._last_close(chain, pos.strike, pos.side)
            if px is not None:
                t = pos.close(px * (1 - slip), self._costs, reason="eod")
                trades.append(replace(t, date=day, entry_dt=entry_dt,
                                      exit_dt=pd.Timestamp(last).to_pydatetime()))
        return trades

    def _open_spread(self, orders, chain, fill_dt, day, slip):
        """Open a multi-leg SpreadPosition; each leg fills at fill_dt with adverse
        slip (buy UP, sell DOWN). Returns None if any leg can't be priced (all-or-none)."""
        legs = []
        for o in orders:
            px = self._opt_price(chain, fill_dt, o.strike, o.side, self._k.fill_price)
            if px is None:
                return None
            qty = o.qty if o.qty is not None else o.lots * lot_size_for(day)
            entry = px * (1 + slip) if o.action == "buy" else px * (1 - slip)
            legs.append(SpreadLeg(action=o.action, side=o.side, strike=o.strike,
                                  entry=entry, qty=qty))
        return SpreadPosition(legs=tuple(legs))

    def _volume_ok(self, chain: Chain, signal_dt, strike, side, qty) -> bool:
        """Liquidity floor: the signal-bar option volume must be at least
        ``min_volume_mult * qty`` to fill the order. Mirrors the validated audit
        (signal-minute volume; not-traded or missing strike -> 0 -> reject).
        Returns True when the floor is off (mult <= 0)."""
        mult = self._k.min_volume_mult
        if mult <= 0:
            return True
        snap = chain.snapshot_at(signal_dt)
        if strike not in snap.strikes():
            return False
        q = snap.call(strike) if side.upper() == "CALL" else snap.put(strike)
        vol = q.volume if q.is_traded else 0
        return vol >= mult * qty

    # ---- option price helpers ----
    @staticmethod
    def _opt_price(chain: Chain, dt, strike, side, which):
        snap = chain.snapshot_at(dt)
        if strike not in snap.strikes():
            return None
        q = snap.call(strike) if side.upper() == "CALL" else snap.put(strike)
        return q.open if which == "open" else q.close

    def _book(self, pos, fill_price, reason, fraction, slip, day, entry_dt, exit_dt, trades):
        """Close ``fraction`` of pos at fill_price; return the remaining Position
        (or None if fully closed). Enables partial scale-outs."""
        if fraction >= 1.0:
            t = pos.close(fill_price * (1 - slip), self._costs, reason=reason)
            trades.append(replace(t, date=day, entry_dt=entry_dt, exit_dt=exit_dt))
            return None
        close_qty = pos.qty * fraction
        t = pos.close(fill_price * (1 - slip), self._costs, reason=reason, qty=close_qty)
        trades.append(replace(t, date=day, entry_dt=entry_dt, exit_dt=exit_dt))
        return Position(pos.side, pos.strike, pos.entry, pos.qty - close_qty)

    @staticmethod
    def _opt_window(chain: Chain, start, end, strike, side):
        """Option 1-min bars (dt, open, high, low) in [start, end) for the held strike."""
        df = chain._df
        pfx = "Call" if side.upper() == "CALL" else "Put"
        m = ((df["DateTime"] >= start) & (df["DateTime"] < end) & (df["StrikePrice"] == strike))
        sub = df[m].sort_values("DateTime")
        return [(r["DateTime"], float(r[pfx + "Open"]), float(r[pfx + "High"]),
                 float(r[pfx + "Low"])) for _, r in sub.iterrows()]

    @staticmethod
    def _last_close(chain: Chain, strike, side):
        last = chain._df["DateTime"].max()                    # day's last option bar
        return Backtest._opt_price(chain, last, strike, side, "close")
