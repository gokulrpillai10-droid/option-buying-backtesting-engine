"""5-EMA deviation strategy on NIFTY, expressed as ITM option BUYING.

Power-of-Stocks rules mapped onto the engine (spot decides when, chain
decides at what price):
- PUT side (run on timeframe='5min'):  a 5m candle fully ABOVE the 5-EMA
  (low > ema, wicks included) arms a setup at (trigger=candle low,
  sl=candle high). A later candle trading below the trigger fires the
  signal -> buy an ITM PUT (strike = ceil(spot/50)*50 + itm_offset).
- CALL side (run on timeframe='15min'): mirror — candle fully BELOW the
  EMA arms (trigger=high, sl=low); break above buys an ITM CALL
  (floor(spot/50)*50 - itm_offset).
- Re-arm/disarm: each completed candle re-arms at its own extreme if it is
  again fully clear of the EMA, otherwise disarms (touching the EMA cancels
  the setup). Setups never carry overnight.
- Exits are SPOT-level, per the source strategy: stop when spot crosses the
  setup extreme, target at rr x risk from the trigger; EOD square-off.
  (Option P&L then follows via the engine's real per-minute premiums.)

Engine-contract note: the original enters on a resting stop order AT the
trigger; the engine signals at the breaking bar's close and fills the option
at the NEXT bar's open — one bar of honest latency. The EMA warms up from
the day's open (span-5 converges in ~10 bars); no signal before min_warmup
observations.
"""
import math
from datetime import time

from engine.execution.model import Order
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit

ALPHA = 2.0 / (5 + 1)


def itm_strike(spot, side, offset):
    """ITM strike ``offset`` points in the money, on the 50-point grid."""
    if side == "CALL":
        return int(math.floor(spot / 50) * 50 - offset)
    return int(math.ceil(spot / 50) * 50 + offset)


class Ema5Itm(Strategy):
    def __init__(self, side="PUT", itm_offset=100, rr=3.0, lots=1,
                 min_warmup=5, entry_end=time(15, 0), square=time(15, 10),
                 max_trades=None):
        assert side in ("PUT", "CALL")
        self.SIDE = side
        self.ITM = itm_offset
        self.RR = rr
        self.LOTS = lots
        self.WARM = min_warmup
        self.ENTRY_END, self.SQUARE = entry_end, square
        self.MAX_TRADES = max_trades

    def on_day_start(self, ctx):
        self.ema = None
        self.n_obs = 0
        self.armed = None                  # (trigger_level, spot_sl)
        self.spot_sl = self.spot_tgt = None
        self.n_trades = 0

    def _observe(self, bar):
        c = bar.close
        self.ema = c if self.ema is None else self.ema + ALPHA * (c - self.ema)
        self.n_obs += 1

    def _rearm(self, bar):
        """After a completed candle: re-arm if fully clear of the EMA."""
        if self.n_obs < self.WARM:
            self.armed = None
            return
        if self.SIDE == "PUT":             # short-signal: candle fully above
            self.armed = (bar.low, bar.high) if bar.low > self.ema else None
        else:                              # long-signal: candle fully below
            self.armed = (bar.high, bar.low) if bar.high < self.ema else None

    def on_bar(self, ctx):
        bar = ctx.spot_bar
        self._observe(bar)
        trig = None
        if (self.armed is not None and ctx.now.time() <= self.ENTRY_END
                and (self.MAX_TRADES is None or self.n_trades < self.MAX_TRADES)):
            level, sl = self.armed
            if self.SIDE == "PUT" and bar.low < level:
                trig = (level, sl)
            if self.SIDE == "CALL" and bar.high > level:
                trig = (level, sl)
        self._rearm(bar)
        if trig is None:
            return []
        level, sl = trig
        risk = abs(sl - level)
        self.spot_sl = sl
        self.spot_tgt = level - self.RR * risk if self.SIDE == "PUT" else level + self.RR * risk
        self.armed = None
        self.n_trades += 1
        return [Order(side=self.SIDE, strike=itm_strike(bar.close, self.SIDE, self.ITM),
                      lots=self.LOTS)]

    def manage(self, ctx, pos):
        bar = ctx.spot_bar
        self._observe(bar)                 # EMA stays continuous through trades
        self._rearm(bar)
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        if self.SIDE == "PUT":
            if bar.high >= self.spot_sl:
                return Exit.market("spot_sl")
            if bar.low <= self.spot_tgt:
                return Exit.market("spot_tgt")
        else:
            if bar.low <= self.spot_sl:
                return Exit.market("spot_sl")
            if bar.high >= self.spot_tgt:
                return Exit.market("spot_tgt")
        return None
