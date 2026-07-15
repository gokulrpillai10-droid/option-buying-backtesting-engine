"""Opening-range fade-short on cash equity (built for ADANIENT).

Study basis (analysis/hdfc_patterns.py on ADANIENT, 245 sessions to
2026-07-14): downside 15m-OR breaks win 57.9% to close while upside breaks
lose; 27% of session lows print in the final 45 minutes (weak days trend into
the bell); the edge concentrates in pre-11:00 triggers on non-gap-up days and
dies in vertical-ramp regimes — hence the three entry filters and the
caller-supplied regime gate.

Rules: measure the 09:15-09:30 opening range; short the first 5m close below
the OR-low, only if the signal bar is before ``entry_cutoff`` and the day did
not gap up more than ``gap_thr``. One trade/day. Stop = resting level at the
OR-high (intrabar, gap-through honest); otherwise hold to the ``square``
square-off — no profit target, the late-LoD stat is the payoff.

``regime_skip``: optional callable date -> bool, True = stand aside (ramp
gate). Fit its parameters on the train split only; the strategy itself stays
parameter-free about regime.
"""
from datetime import time

from engine.equity.model import EquityOrder
from engine.strategy.base import Strategy
from engine.strategy.exit import Exit


class OrbShortEquity(Strategy):
    def __init__(self, capital=100_000.0, qty=None, gap_thr=0.0015,
                 or_end=time(9, 30), entry_cutoff=time(11, 0),
                 square=time(15, 15), regime_skip=None):
        self.CAPITAL = capital
        self.QTY = qty                         # fixed shares; overrides capital
        self.GAP_THR = gap_thr
        self.OR_END, self.CUTOFF, self.SQUARE = or_end, entry_cutoff, square
        self.regime_skip = regime_skip

    def on_day_start(self, ctx):
        self.active = False
        self.traded = False                    # one trade/day guard
        self._or_high = self._or_low = None
        pc = ctx.prev_close()
        if pc is None:
            return
        if self.regime_skip is not None and self.regime_skip(ctx.now.date()):
            return
        gap = ctx.spot_bar.open / pc - 1.0
        if gap > self.GAP_THR:                 # gap-up day: different trade
            return
        self._or_high = ctx.spot_bar.high      # seed with the 09:15 bar
        self._or_low = ctx.spot_bar.low
        self.active = True

    def _track_or(self, ctx):
        # incremental, same reasoning as GapRouterEquity._track_or: the OR is
        # immutable after OR_END and ctx.opening_range() is O(N) per call.
        # START-labelled bars: only bars labelled BEFORE or_end are inside the
        # 09:15-09:30 window (the 09:30 bar covers 09:30-09:35).
        if ctx.spot_bar is None or self._or_high is None:
            return
        if ctx.now.time() < self.OR_END:
            self._or_high = max(self._or_high, ctx.spot_bar.high)
            self._or_low = min(self._or_low, ctx.spot_bar.low)

    def on_bar(self, ctx):
        self._track_or(ctx)
        if not self.active or self.traded:
            return []
        t = ctx.now.time()
        if t < self.OR_END:
            return []
        if t >= self.CUTOFF:                   # late break = different pattern
            self.active = False
            return []
        if ctx.spot_bar.close < self._or_low:
            self.traded = True
            return [EquityOrder(direction="short", capital=self.CAPITAL,
                                qty=self.QTY)]
        return []

    def manage(self, ctx, pos):
        if ctx.now.time() >= self.SQUARE:
            return Exit.market("eod")
        # resting stop at the OR-high: a short exits when price RISES to it
        return Exit.at(self._or_high, "rise", "or_stop")
