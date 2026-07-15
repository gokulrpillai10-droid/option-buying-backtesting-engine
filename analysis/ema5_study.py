"""5-EMA strategy (Power-of-Stocks style) event study on cached 1-min data.

Rules implemented exactly as specified:
- SHORT on 5-min: setup candle fully ABOVE the 5-EMA (low > ema, wicks
  included). Trigger = a later candle breaking the setup LOW (stop order at
  that level, filled intrabar); SL = setup HIGH. A candle that neither
  triggers nor stays clear of the EMA disarms; a new non-touching candle
  re-arms at its own low.
- LONG on 15-min: exact mirror (candle fully below EMA, trigger = break of
  setup high, SL = setup low).
- EMA: 5-period, continuous across days (chart behavior); setups do not
  carry overnight (disarmed at each session start). One open trade per side;
  no fresh entries after 15:00; EOD square-off 15:15. Intrabar exits resolve
  on 1-min bars; if SL and target fall in the same minute, SL wins
  (conservative).
- Exits swept: fixed RR targets 1:2 / 1:3 / 1:4 and no-target (SL + EOD
  only, the "let winners run" reading).

Costs: reported per trade in % of entry — gross, and net of the Zerodha MIS
card + 0.05% slippage per side. With stops this tight, cost drag per R is
the number that decides everything; it is printed explicitly.

Run: PYTHONPATH=<repo> python3 analysis/ema5_study.py [SYMBOL]
"""
import sys

import numpy as np
import pandas as pd

from engine.equity.costs import EquityCostModel
from engine.equity.kite_loader import load_cached

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "ADANIENT"
SLIP = 0.0005                      # per side, adverse
NO_ENTRY_AFTER = pd.Timestamp("2000-01-01 15:00").time()
SQUARE = pd.Timestamp("2000-01-01 15:15").time()
COSTS = EquityCostModel()

df = load_cached(SYMBOL).set_index("DateTime")
counts = df.groupby(df.index.normalize()).size()
df = df[df.index.normalize().isin(counts[counts >= 300].index)]

def bars_tf(rule):
    o = df["Open"].resample(rule, offset="9h15min").first()
    h = df["High"].resample(rule, offset="9h15min").max()
    l = df["Low"].resample(rule, offset="9h15min").min()
    c = df["Close"].resample(rule, offset="9h15min").last()
    out = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}).dropna()
    out["ema"] = out["Close"].ewm(span=5, adjust=False).mean()
    return out

M1_BY_DAY = {d: g.sort_index() for d, g in df.groupby(df.index.normalize())}


def simulate(side, rule, rr):
    """side: 'short'|'long'; rr: RR multiple or None (SL+EOD only)."""
    bars = bars_tf(rule)
    tfd = pd.Timedelta(rule)
    trades = []
    for day, day_bars in bars.groupby(bars.index.normalize()):
        m1 = M1_BY_DAY.get(day)
        if m1 is None:
            continue
        armed = None                     # (level, sl)
        busy_until = None                # skip signals while a trade is open
        for ts, b in day_bars.iterrows():
            if busy_until is not None and ts < busy_until:
                continue
            trig = False
            if armed is not None and ts.time() < NO_ENTRY_AFTER:
                lvl, sl = armed
                if side == "short" and b["Low"] < lvl:
                    trig = True
                if side == "long" and b["High"] > lvl:
                    trig = True
            if trig:
                # find the triggering minute inside this candle
                w = m1[(m1.index >= ts) & (m1.index < ts + tfd)]
                hit = w[w["Low"] < lvl] if side == "short" else w[w["High"] > lvl]
                t0 = hit.index[0]
                entry = lvl
                risk = abs(sl - entry)
                tgt = (entry - rr * risk if side == "short"
                       else entry + rr * risk) if rr else None
                # walk 1-min from the entry minute (inclusive) to square-off
                path = m1[(m1.index >= t0)]
                exit_px, reason = None, "eod"
                for mt, mb in path.iterrows():
                    if mt.time() >= SQUARE:
                        exit_px = mb["Open"]; break
                    if side == "short":
                        if mb["High"] >= sl:
                            exit_px = max(sl, mb["Open"]); reason = "sl"; break
                        if tgt and mb["Low"] <= tgt:
                            exit_px = min(tgt, mb["Open"]) if mb["Open"] < tgt else tgt
                            reason = "tgt"; break
                    else:
                        if mb["Low"] <= sl:
                            exit_px = min(sl, mb["Open"]); reason = "sl"; break
                        if tgt and mb["High"] >= tgt:
                            exit_px = max(tgt, mb["Open"]) if mb["Open"] > tgt else tgt
                            reason = "tgt"; break
                    last_mt = mt
                if exit_px is None:
                    exit_px = path["Close"].iloc[-1]
                # adverse slippage both sides
                if side == "short":
                    e, x = entry * (1 - SLIP), exit_px * (1 + SLIP)
                    gross = (e - x) / e
                else:
                    e, x = entry * (1 + SLIP), exit_px * (1 - SLIP)
                    gross = (x - e) / e
                qty = int(100_000 // e)
                cost = COSTS.total(buy_value=(x if side == "short" else e) * qty,
                                   sell_value=(e if side == "short" else x) * qty)
                trades.append({"day": day, "year": day.year, "entry": e,
                               "risk_pct": risk / entry * 100,
                               "gross": gross * 100,
                               "net": gross * 100 - cost / (e * qty) * 100,
                               "r": (gross * entry / risk) if risk > 0 else 0,
                               "reason": reason})
                busy_until = mt + pd.Timedelta(minutes=1) if exit_px is not None else None
                armed = None
                continue
            # re-arm / disarm on this candle
            if side == "short":
                armed = (b["Low"], b["High"]) if b["Low"] > b["ema"] else None
            else:
                armed = (b["High"], b["Low"]) if b["High"] < b["ema"] else None
        # day ends -> disarm (no overnight setups)
    return pd.DataFrame(trades)


def report(tag, T):
    if len(T) == 0:
        print(f"  {tag}: no trades"); return
    wins = T[T["net"] > 0]
    pf = wins["net"].sum() / max(-T.loc[T["net"] <= 0, "net"].sum(), 1e-9)
    print(f"  {tag:<14} n={len(T):5d} ({len(T)/6.5:.0f}/yr)  win={100*len(wins)/len(T):4.1f}%  "
          f"avg gross={T['gross'].mean():+.3f}%  avg net={T['net'].mean():+.3f}%  "
          f"PF={pf:4.2f}  avgR(gross)={T['r'].mean():+.2f}")


print(f"{SYMBOL}: {len(df):,} 1-min bars, {df.index.min().date()} -> {df.index.max().date()}")
print(f"median 5m setup risk and cost drag printed below — the decisive ratio\n")
for side, rule in [("short", "5min"), ("long", "15min")]:
    print(f"== {side.upper()} on {rule} ==")
    for rr in (2, 3, 4, None):
        T = simulate(side, rule, rr)
        report(f"RR 1:{rr}" if rr else "no-target", T)
        if rr == 3 and len(T):
            print(f"    risk/trade median={T['risk_pct'].median():.2f}%  "
                  f"cost+slip drag ~{(T['gross']-T['net']).mean():.3f}%/trade "
                  f"= {((T['gross']-T['net'])/(T['risk_pct'])).median():.2f}R")
            print("    by year (net% sum | n | win%):")
            for y, seg in T.groupby("year"):
                w = 100 * (seg["net"] > 0).mean()
                print(f"      {y}: {seg['net'].sum():+7.1f}% | {len(seg):4d} | {w:4.1f}%")
    print()
