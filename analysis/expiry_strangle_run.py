"""Near-expiry long-strangle backtest (premium-ceiling legs, first-leg 10x exit).

Parameters (as specified):
  - DTE 0 and 1 (min_dte=0, max_dte=1): trade the expiry day AND the day before,
    on the near contract. DaysToExpiry is a YEAR fraction; calendar DTE = int(*365).
  - entry_time 14:30, exit_time 15:25.
  - entry_premium_target = 5  -> per-leg premium CEILING: each side buys the strike
    with the LARGEST last-traded premium at or below Rs5.
  - min_entry_premium = 1     -> per-leg premium FLOOR: a side whose pick is below
    Rs1 skips the WHOLE day (no one-legged entries).
  - target_multiplier = 10    -> the FIRST leg to reach 10x its own fill closes BOTH
    legs; else exit both at 15:25.
  - Sizing ~Rs1 lakh deployed/trade; costs = CostModel + 0.25% slippage (SpreadPosition).

Standalone (per-leg / first-leg exit logic the one-position-per-day engine can't
express). Run: PYTHONPATH=. python3 analysis/expiry_strangle_run.py | tee analysis/expiry_strangle_run_out.txt
"""
from __future__ import annotations

from datetime import time

CEILING = 5.0            # entry_premium_target: per-leg premium ceiling (buy largest <= this)
MIN_ENTRY = 1.0          # min_entry_premium: per-leg floor; a side below it skips the day
TARGET_MULT = 10.0       # first leg to reach 10x its own fill closes BOTH legs
MIN_DTE, MAX_DTE = 0, 1  # trade DTE 0 (expiry day) and DTE 1 (day before)
ENTRY_T, EXIT_T = time(14, 30), time(15, 25)
CAPITAL = 100000.0       # Rs deployed per trade
SLIP = 0.0025
DAYS_PER_YEAR = 365.0    # DaysToExpiry is a calendar-year fraction
ROOT = "/Users/gokul/Desktop/tradealgo/data nifty "
START, END = "2024-01-01", "2026-04-10"


def pick_leg_ceiling(quotes, ceiling=CEILING, floor=MIN_ENTRY):
    """The traded strike with the LARGEST premium at or below ``ceiling`` (nearest
    to the ceiling from below). Returns ``(strike, premium)``, or ``None`` if that
    pick is below ``floor`` (the side can't clear it -> skip the whole day).
    ``quotes``: iterable of ``(strike, premium, traded)``."""
    best = None
    for strike, prem, traded in quotes:
        if not traded or prem is None or prem > ceiling:
            continue
        if best is None or prem > best[1]:
            best = (strike, prem)
    if best is None or best[1] < floor:
        return None
    return best


def find_exit_firstleg(minutes, ce_entry, pe_entry, mult):
    """Perfect-accuracy intrabar target: the first minute in which EITHER leg's
    1-min HIGH reaches ``mult`` x its own fill closes BOTH legs. The winning leg
    fills exactly at its target (limit level); the other leg at that minute's close.
    Else exit both at the last minute's close ('eod').

    ``minutes``: sorted ``[(dt, ce_high, pe_high, ce_close, pe_close)]``. None if empty."""
    if not minutes:
        return None
    ce_t, pe_t = mult * ce_entry, mult * pe_entry
    for dt, ceh, peh, cec, pec in minutes:
        ce_hit, pe_hit = ceh >= ce_t, peh >= pe_t
        if not (ce_hit or pe_hit):
            continue
        if ce_hit and pe_hit:                       # both intrabar -> the more decisive wins
            leg = "CE" if ceh / ce_entry >= peh / pe_entry else "PE"
        else:
            leg = "CE" if ce_hit else "PE"
        ce_x = ce_t if leg == "CE" else cec         # winner at target, other at close
        pe_x = pe_t if leg == "PE" else pec
        return (dt, ce_x, pe_x, leg)
    dt, ceh, peh, cec, pec = minutes[-1]
    return (dt, cec, pec, "eod")


