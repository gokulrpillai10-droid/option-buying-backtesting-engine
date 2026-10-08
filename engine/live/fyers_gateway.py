"""Fyers order gateway for live NIFTY option trading.

Translates the engine's internal Order model into Fyers v3 API calls.
Credentials are read from .env (FYERS_APP_ID, FYERS_ACCESS_TOKEN).

Daily token must be refreshed before use:
    python3 fyers_auth.py

Symbol format for NIFTY weekly options on Fyers v3:
    NSE:NIFTY{YY}{M_CODE}{DD}{STRIKE}{CE|PE}
    Month codes: 1-9 = Jan-Sep, O = Oct, N = Nov, D = Dec
    Example: NSE:NIFTY25D1824000CE  (18 Dec 2025, 24000 CE)

Symbol format for NIFTY monthly options:
    NSE:NIFTY{YY}{MMM}{STRIKE}{CE|PE}
    Example: NSE:NIFTY25DEC24000CE  (Dec 2025 monthly, 24000 CE)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

_WEEKLY_MONTH_CODES = {
    1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
    7: "7", 8: "8", 9: "9", 10: "O", 11: "N", 12: "D",
}
_MONTHLY_MONTH_NAMES = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

# Fyers order type codes
MARKET = 2
LIMIT  = 1

# Fyers side codes
BUY  =  1
SELL = -1


@dataclass
class OrderResult:
    order_id: str
    status: str          # "placed" | "rejected" | "error"
    message: str
    raw: dict


def nifty_symbol(strike: int, expiry: date, option_type: str, is_monthly: bool = False) -> str:
    """Build a Fyers v3 symbol string for a NIFTY option.

    Args:
        strike:      Strike price (e.g. 24000)
        expiry:      Expiry date of the contract
        option_type: "CE" or "PE"
        is_monthly:  True for monthly expiry (last Thursday of month)
    """
    yy = expiry.strftime("%y")
    opt = option_type.upper()
    if is_monthly:
        mon = _MONTHLY_MONTH_NAMES[expiry.month]
        return f"NSE:NIFTY{yy}{mon}{strike}{opt}"
    else:
        m_code = _WEEKLY_MONTH_CODES[expiry.month]
        dd = f"{expiry.day:02d}"
        return f"NSE:NIFTY{yy}{m_code}{dd}{strike}{opt}"


def make_fyers():
    """Build a live FyersModel from .env credentials."""
    try:
        from dotenv import load_dotenv
        from fyers_apiv3 import fyersModel
    except ImportError as e:
        raise ImportError(f"Run: pip3 install fyers-apiv3 python-dotenv  ({e})")

    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    load_dotenv(env_path)

    app_id = os.getenv("FYERS_APP_ID")
    token  = os.getenv("FYERS_ACCESS_TOKEN")

    if not app_id or not token:
        raise RuntimeError(
            "FYERS_APP_ID or FYERS_ACCESS_TOKEN missing in .env. "
            "Run python3 fyers_auth.py to refresh today's token."
        )

    return fyersModel.FyersModel(client_id=app_id, token=token, is_async=False)


class FyersGateway:
    """Thin wrapper around the Fyers v3 API for NIFTY option orders.

    Usage:
        gw = FyersGateway()
        result = gw.place_market_order(
            symbol=nifty_symbol(24000, date(2025, 12, 18), "CE"),
            qty=75,
            side=BUY,
        )
    """

    def __init__(self, fyers=None):
        self._fyers = fyers or make_fyers()

    def place_market_order(
        self,
        symbol: str,
        qty: int,
        side: int,
        product_type: str = "INTRADAY",
    ) -> OrderResult:
        """Place a market order. side = BUY (+1) or SELL (-1)."""
        return self._place(symbol, qty, side, MARKET, 0.0, product_type)

    def place_limit_order(
        self,
        symbol: str,
        qty: int,
        side: int,
        limit_price: float,
        product_type: str = "INTRADAY",
    ) -> OrderResult:
        """Place a limit order."""
        return self._place(symbol, qty, side, LIMIT, limit_price, product_type)

    def _place(
        self,
        symbol: str,
        qty: int,
        side: int,
        order_type: int,
        limit_price: float,
        product_type: str,
    ) -> OrderResult:
        data = {
            "symbol":       symbol,
            "qty":          qty,
            "type":         order_type,
            "side":         side,
            "productType":  product_type,
            "limitPrice":   limit_price,
            "stopPrice":    0,
            "validity":     "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "stopLoss":     0,
            "takeProfit":   0,
        }
        resp = self._fyers.place_order(data=data)
        if resp.get("s") == "ok":
            return OrderResult(
                order_id=resp.get("id", ""),
                status="placed",
                message=resp.get("message", ""),
                raw=resp,
            )
        return OrderResult(
            order_id="",
            status="rejected",
            message=resp.get("message", str(resp)),
            raw=resp,
        )

    def get_order_status(self, order_id: str) -> dict:
        """Fetch current status of an order by ID."""
        resp = self._fyers.orderbook()
        if resp.get("s") != "ok":
            return {"error": resp.get("message", str(resp))}
        orders = resp.get("orderBook", [])
        for o in orders:
            if str(o.get("id")) == str(order_id):
                return o
        return {"error": f"order {order_id!r} not found in orderbook"}

    def cancel_order(self, order_id: str) -> dict:
        """Cancel a pending order."""
        resp = self._fyers.cancel_order(data={"id": order_id})
        return resp

    def positions(self) -> list:
        """Return all open intraday positions."""
        resp = self._fyers.positions()
        if resp.get("s") != "ok":
            return []
        return resp.get("netPositions", [])

    def funds(self) -> dict:
        """Available margin / funds."""
        resp = self._fyers.funds()
        if resp.get("s") != "ok":
            return {}
        return {item["title"]: item["equityAmount"]
                for item in resp.get("fund_limit", [])}
