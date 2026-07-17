"""Cash-equity intraday backtest driver.

Mirrors the SHAPE of engine.backtest.Backtest._run_day (on_day_start -> on_bar
-> fill next-open -> manage -> level/market/EOD) but fills and exits read the
stock's OWN bars — no chain, no premium layer, and the proven options driver
is never imported. Reuses only instrument-agnostic pieces: SpotSeries (signal
+ fill feed in one), Result/Trade (pure data/metrics), and the shared
resolve_level_exit for honest intrabar level fills.

Day gates (spec 2026-07-03): skip sessions with fewer than min_bars_per_day
1-min bars (partial/Muhurat days) and days whose overnight gap exceeds
max_overnight_gap — Kite intraday history is NOT back-adjusted, so an
unadjusted bonus/split (HDFCBANK 1:1, Aug 2025) makes prior-day CPR levels
garbage; intraday-only means the only damage is a wasted signal day.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import List, Optional, Tuple

import pandas as pd

from engine.backtest import Result
from engine.data.spot import SpotSeries, _resample_ohlc
from engine.equity.context import EquityBarContext
from engine.equity.costs import EquityCostModel
from engine.equity.position import EquityPosition
from engine.position.manager import Trade
from engine.strategy.exit import resolve_level_exit


@dataclass
class EquityKnobs:
    slippage_pct: float = 0.0005      # ~HDFCBANK cash spread; NOT the options 0.25%
    timeframe: str = "5min"
    min_bars_per_day: int = 300       # of 375; skips partial/Muhurat sessions
    max_overnight_gap: float = 0.15   # skip unadjusted corporate-action days
    # stress dials (0 = off = historical behavior):
    slippage_range_k: float = 0.0     # extra slip = k * (fill-bar high-low)/mid — fast tape pays more
    entry_delay_bars: int = 0         # fill N extra strategy bars late (latency stress)


class EquityBacktest:
    def __init__(self, one_min_df: pd.DataFrame, cost_model: EquityCostModel,
                 knobs: Optional[EquityKnobs] = None):
        self._costs = cost_model
        self._k = knobs or EquityKnobs()
        self._tf = pd.Timedelta(self._k.timeframe)
        df = one_min_df.sort_values("DateTime").reset_index(drop=True)
        self._m1_by_day = {d: sub for d, sub in df.groupby(df["DateTime"].dt.date)}
        self._m1 = SpotSeries(df)
        self._spot = SpotSeries(_resample_ohlc(df, self._k.timeframe, "left"))
        self._bars_by_day: dict = {}
        for ts in self._spot.timestamps():
            self._bars_by_day.setdefault(ts.to_pydatetime().date(), []).append(ts)
        self.skipped_days: List[Tuple[date, str]] = []   # (day, reason) quality log

    def run(self, strategy, start: Optional[date] = None,
            end: Optional[date] = None) -> Result:
        self.skipped_days = []
        result = Result()
        for day in sorted(self._m1_by_day):
            if (start and day < start) or (end and day > end):
                continue
            result.trades.extend(self._run_day(strategy, day))
        return result

    # ---- one day ----
    def _run_day(self, strategy, day: date) -> List[Trade]:
        m1 = self._m1_by_day[day]
        if len(m1) < self._k.min_bars_per_day:
            self.skipped_days.append((day, "min_bars"))
            return []
        first = m1.iloc[0]
        prev_close = self._m1.close_before(first["DateTime"])
        if (prev_close is not None
                and abs(float(first["Open"]) / prev_close - 1.0)
                > self._k.max_overnight_gap):
            self.skipped_days.append((day, "overnight_gap"))
            return []

        bars = self._bars_by_day.get(day, [])
        if not bars:
            return []
        strategy.on_day_start(EquityBarContext(now=bars[0].to_pydatetime(),
                                               spot=self._spot))
        trades: List[Trade] = []
        pos: Optional[EquityPosition] = None
        entry_dt = None
        for b in bars:
            now = b.to_pydatetime()
            ctx = EquityBarContext(now=now, spot=self._spot)
            if pos is None:
                orders = strategy.on_bar(ctx)
                if orders:
                    if len(orders) > 1:
                        raise ValueError(
                            f"equity v1 takes one order per bar (got {len(orders)})")
                    o = orders[0]
                    fb = self._spot.bar_at(b + self._tf * (1 + self._k.entry_delay_bars))
                    # signal on the day's last bar has no next bar -> dropped (no fill)
                    if fb is not None:
                        mb = self._m1.bar_at(fb.dt)          # fill-minute range for slip
                        hi, lo = (mb.high, mb.low) if mb is not None else (None, None)
                        fill = self._entry_fill(fb.open, o.direction, hi, lo)
                        qty = o.qty if o.qty is not None else int(o.capital // fill)
                        if qty > 0:
                            pos = EquityPosition(direction=o.direction,
                                                 entry=fill, qty=qty)
                            entry_dt = fb.dt
            else:
                ex = strategy.manage(ctx, pos)
                if ex is None:
                    continue
                if ex.fraction != 1.0:
                    raise ValueError("equity v1 has no partial exits "
                                     f"(got fraction={ex.fraction})")
                if ex.kind == "market":
                    fb = self._spot.bar_at(b + self._tf)
                    # no next bar on the day's last bar -> exit dropped here; falls
                    # through to the EOD square-off below
                    if fb is not None:
                        mb = self._m1.bar_at(fb.dt)          # fill-minute range for slip
                        hi, lo = (mb.high, mb.low) if mb is not None else (None, None)
                        t = pos.close(self._exit_fill(fb.open, pos.direction, hi, lo),
                                      self._costs, reason=ex.reason)
                        trades.append(replace(t, date=day, entry_dt=entry_dt,
                                              exit_dt=fb.dt))
                        pos = None
                elif ex.kind == "level":
                    for xdt, o_, h_, l_ in self._m1_window(m1, b, b + self._tf):
                        fill, hit = resolve_level_exit(ex, o_, h_, l_)
                        if hit:
                            t = pos.close(self._exit_fill(fill, pos.direction, h_, l_),
                                          self._costs, reason=ex.reason)
                            trades.append(replace(t, date=day, entry_dt=entry_dt,
                                                  exit_dt=xdt.to_pydatetime()))
                            pos = None
                            break

        if pos is not None:                              # EOD square-off (MIS)
            last = m1.iloc[-1]
            t = pos.close(self._exit_fill(float(last["Close"]), pos.direction,
                                          float(last["High"]), float(last["Low"])),
                          self._costs, reason="eod")
            trades.append(replace(t, date=day, entry_dt=entry_dt,
                                  exit_dt=last["DateTime"].to_pydatetime()))
        return trades

    # ---- fills: adverse slippage by direction ----
    def _eff_slip(self, high: Optional[float], low: Optional[float]) -> float:
        """Base slip plus range-scaled friction at the fill bar (fast tape
        costs more). slippage_range_k=0 -> flat base (historical behavior)."""
        s = self._k.slippage_pct
        k = self._k.slippage_range_k
        if k > 0 and high is not None and low is not None:
            mid = (high + low) / 2.0
            if mid > 0:
                s += k * (high - low) / mid
        return s

    def _entry_fill(self, raw: float, direction: str,
                    high: Optional[float] = None, low: Optional[float] = None) -> float:
        s = self._eff_slip(high, low)
        return raw * (1 + s) if direction == "long" else raw * (1 - s)

    def _exit_fill(self, raw: float, direction: str,
                   high: Optional[float] = None, low: Optional[float] = None) -> float:
        s = self._eff_slip(high, low)
        return raw * (1 - s) if direction == "long" else raw * (1 + s)

    @staticmethod
    def _m1_window(m1: pd.DataFrame, start, end):
        """1-min bars (dt, open, high, low) in [start, end) — intrabar exits."""
        m = (m1["DateTime"] >= start) & (m1["DateTime"] < end)
        return [(r["DateTime"], float(r["Open"]), float(r["High"]), float(r["Low"]))
                for _, r in m1[m].iterrows()]
