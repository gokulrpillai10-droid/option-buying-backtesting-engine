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

    ``intrabar`` bounds the unknowable order of events INSIDE a 1-min bar:
    'mixed' (default, the historical behavior) folds the bar high into a trail's
    peak before checking its low (optimistic) but checks a bracket's stop before
    its target (pessimistic). 'pessimistic' also checks the trail low against the
    PRIOR bar's peak; 'optimistic' also lets a bracket's target win the tie.
    Run a strategy under both extremes and read the spread as the error band —
    a GO verdict should survive 'pessimistic'.

    ``spread_rs`` models the bid-ask spread as a fixed ₹ amount per fill:
    added to the entry price (buy at ask) and subtracted from the exit price
    (sell at bid).  Use with ``intrabar='pessimistic'`` for a full friction scenario.
    Conservative calibration estimates (pending live-fill audit):
        ATM option  ~₹8     (deep liquid; less on quiet days)
        1-OTM       ~₹5
        2+ OTM      ~₹2
    Applied to single-leg fills only; spread-position legs are unaffected until
    live calibration confirms per-leg spread sizes.
    """
    slippage_pct: float = 0.0
    timeframe: str = "5min"
    fill_price: str = "open"
    min_volume_mult: float = 0.0   # reject an entry if signal-bar option volume < this * order qty (0 = off)
    intrabar: str = "mixed"        # 'mixed' | 'optimistic' | 'pessimistic' (see docstring)
    # state-dependent friction (0/1 = off = flat slippage_pct, the historical behavior):
    slippage_range_k: float = 0.0  # extra slip = k * (fill-bar high-low)/mid — fast markets pay more
    thin_exit_mult: float = 1.0    # multiply slip when EXIT-bar volume < min_volume_mult * qty
    spread_rs: float = 0.0         # fixed ₹ half-spread per fill side (buy pays +, sell receives -)


@dataclass
class Result:
    trades: List[Trade] = field(default_factory=list)
    carry_forwards: int = 0          # data-quality: option-price gaps carried forward
    missed_fills: int = 0            # data-quality: orders that could not be filled
    missed_fill_log: List[dict] = field(default_factory=list)

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
        if self._k.intrabar not in ("mixed", "optimistic", "pessimistic"):
            raise ValueError(f"intrabar must be mixed/optimistic/pessimistic, "
                             f"got {self._k.intrabar!r}")
        self._tf = pd.Timedelta(self._k.timeframe)

    def run(self, strategy, start: date, end: date) -> Result:
        result = Result()
        for expiry, chains in self._market.stream(start, end):
            for day in sorted(chains):
                self._run_day(strategy, expiry, day, chains[day], result)
        return result

    def _lot(self, expiry, day) -> int:
        """Contract lot for sizing: the traded EXPIRY's authoritative lot when the
        market exposes one (``market.lot_size``), else the day-keyed schedule."""
        fn = getattr(self._market, "lot_size", None)
        if fn is not None:
            v = fn(expiry)
            if v is not None:
                return v
        return lot_size_for(day)

    # ---- one day ----
    def _run_day(self, strategy, expiry, day: date, chain: Chain, result: Result) -> None:
        spot = self._market.spot()
        bars = [t for t in spot.timestamps() if t.to_pydatetime().date() == day]
        if not bars:
            return
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
                        pos = self._open_spread(orders, chain, fill_dt, expiry, day, slip)  # multi-leg
                        if pos is not None:
                            entry_dt = fill_dt.to_pydatetime()
                            for o in orders:
                                strategy.on_fill(o)
                        else:
                            for o in orders:
                                strategy.on_order_rejected(o, "no_quote_spread")
                            result.missed_fills += 1          # 1 per attempt, not per leg
                            result.missed_fill_log.append(
                                {"day": day, "reason": "no_quote_spread",
                                 "strike": None, "side": None})
                        continue
                    q = self._opt_quote(chain, fill_dt, orders[0].strike, orders[0].side)
                    if q is not None:
                        px = q.open if self._k.fill_price == "open" else q.close
                        qty = orders[0].qty if orders[0].qty is not None else orders[0].lots * self._lot(expiry, day)
                        if self._volume_ok(chain, now, orders[0].strike, orders[0].side, qty):
                            eslip = self._eff_slip(q.high, q.low)   # entry qty gated by _volume_ok
                            pos = Position(orders[0].side, orders[0].strike,
                                           px * (1 + eslip) + self._k.spread_rs, qty)
                            entry_dt = fill_dt.to_pydatetime()
                            peak = px                          # premium peak starts at the raw fill
                            strategy.on_fill(orders[0])
                        else:
                            strategy.on_order_rejected(orders[0], "volume")
                            result.missed_fills += 1
                            result.missed_fill_log.append(
                                {"day": day, "reason": "volume",
                                 "strike": orders[0].strike, "side": orders[0].side})
                    else:
                        strategy.on_order_rejected(orders[0], "no_quote")
                        result.missed_fills += 1
                        result.missed_fill_log.append(
                            {"day": day, "reason": "no_quote",
                             "strike": orders[0].strike, "side": orders[0].side})
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
                    q = self._opt_quote(chain, xdt, pos.strike, pos.side)
                    if q is not None:
                        px = q.open if self._k.fill_price == "open" else q.close
                        eslip = self._eff_slip(q.high, q.low, q.volume, pos.qty * ex.fraction)
                        pos = self._book(pos, px, ex.reason, ex.fraction, eslip,
                                         day, entry_dt, xdt.to_pydatetime(), trades)
                elif ex.kind == "level":             # single premium stop/target, intrabar, partial-aware
                    for xdt, o, h, l, v in self._opt_window(chain, b, b + self._tf, pos.strike, pos.side):
                        fill, hit = resolve_level_exit(ex, o, h, l)
                        if hit:
                            eslip = self._eff_slip(h, l, v, pos.qty * ex.fraction)
                            pos = self._book(pos, fill, ex.reason, ex.fraction, eslip,
                                             day, entry_dt, xdt.to_pydatetime(), trades)
                            break
                elif ex.kind == "spot_level":        # spot-level exit, crossing found on 1-min SPOT
                    for sb in spot.minute_window(b, b + self._tf):
                        crossed = (sb.low <= ex.level if ex.trigger == "fall"
                                   else sb.high >= ex.level)
                        if not crossed:
                            continue
                        xdt = pd.Timestamp(sb.dt) + pd.Timedelta("1min")   # option fills next 1-min open
                        q = self._opt_quote(chain, xdt, pos.strike, pos.side)
                        if q is not None:
                            eslip = self._eff_slip(q.high, q.low, q.volume, pos.qty * ex.fraction)
                            pos = self._book(pos, q.open, ex.reason, ex.fraction, eslip,
                                             day, entry_dt, xdt.to_pydatetime(), trades)
                        break                        # quote missing -> re-arms next strategy bar
                elif ex.kind == "bracket":           # premium stop/target, intrabar on option bars
                    optimistic = self._k.intrabar == "optimistic"
                    for xdt, o, h, l, v in self._opt_window(chain, b, b + self._tf, pos.strike, pos.side):
                        fill, reason, frac = ex.resolve(o, h, l, optimistic=optimistic)
                        if fill is None:
                            continue
                        eslip = self._eff_slip(h, l, v, pos.qty * frac)
                        pos = self._book(pos, fill, reason, frac, eslip,
                                         day, entry_dt, xdt.to_pydatetime(), trades)
                        if pos is None:
                            break
                        ex = ex.consume(reason)      # partial: the other side keeps resting this window
                elif ex.kind == "trail":             # premium trailing stop, intrabar peak
                    pess = self._k.intrabar == "pessimistic"
                    for xdt, o, h, l, v in self._opt_window(chain, b, b + self._tf, pos.strike, pos.side):
                        if not pess:
                            peak = max(peak, h)      # optimistic/mixed: this bar's high came first
                        armed = ex.arm is None or peak >= ex.arm
                        stop = peak - ex.gap
                        if armed and l <= stop:
                            fill = o if o < stop else stop    # gap-through -> worse open
                            eslip = self._eff_slip(h, l, v, pos.qty * ex.fraction)
                            pos = self._book(pos, fill, ex.reason, ex.fraction, eslip,
                                             day, entry_dt, xdt.to_pydatetime(), trades)
                            break
                        peak = max(peak, h)          # pessimistic: high folds in only after the low check
                elif ex.kind == "two_stage":         # engine-owned policy: in-window scale->trail handoff
                    pess = self._k.intrabar == "pessimistic"
                    optimistic = self._k.intrabar == "optimistic"
                    trailing = ex.scaled(pos)
                    br = None if trailing else ex.exit_for(pos)      # the stage-1 bracket
                    for xdt, o, h, l, v in self._opt_window(chain, b, b + self._tf, pos.strike, pos.side):
                        if not trailing:
                            fill, reason, frac = br.resolve(o, h, l, optimistic=optimistic)
                            if fill is None:
                                continue
                            eslip = self._eff_slip(h, l, v, pos.qty * frac)
                            pos = self._book(pos, fill, reason, frac, eslip,
                                             day, entry_dt, xdt.to_pydatetime(), trades)
                            if pos is None:
                                break
                            if reason == ex.target_reason:           # scale-out booked -> trail NOW
                                trailing = True
                                peak = fill          # the trail references the scale-out fill onward
                            else:
                                br = br.consume(reason)
                            continue                 # this bar's range is consumed by the fill
                        if not pess:
                            peak = max(peak, h)
                        stop = peak - ex.trail_gap
                        if l <= stop:
                            fill = o if o < stop else stop
                            eslip = self._eff_slip(h, l, v, pos.qty)
                            pos = self._book(pos, fill, ex.trail_reason, 1.0, eslip,
                                             day, entry_dt, xdt.to_pydatetime(), trades)
                            break
                        peak = max(peak, h)

        if isinstance(pos, SpreadPosition):                  # EOD square-off (spread)
            last = chain._df["DateTime"].max()
            xprices = [self._last_close(chain, lg.strike, lg.side) for lg in pos.legs]
            if all(p is not None for p in xprices):
                t = pos.close(xprices, self._costs, slip, reason="eod")
                trades.append(replace(t, date=day, entry_dt=entry_dt,
                                      exit_dt=pd.Timestamp(last).to_pydatetime()))
        elif pos is not None:                                # EOD square-off (single leg)
            last = chain._df["DateTime"].max()
            q = self._opt_quote(chain, last, pos.strike, pos.side)
            if q is not None:
                eslip = self._eff_slip(q.high, q.low, q.volume, pos.qty)
                t = pos.close(q.close * (1 - eslip) - self._k.spread_rs, self._costs, reason="eod")
                trades.append(replace(t, date=day, entry_dt=entry_dt,
                                      exit_dt=pd.Timestamp(last).to_pydatetime()))
        result.trades.extend(trades)

    def _open_spread(self, orders, chain, fill_dt, expiry, day, slip):
        """Open a multi-leg SpreadPosition; each leg fills at fill_dt with adverse
        slip (buy UP, sell DOWN). Returns None if any leg can't be priced (all-or-none).

        Naked short guard: per option side, sold qty must not exceed bought qty.
        Covered verticals are fine; NET-short exposure (unbounded loss, margin,
        assignment) is not modeled — refuse loudly instead of pricing it wrong."""
        net = {"CALL": 0.0, "PUT": 0.0}
        for o in orders:
            qty = o.qty if o.qty is not None else o.lots * self._lot(expiry, day)
            net[o.side.upper()] += qty if o.action == "buy" else -qty
        if any(q < 0 for q in net.values()):
            raise ValueError("naked short option legs are not modeled "
                             "(margin/assignment): each side's sold qty must be "
                             f"covered by bought qty, got net {net}")
        legs = []
        for o in orders:
            px = self._opt_price(chain, fill_dt, o.strike, o.side, self._k.fill_price)
            if px is None:
                return None
            qty = o.qty if o.qty is not None else o.lots * self._lot(expiry, day)
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
    def _opt_quote(chain: Chain, dt, strike, side):
        snap = chain.snapshot_at(dt)
        if strike not in snap.strikes():
            return None
        return snap.call(strike) if side.upper() == "CALL" else snap.put(strike)

    @staticmethod
    def _opt_price(chain: Chain, dt, strike, side, which):
        q = Backtest._opt_quote(chain, dt, strike, side)
        if q is None:
            return None
        return q.open if which == "open" else q.close

    def _eff_slip(self, high=None, low=None, volume=None, qty=None) -> float:
        """Effective slippage for one fill: the flat base, plus range-scaled
        friction (fast bars cost more), times the thin-exit penalty when the
        fill bar can't absorb the qty. All extras default off -> flat base."""
        s = self._k.slippage_pct
        k = self._k.slippage_range_k
        if k > 0 and high is not None and low is not None:
            mid = (high + low) / 2.0
            if mid > 0:
                s += k * (high - low) / mid
        if (self._k.thin_exit_mult > 1.0 and self._k.min_volume_mult > 0
                and volume is not None and qty is not None
                and volume < self._k.min_volume_mult * qty):
            s *= self._k.thin_exit_mult
        return s

    def _book(self, pos, fill_price, reason, fraction, slip, day, entry_dt, exit_dt, trades):
        """Close ``fraction`` of pos at fill_price; return the remaining Position
        (or None if fully closed). Enables partial scale-outs."""
        net_px = fill_price * (1 - slip) - self._k.spread_rs
        if fraction >= 1.0:
            t = pos.close(net_px, self._costs, reason=reason)
            trades.append(replace(t, date=day, entry_dt=entry_dt, exit_dt=exit_dt))
            return None
        close_qty = pos.qty * fraction
        t = pos.close(net_px, self._costs, reason=reason, qty=close_qty)
        trades.append(replace(t, date=day, entry_dt=entry_dt, exit_dt=exit_dt))
        return Position(pos.side, pos.strike, pos.entry, pos.qty - close_qty)

    @staticmethod
    def _opt_window(chain: Chain, start, end, strike, side):
        """Option 1-min bars (dt, open, high, low, volume) in [start, end) for the held strike."""
        df = chain._df
        pfx = "Call" if side.upper() == "CALL" else "Put"
        m = ((df["DateTime"] >= start) & (df["DateTime"] < end) & (df["StrikePrice"] == strike))
        sub = df[m].sort_values("DateTime")
        return [(r["DateTime"], float(r[pfx + "Open"]), float(r[pfx + "High"]),
                 float(r[pfx + "Low"]), float(r[pfx + "Volume"])) for _, r in sub.iterrows()]

    @staticmethod
    def _last_close(chain: Chain, strike, side):
        last = chain._df["DateTime"].max()                    # day's last option bar
        return Backtest._opt_price(chain, last, strike, side, "close")
