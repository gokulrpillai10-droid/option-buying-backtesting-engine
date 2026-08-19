"""Pull a LIVE NIFTY option-chain snapshot from Kite (LTP / OI / volume).

Kite has no chain endpoint, so we assemble one: instruments('NFO') -> filter
NIFTY options for the nearest expiry -> batch quote() ATM +/- STRIKE_RANGE. Prints
the chain, PCR, and max-OI (support/resistance) strikes, and writes a timestamped
snapshot CSV under analysis/chain_snapshots/ for downstream OI features.

Kite quote() returns price/OHLC/volume/OI/depth but NOT IV or Greeks — those
would have to be computed (Black-Scholes) separately.

Prereq: a fresh Kite token (cd ~/Desktop/tradealgo && python3 zerodha_auth.py).
    PYTHONPATH=. python3 analysis/live_nifty_chain.py
"""
from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd

from engine.equity.kite_loader import make_kite

STRIKE_STEP = 50
STRIKE_RANGE = 10                    # ATM +/- this many strikes each side
SNAP_DIR = "analysis/chain_snapshots"


def _nearest_expiry(nifty_opts):
    today = date.today()
    exps = sorted({i["expiry"] for i in nifty_opts if i["expiry"] >= today})
    if not exps:
        raise RuntimeError("no NIFTY option expiry >= today in the NFO dump")
    return exps[0]


def fetch_chain(kite):
    nfo = kite.instruments("NFO")
    opts = [i for i in nfo if i["name"] == "NIFTY"
            and i["instrument_type"] in ("CE", "PE") and i["segment"] == "NFO-OPT"]
    expiry = _nearest_expiry(opts)
    spot = kite.ltp(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"]
    atm = round(spot / STRIKE_STEP) * STRIKE_STEP
    strikes = [atm + k * STRIKE_STEP for k in range(-STRIKE_RANGE, STRIKE_RANGE + 1)]

    sel = [i for i in opts if i["expiry"] == expiry and i["strike"] in strikes]
    by_sym = {f'NFO:{i["tradingsymbol"]}': i for i in sel}
    q = kite.quote(list(by_sym))

    rows = {k: {"strike": k} for k in strikes}
    for sym, inst in by_sym.items():
        data = q.get(sym, {})
        side = "CE" if inst["instrument_type"] == "CE" else "PE"
        rows[inst["strike"]].update({
            f"{side}_ltp": data.get("last_price"),
            f"{side}_oi": data.get("oi"),
            f"{side}_vol": data.get("volume"),
        })
    df = pd.DataFrame([rows[s] for s in strikes])
    return df, expiry, spot, atm


def _fmt(v, kind="int"):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    return f"{int(v):,}" if kind == "int" else f"{v:,.2f}"


def main():
    kite = make_kite()
    df, expiry, spot, atm = fetch_chain(kite)
    now = datetime.now()

    print(f"NIFTY LIVE OPTION CHAIN — expiry {expiry}  |  spot {spot:,.2f}  "
          f"ATM {atm:,}  |  {now:%Y-%m-%d %H:%M:%S}")
    print(f"{'CALL OI':>11} {'CALL Vol':>10} {'CALL LTP':>9}   "
          f"{'STRIKE':^8}   {'PE LTP':>9} {'PE Vol':>10} {'PE OI':>11}")
    for _, r in df.iterrows():
        mark = " <ATM" if r["strike"] == atm else ""
        print(f"{_fmt(r.get('CE_oi')):>11} {_fmt(r.get('CE_vol')):>10} "
              f"{_fmt(r.get('CE_ltp'),'f'):>9}   {int(r['strike']):^8}   "
              f"{_fmt(r.get('PE_ltp'),'f'):>9} {_fmt(r.get('PE_vol')):>10} "
              f"{_fmt(r.get('PE_oi')):>11}{mark}")

    ce_oi = df["CE_oi"].fillna(0).sum()
    pe_oi = df["PE_oi"].fillna(0).sum()
    pcr = pe_oi / ce_oi if ce_oi else float("nan")
    max_ce = df.loc[df["CE_oi"].fillna(0).idxmax(), "strike"] if ce_oi else None
    max_pe = df.loc[df["PE_oi"].fillna(0).idxmax(), "strike"] if pe_oi else None
    print(f"\nPCR (put OI / call OI, ATM+/-{STRIKE_RANGE}): {pcr:.2f}")
    print(f"Max CALL OI @ {int(max_ce):,} (resistance)   "
          f"Max PUT OI @ {int(max_pe):,} (support)")

    os.makedirs(SNAP_DIR, exist_ok=True)
    df.insert(0, "spot", spot)
    df.insert(0, "captured_at", now.isoformat(timespec="seconds"))
    df.insert(0, "expiry", expiry.isoformat())
    path = os.path.join(SNAP_DIR, f"NIFTY_{expiry:%Y%m%d}_{now:%Y%m%d_%H%M%S}.csv")
    df.to_csv(path, index=False)
    print(f"\nsnapshot -> {path}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