# ------------------------------------------------------------------ #
# Real-data driver (pure helpers above are unit-tested)
# ------------------------------------------------------------------ #
def _entry_quotes(day_rows, side):
    pc = "CallClose" if side == "CALL" else "PutClose"
    tc = "CallIsTradedStrike" if side == "CALL" else "PutIsTradedStrike"
    return [(int(r.StrikePrice), float(getattr(r, pc)), bool(getattr(r, tc)))
            for r in day_rows.itertuples()]


def _leg_minutes(df_day, ce_k, pe_k, lo, hi):
    """[(dt, ce_high, pe_high, ce_close, pe_close)] per 1-min bar in (lo, hi]."""
    import pandas as pd
    c = df_day[df_day.StrikePrice == ce_k].set_index("DateTime")[["CallHigh", "CallClose"]]
    p = df_day[df_day.StrikePrice == pe_k].set_index("DateTime")[["PutHigh", "PutClose"]]
    both = pd.concat([c, p], axis=1).dropna()
    both = both[(both.index.time > lo) & (both.index.time <= hi)]
    return [(dt, float(r.CallHigh), float(r.PutHigh), float(r.CallClose), float(r.PutClose))
            for dt, r in both.iterrows()]


def _price_strangle(ce_k, pe_k, ce_entry, pe_entry, ce_exit, pe_exit, qty, cost):
    from engine.position.manager import SpreadLeg, SpreadPosition
    legs = (
        SpreadLeg(action="buy", side="CALL", strike=ce_k, entry=ce_entry * (1 + SLIP), qty=qty),
        SpreadLeg(action="buy", side="PUT", strike=pe_k, entry=pe_entry * (1 + SLIP), qty=qty),
    )
    return SpreadPosition(legs=legs).close(
        exit_prices=(ce_exit, pe_exit), cost_model=cost, slip=SLIP, reason="strangle")


def main():
    import pandas as pd
    from engine.data.catalog import Catalog
    from engine.execution.costs import CostModel
    from engine.instruments import lot_size_for

    cat = Catalog(ROOT)
    cost = CostModel()
    expiries = [e for e in cat.expiries() if START <= e <= END]

    rows, skipped = [], {"no_entry_bar": 0, "no_leg>=floor": 0, "no_window": 0}
    for e in expiries:
        df = pd.read_parquet(cat.path_for(e))
        df["DateTime"] = pd.to_datetime(df["DateTime"])
        ent = df[df["DateTime"].dt.time == ENTRY_T]
        if ent.empty:
            continue
        ent = ent.assign(_date=ent["DateTime"].dt.date,
                         _dte=(ent["DaysToExpiry"] * DAYS_PER_YEAR).astype(int))
        for d, dsnap in ent.groupby("_date"):
            dte = int(dsnap["_dte"].iloc[0])
            if not (MIN_DTE <= dte <= MAX_DTE):
                continue
            ce = pick_leg_ceiling(_entry_quotes(dsnap, "CALL"))
            pe = pick_leg_ceiling(_entry_quotes(dsnap, "PUT"))
            if ce is None or pe is None:
                skipped["no_leg>=floor"] += 1; continue
            (ce_k, ce_e), (pe_k, pe_e) = ce, pe
            day = df[df["DateTime"].dt.date == d]
            minutes = _leg_minutes(day, ce_k, pe_k, ENTRY_T, EXIT_T)
            ex = find_exit_firstleg(minutes, ce_e, pe_e, TARGET_MULT)
            if ex is None:
                skipped["no_window"] += 1; continue
            _dt, ce_x, pe_x, reason = ex
            debit = ce_e + pe_e
            lot = lot_size_for(d)
            lots = max(1, int(CAPITAL / (debit * lot)))
            qty = lots * lot
            t = _price_strangle(ce_k, pe_k, ce_e, pe_e, ce_x, pe_x, qty, cost)
            rows.append({"date": d, "dte": dte, "ce_k": ce_k, "pe_k": pe_k,
                         "ce_e": ce_e, "pe_e": pe_e, "debit": debit, "reason": reason,
                         "net": t.net, "cost": t.cost, "lots": lots,
                         "deployed": debit * qty, "roc": t.net / (debit * qty)})

    _report(pd.DataFrame(rows), skipped, expiries)


