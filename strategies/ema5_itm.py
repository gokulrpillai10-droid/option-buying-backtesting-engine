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
- premium_tgt variant: when set, the spot target is replaced by a PREMIUM
  target — exit when the option is up ``premium_tgt`` points over the entry
  fill (resolved intrabar on 1-min option bars). Spot stop and EOD unchanged;
  on a bar where both stop and target ranges hit, the stop wins (conservative).
- prem_trail variant ``(arm_pts, gap_pts)``: no fixed target — once the
  premium has captured ``arm_pts`` over entry, a ``gap_pts`` trailing stop on
  the premium takes over (peak tracked intrabar from entry by the engine).
  Until armed, the spot stop is the only risk exit; EOD unchanged.
- scale_trail variant ``(tgt_pts, gap_pts, fraction)``: two-phase — book
  ``fraction`` of the position at entry + ``tgt_pts`` premium (intrabar level
  exit), then trail the REMAINDER with a ``gap_pts`` premium trailing stop
  (live from the bar after the scale-out; its peak references highs from
  activation on, seeded at the entry fill — the scale-out bar's own high is
  not in it). Spot stop stays the full-size disaster exit throughout.
- prem_sl: replaces the SPOT stop with a PREMIUM stop ``prem_sl`` points
  below the entry fill (intrabar). Requires premium_tgt or scale_trail (it
  pairs with the target as a bracket: full-size stop + target, stop checked
  first per bar; after a partial target fill the stop KEEPS RESTING for the
  rest of that bar's window). In the scale_trail trail phase the trail
  itself is the stop — its entry-seeded peak puts a floor at entry -
  gap_pts, ratcheting up with new highs. The scale_trail two-stage policy
  is the shared engine ``TwoStage`` (same machinery as cpr_breakout).

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
from engine.strategy.exit import Exit, TwoStage

ALPHA = 2.0 / (5 + 1)


def itm_strike(spot, side, offset):
    """ITM strike ``offset`` points in the money, on the 50-point grid."""
    if side == "CALL":
        return int(math.floor(spot / 50) * 50 - offset)
    return int(math.ceil(spot / 50) * 50 + offset)


class Ema5Itm(Strategy):
    LOTS = 1
    MIN_DTE = 3

    def __init__(self, side="PUT", itm_offset=100, rr=3.0, lots=1,
                 min_warmup=5, entry_end=time(15, 0), square=time(15, 10),
                 max_trades=None, premium_tgt=None, prem_trail=None,
                 scale_trail=None, prem_sl=None):
        assert side in ("PUT", "CALL")
        assert sum(x is not None for x in (premium_tgt, prem_trail, scale_trail)) <= 1
        # prem_sl needs a premium target scheme (bracket partner); the armed
        # trail keeps the spot stop while dormant, so it can't take one.
        assert prem_sl is None or premium_tgt is not None or scale_trail is not None
        self.SIDE = side
        self.ITM = itm_offset
        self.RR = rr
        self.LOTS = lots
        self.WARM = min_warmup
        self.ENTRY_END, self.SQUARE = entry_end, square
        self.MAX_TRADES = max_trades
        self.PREM_TGT = premium_tgt
        self.PREM_TRAIL = prem_trail               # (arm_pts, gap_pts) or None
        self.PREM_SL = prem_sl                     # premium stop, points below entry, or None
        # scale_trail (tgt_pts, gap_pts, fraction) -> the shared two-stage policy
        self._two = (TwoStage(target_pts=scale_trail[0], trail_gap=scale_trail[1],
                              fraction=scale_trail[2], stop_pts=prem_sl,
                              stop_reason="prem_sl", target_reason="scale_tgt",
                              trail_reason="prem_trail")
                     if scale_trail is not None else None)
        # the plain spot exits only run when no premium scheme replaces them
        self._spot_exits = (premium_tgt is None and prem_trail is None
                            and scale_trail is None)

    def on_day_start(self, ctx):
        self.ema = None
        self.n_obs = 0
        self.armed = None                  # (trigger_level, spot_sl)
        self.spot_sl = self.spot_tgt = None
        if self._two is not None:
            self._two.reset()
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
        self.spot_sl = sl
        if self._spot_exits:               # rr target only exists in spot-exit mode
            risk = abs(sl - level)
            self.spot_tgt = (level - self.RR * risk if self.SIDE == "PUT"
                             else level + self.RR * risk)
        self.armed = None
        self.n_trades += 1
        if self._two is not None:
            self._two.reset()
        return [Order(side=self.SIDE, strike=itm_strike(bar.close, self.SIDE, self.ITM),
                      lots=self.LOTS)]

    def manage(self, ctx, pos):
        bar = ctx.spot_bar
        self._observe(bar)                 # EMA stays continuous through trades
        self._rearm(bar)
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        if self.PREM_SL is None:                       # spot stop unless premium stop set
            if self.SIDE == "PUT":
                if bar.high >= self.spot_sl:
                    return Exit.market("spot_sl")
                if self._spot_exits and bar.low <= self.spot_tgt:
                    return Exit.market("spot_tgt")
            else:
                if bar.low <= self.spot_sl:
                    return Exit.market("spot_sl")
                if self._spot_exits and bar.high >= self.spot_tgt:
                    return Exit.market("spot_tgt")
        if self.PREM_TGT is not None:
            if self.PREM_SL is not None:
                return Exit.bracket(pos.entry - self.PREM_SL, pos.entry + self.PREM_TGT,
                                    stop_reason="prem_sl", target_reason="prem_tgt")
            return Exit.at(pos.entry + self.PREM_TGT, "rise", "prem_tgt")
        if self.PREM_TRAIL is not None:
            arm_pts, gap_pts = self.PREM_TRAIL
            return Exit.trail(gap_pts, "prem_trail", arm=pos.entry + arm_pts)
        if self._two is not None:          # scale-out then trail (shared policy)
            return self._two.exit_for(pos)
        return None
