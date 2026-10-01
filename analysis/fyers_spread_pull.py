#!/usr/bin/env python3
"""
Live bid-ask spread measurement for NIFTY options via Fyers optionchain API.

Fetches quotes for ATM ± 50/100/150/200 pt strikes on the nearest active
expiry and prints bid, ask, spread, mid, LTP. Used to calibrate spread_rs.

Run during market hours (9:15–15:30 IST):
    python3 analysis/fyers_spread_pull.py

Prereqs:
    pip3 install fyers-apiv3 python-dotenv
    python3 fyers_auth.py   # refresh token first
"""
from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    sys.exit("pip3 install python-dotenv")
try:
    from fyers_apiv3 import fyersModel
except ImportError:
    sys.exit("pip3 install fyers-apiv3")

# ── fyers client ──────────────────────────────────────────────────────────────

def make_fyers():
    env = os.path.join(os.path.dirname(__file__), "..", ".env")
    load_dotenv(env)
    app_id = os.getenv("FYERS_APP_ID")
    token  = os.getenv("FYERS_ACCESS_TOKEN")
    if not app_id or not token:
        sys.exit("FYERS_APP_ID / FYERS_ACCESS_TOKEN missing — run fyers_auth.py first")
    return fyersModel.FyersModel(client_id=app_id, token=token, is_async=False)

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    fyers = make_fyers()

    # strikecount=4 → ATM ± 4 strikes at 50pt spacing = ATM ± 200pt
    resp = fyers.optionchain(data={
        "symbol": "NSE:NIFTY50-INDEX",
        "strikecount": 4,
        "timestamp": "",
    })
    if resp.get("s") != "ok":
        sys.exit(f"optionchain failed: {resp}")

    chain = resp.get("data", {}).get("optionsChain", [])

    # Spot / ATM from underlying row
    underlying = next((r for r in chain if r.get("strike_price") == -1), None)
    spot = underlying["ltp"] if underlying else None
    if not spot:
        sys.exit("Could not read NIFTY spot from optionchain response")
    atm = round(spot / 50) * 50

    # Find active expiry label from first real option row
    first_opt = next((r for r in chain if r.get("option_type") in ("CE", "PE")), None)
    expiry_label = first_opt["symbol"].split("NIFTY")[1][:5] if first_opt else "?"

    print(f"\nNIFTY spot: {spot:.0f}   ATM: {atm}   Expiry: {expiry_label}\n")

    # Collect options, sort by strike
    options = [r for r in chain if r.get("option_type") in ("CE", "PE")]
    strikes = sorted({r["strike_price"] for r in options})

    header = (f"{'Strike':>8}  {'Side':4}  {'Bid':>8}  {'Ask':>8}  "
              f"{'Spread':>8}  {'Mid':>8}  {'LTP':>8}  OTM_pts")
    print(header)
    print("-" * len(header))

    atm_spread = None
    spreads = []
    for strike in strikes:
        otm_pts = strike - atm
        for side in ("CE", "PE"):
            row = next((r for r in options
                        if r["strike_price"] == strike and r["option_type"] == side), None)
            if row is None:
                continue
            bid    = float(row.get("bid") or 0)
            ask    = float(row.get("ask") or 0)
            ltp    = float(row.get("ltp") or 0)
            spread = ask - bid
            mid    = (bid + ask) / 2 if (bid + ask) > 0 else ltp
            if spread > 0:
                spreads.append(spread)
            if strike == atm and side == "CE" and spread > 0:
                atm_spread = spread
            print(f"{strike:>8}  {side:4}  {bid:>8.2f}  {ask:>8.2f}  "
                  f"{spread:>8.2f}  {mid:>8.2f}  {ltp:>8.2f}  {otm_pts:>+5}")

    if spreads:
        print(f"\n--- spread summary ---")
        if atm_spread:
            print(f"ATM CE spread:       {atm_spread:.2f}")
        print(f"Mean spread (all):   {sum(spreads)/len(spreads):.2f}")
        print(f"Min / Max:           {min(spreads):.2f} / {max(spreads):.2f}")
        if atm_spread:
            print(f"\nRecommended spread_rs starting point: ATM spread / 2 = "
                  f"{atm_spread / 2:.2f}  (half-spread per leg)")

if __name__ == "__main__":
    main()
