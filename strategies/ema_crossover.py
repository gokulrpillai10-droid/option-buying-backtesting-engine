"""EMA 9/21 crossover, 5-min, intraday option buying (user spec).

Entry: EMA9 crosses EMA21 on 5-min spot -> bullish=buy ATM CALL, bearish=buy ATM PUT.
Entry window [entry_start, entry_end]. Exit: a premium bracket (either +/-Rs pnl_exit,
or +/-exit_points premium points if given), resolved intrabar on the option (stop checked
first = conservative), else square-off at square_off. 1 lot.

The EMAs are updated EVERY bar via _observe (called from on_bar when flat AND manage
when in a trade -- never both on the same bar), so the indicator stays continuous across
positions and warms up from market open (09:15) even though entries start later. Cross is
acted on only when flat.
"""
from datetime import time

from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit

A9, A21 = 2 / (9 + 1), 2 / (21 + 1)


class EMACrossover(Strategy):
    LOTS = 1
    MIN_DTE = 3

    def __init__(self, pnl_exit=2000.0, exit_points=None,
                 stop_points=None, target_points=None, itm_offset=0, lots=1, max_trades=None,
                 use_vwap=False,
                 entry_start=time(9, 40), entry_end=time(14, 58), square_off=time(15, 10)):
        self.PNL_EXIT = pnl_exit            # +/- rupees premium bracket (used if no points given)
        self.EXIT_POINTS = exit_points      # symmetric +/- premium POINTS bracket
        self.STOP_POINTS = stop_points      # asymmetric stop (premium points below entry); overrides above
        self.TARGET_POINTS = target_points  # asymmetric target (premium points above entry)
        self.ITM_OFFSET = itm_offset        # points IN the money: CALL strike=ATM-offset, PUT=ATM+offset
        self.LOTS = lots
        self.MAX_TRADES = max_trades        # cap entries per day (None = unlimited)
        self.USE_VWAP = use_vwap            # gate: only CALL if spot>VWAP, only PUT if spot<VWAP
        self.ENTRY_START = entry_start
        self.ENTRY_END = entry_end
        self.SQUARE_OFF = square_off

    def on_day_start(self, ctx):
        self.ema9 = None
        self.ema21 = None
        self.prev_sign = None
        self._crossed = False
        self._dir = 0
        self._n_trades = 0                  # entries taken today
        self._vwap_pv = 0.0                 # cumulative typical price (equal-weighted, no index volume)
        self._vwap_n = 0
        self.vwap = None

    def _observe(self, bar):
        close = bar.close
        # anchored equal-weighted VWAP proxy: cumulative mean of typical price (H+L+C)/3
        self._vwap_pv += (bar.high + bar.low + bar.close) / 3.0
        self._vwap_n += 1
        self.vwap = self._vwap_pv / self._vwap_n
        if self.ema9 is None:
            self.ema9 = self.ema21 = close
        else:
            self.ema9 += A9 * (close - self.ema9)
            self.ema21 += A21 * (close - self.ema21)
        diff = self.ema9 - self.ema21
        sign = 1 if diff > 0 else (-1 if diff < 0 else 0)
        self._crossed = (self.prev_sign is not None and sign != 0 and sign != self.prev_sign)
        self._dir = sign
        if sign != 0:
            self.prev_sign = sign

    def on_bar(self, ctx):
        bar = ctx.spot_bar
        self._observe(bar)
        if not (self.ENTRY_START <= ctx.now.time() <= self.ENTRY_END):
            return []
        if self.MAX_TRADES is not None and self._n_trades >= self.MAX_TRADES:
            return []
        if self._crossed:
            side = "CALL" if self._dir > 0 else "PUT"
            if self.USE_VWAP and self.vwap is not None:        # trade only in VWAP's direction
                if side == "CALL" and bar.close <= self.vwap:
                    return []
                if side == "PUT" and bar.close >= self.vwap:
                    return []
            atm = ctx.atm_strike(side)
            if atm is None:
                return []
            strike = atm - self.ITM_OFFSET if side == "CALL" else atm + self.ITM_OFFSET
            self._n_trades += 1
            return [Order(side=side, strike=strike, lots=self.LOTS)]
        return []

    def manage(self, ctx, pos):
        self._observe(ctx.spot_bar)                    # keep EMA + VWAP continuous
        if ctx.now.time() >= self.SQUARE_OFF:
            return Exit.market("eod")
        if self.STOP_POINTS is not None or self.TARGET_POINTS is not None:
            sp = self.STOP_POINTS if self.STOP_POINTS is not None else self.TARGET_POINTS
            tp = self.TARGET_POINTS if self.TARGET_POINTS is not None else self.STOP_POINTS
        else:
            sp = tp = self.EXIT_POINTS if self.EXIT_POINTS is not None else self.PNL_EXIT / pos.qty
        return Exit.bracket(pos.entry - sp, pos.entry + tp, "stop", "target")