def _report(tr, skipped, expiries):
    print("NEAR-EXPIRY LONG STRANGLE — premium-ceiling legs (<=Rs5, >=Rs1), first-leg 10x closes both")
    print(f"window {START}..{END} (data-limited); expiries {len(expiries)}, "
          f"trades {len(tr)}, skipped {skipped}")
    print(f"params: DTE {MIN_DTE}-{MAX_DTE}, entry {ENTRY_T}, exit {EXIT_T}, "
          f"ceiling Rs{CEILING:.0f}, floor Rs{MIN_ENTRY:.0f}, target {TARGET_MULT:.0f}x first-leg, "
          f"slip {SLIP:.2%}, ~Rs{CAPITAL:,.0f}/trade\n")
    if tr.empty:
        print("no trades"); return
    net = tr.net
    loss = -net[net <= 0].sum()
    pf = (net[net > 0].sum() / loss) if loss > 0 else float("inf")
    hit = (tr.reason != "eod").mean()
    print(f"TRADES  n={len(tr)}  net={net.sum():>14,.0f}  win={(net>0).mean()*100:4.0f}%  "
          f"PF={pf:5.2f}  target-hit={hit*100:4.0f}%  avg net/trade={net.mean():>10,.0f}")
    print(f"        avg deployed={tr.deployed.mean():>10,.0f}  avg lots={tr.lots.mean():5.0f}  "
          f"avg debit(pts)={tr.debit.mean():.2f}  avg return-on-capital/trade={tr.roc.mean()*100:+.1f}%")
    print(f"        SUM invested={CAPITAL*len(tr):,.0f}  "
          f"total return={net.sum()/(CAPITAL*len(tr))*100:+.1f}%  (non-compounded)")

    print("\nBY DTE:")
    for dte, s in tr.groupby("dte"):
        l = -s.net[s.net <= 0].sum()
        p = (s.net[s.net > 0].sum() / l) if l > 0 else float("inf")
        print(f"  DTE {dte}: n={len(s):>3}  net={s.net.sum():>14,.0f}  win={(s.net>0).mean()*100:4.0f}%  "
              f"PF={p:5.2f}  target-hit={(s.reason!='eod').mean()*100:4.0f}%")

    print("\nPER YEAR:")
    tr = tr.assign(yr=tr.date.map(lambda d: d.year))
    for y, s in tr.groupby("yr"):
        l = -s.net[s.net <= 0].sum()
        p = (s.net[s.net > 0].sum() / l) if l > 0 else float("inf")
        print(f"  {y}: n={len(s):>3}  net={s.net.sum():>14,.0f}  win={(s.net>0).mean()*100:4.0f}%  "
              f"PF={p:5.2f}  target-hit={(s.reason!='eod').mean()*100:4.0f}%")

    print("\nEXIT REASON MIX:", dict(tr.reason.value_counts()))
    print(f"\nWINNERS ({(tr.net>0).sum()} of {len(tr)}):")
    print(f"  {'date':<11}{'dte':>4} {'CE':>6} {'PE':>6} {'ce@':>5} {'pe@':>5} "
          f"{'lots':>5} {'exit':<5} {'net':>12} {'roc%':>7}")
    for r in tr[tr.net > 0].sort_values("date").itertuples():
        print(f"  {str(r.date):<11}{r.dte:>4} {r.ce_k:>6} {r.pe_k:>6} {r.ce_e:>5.2f} {r.pe_e:>5.2f} "
              f"{r.lots:>5} {r.reason:<5} {r.net:>12,.0f} {r.roc*100:>+6.1f}%")


if __name__ == "__main__":
    main()
