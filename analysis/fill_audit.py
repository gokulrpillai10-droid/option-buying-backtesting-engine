"""Gap-drive fill audit — record live ADANIENT depth, compare vs engine slip.

The backtest assumes 0.05%/side flat slippage (stressed to 0.10% + range-
scaled). This tool measures what crossing the book ACTUALLY costs at the
moments the strategy trades, from live Kite quote depth (5 levels/side —
depth is live-only; the historical API has none, so evidence accrues
forward from here).

Protocol:
- any day:    a snapshot burst gives the resting spread/depth baseline.
- signal day: (gap-down >=0.5%, still below open at 09:30) run a burst over
  09:29-09:31 — the entry window — and again if the OR-stop area is tested;
  the EOD window (15:25-15:29) covers the square-off leg.

record: append one JSON line per snapshot to data_equity/fill_audit/<sym>.jsonl
        (--loop N --every S takes a burst). Each line: ts, ltp, ohlc, gap%,
        below_open, best bid/ask, spread%, sweep cost to fill QTY through the
        visible book (the honest "cross the book now" price), top-5 qty.
report: distribution of half-spread% and sweep-slip% vs the engine's
        assumed 0.05%/side and the stress 0.10%/side, split by signal window.

Run: PYTHONPATH=<repo> python3 analysis/fill_audit.py record [--loop N] [--every S]
     PYTHONPATH=<repo> python3 analysis/fill_audit.py report
"""
import json
import os
import sys
import time as _time
from datetime import datetime

SYMBOL = "ADANIENT"
QTY = 500                     # the backtested size
OUT_DIR = "/Users/gokul/Desktop/buildstrategies/data_equity/fill_audit"
OUT = os.path.join(OUT_DIR, f"{SYMBOL}.jsonl")
ASSUMED = {"base": 0.0005, "stress": 0.0010}


def sweep_cost(levels, qty):
    """Average fill price for ``qty`` swept through visible depth levels
    [{'price','quantity'},...]; None if the book can't absorb it."""
    remaining, cost = qty, 0.0
    for lv in levels:
        take = min(remaining, lv["quantity"])
        cost += take * lv["price"]
        remaining -= take
        if remaining <= 0:
            return cost / qty
    return None


def snapshot(kite):
    q = kite.quote([f"NSE:{SYMBOL}"])[f"NSE:{SYMBOL}"]
    ltp, ohlc, d = q["last_price"], q["ohlc"], q["depth"]
    bb, ba = d["buy"][0]["price"], d["sell"][0]["price"]
    mid = (bb + ba) / 2.0
    prev_close = ohlc["close"]                       # prev session close intraday
    gap = ohlc["open"] / prev_close - 1.0 if prev_close else None
    sell_sweep = sweep_cost(d["buy"], QTY)           # shorting hits the bids
    buy_sweep = sweep_cost(d["sell"], QTY)           # covering lifts the asks
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "ltp": ltp, "open": ohlc["open"], "prev_close": prev_close,
        "gap_pct": None if gap is None else round(gap * 100, 3),
        "below_open": ltp < ohlc["open"],
        "bid": bb, "ask": ba, "spread_pct": round((ba - bb) / mid * 100, 4),
        "half_spread_pct": round((ba - bb) / 2 / mid * 100, 4),
        "sell_sweep_slip_pct": (None if sell_sweep is None
                                else round((mid - sell_sweep) / mid * 100, 4)),
        "buy_sweep_slip_pct": (None if buy_sweep is None
                               else round((buy_sweep - mid) / mid * 100, 4)),
        "bid_qty5": sum(l["quantity"] for l in d["buy"]),
        "ask_qty5": sum(l["quantity"] for l in d["sell"]),
    }


def record(loop=1, every=20):
    from datetime import time as _t
    now = datetime.now().time()
    if not (_t(9, 15) <= now <= _t(15, 30)):
        sys.exit(f"market closed ({now:%H:%M}) — depth is meaningless off-hours; "
                 "record between 09:15 and 15:30 IST")
    from engine.equity.kite_loader import make_kite
    kite = make_kite()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "a") as f:
        for i in range(loop):
            snap = snapshot(kite)
            f.write(json.dumps(snap) + "\n")
            f.flush()
            print(json.dumps(snap))
            if i < loop - 1:
                _time.sleep(every)


def report():
    if not os.path.exists(OUT):
        sys.exit(f"no audit log yet at {OUT} — run record first")
    rows = [json.loads(l) for l in open(OUT)]
    print(f"{len(rows)} snapshots in {OUT}\n")
    sig = [r for r in rows if r["gap_pct"] is not None
           and r["gap_pct"] <= -0.5 and r["below_open"]]
    for tag, rs in (("ALL", rows), ("SIGNAL-DAY (gap<=-0.5%, below open)", sig)):
        if not rs:
            print(f"{tag}: none")
            continue
        hs = sorted(r["half_spread_pct"] for r in rs)
        sw = sorted(r["sell_sweep_slip_pct"] for r in rs
                    if r["sell_sweep_slip_pct"] is not None)
        cant = sum(1 for r in rs if r["sell_sweep_slip_pct"] is None)
        med = hs[len(hs) // 2]
        p90 = hs[min(len(hs) - 1, int(len(hs) * 0.9))]
        print(f"{tag}: n={len(rs)}")
        print(f"  half-spread%   median {med:.4f}  p90 {p90:.4f}")
        if sw:
            print(f"  sweep-slip% ({QTY} sh, short side)  "
                  f"median {sw[len(sw)//2]:.4f}  p90 "
                  f"{sw[min(len(sw)-1, int(len(sw)*0.9))]:.4f}")
        if cant:
            print(f"  !! book too thin for {QTY} sh in {cant}/{len(rs)} snaps")
        print(f"  engine assumes {ASSUMED['base']*100:.2f}% base / "
              f"{ASSUMED['stress']*100:.2f}% stress per side -> "
              f"{'COVERED' if p90 <= ASSUMED['stress']*100 else 'EXCEEDED at p90'}\n")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"
    if mode == "record":
        loop = int(sys.argv[sys.argv.index("--loop") + 1]) if "--loop" in sys.argv else 1
        every = int(sys.argv[sys.argv.index("--every") + 1]) if "--every" in sys.argv else 20
        record(loop, every)
    else:
        report()
