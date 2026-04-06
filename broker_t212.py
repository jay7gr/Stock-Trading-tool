"""
Trading 212 API Client — execute real trades in your Stocks & Shares ISA.

API docs: https://t212public-api-docs.redoc.ly/
Free, supports ISA accounts, ~6 requests/second rate limit.
"""

import time
import requests
from dataclasses import dataclass
from typing import Optional

import config


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    ticker: str
    action: str
    quantity: float
    price: float
    value_gbp: float
    message: str


class Trading212Client:
    """Client for Trading 212 REST API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or config.T212_API_KEY
        self.base_url = config.T212_BASE_URL
        self.last_request = 0

    def _headers(self) -> dict:
        return {"Authorization": self.api_key}

    def _rate_limit(self):
        """~6 requests/second."""
        elapsed = time.time() - self.last_request
        if elapsed < 0.17:
            time.sleep(0.17 - elapsed)
        self.last_request = time.time()

    def _request(self, method: str, endpoint: str,
                 json_data: dict = None) -> Optional[dict]:
        if not self.api_key:
            print("[T212] No API key configured")
            return None

        self._rate_limit()
        url = f"{self.base_url}/{endpoint}"

        try:
            if method == "GET":
                resp = requests.get(url, headers=self._headers(), timeout=10)
            elif method == "POST":
                resp = requests.post(url, headers=self._headers(),
                                     json=json_data, timeout=10)
            elif method == "DELETE":
                resp = requests.delete(url, headers=self._headers(), timeout=10)
            else:
                return None

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 204:
                return {"status": "success"}
            else:
                print(f"[T212] {method} {endpoint} → {resp.status_code}: {resp.text}")
                return None
        except Exception as e:
            print(f"[T212] Request error: {e}")
            return None

    # ─── Account ──────────────────────────────────────────────────────

    def get_account_info(self) -> Optional[dict]:
        """Get account cash balance and metadata."""
        return self._request("GET", "equity/account/cash")

    def get_portfolio(self) -> Optional[list]:
        """Get all open positions."""
        return self._request("GET", "equity/portfolio")

    # ─── Instruments ──────────────────────────────────────────────────

    def search_instrument(self, query: str) -> Optional[list]:
        """Search for instruments by name or ticker."""
        data = self._request("GET", f"equity/metadata/instruments/search?query={query}")
        return data

    def get_instruments(self) -> Optional[list]:
        """Get all available instruments."""
        return self._request("GET", "equity/metadata/instruments")

    # ─── Orders ───────────────────────────────────────────────────────

    def place_market_order(self, ticker: str, quantity: float) -> OrderResult:
        """
        Place a market buy order.
        For ISA: quantity must be positive (buy) or negative (sell).
        """
        data = {
            "quantity": quantity,
            "ticker": ticker,
        }
        result = self._request("POST", "equity/orders/market", data)

        if result and "id" in result:
            fill_price = result.get("fillPrice", result.get("limitPrice", 0))
            fill_qty = result.get("filledQuantity", quantity)
            return OrderResult(
                success=True,
                order_id=str(result["id"]),
                ticker=ticker,
                action="BUY" if quantity > 0 else "SELL",
                quantity=abs(fill_qty),
                price=fill_price,
                value_gbp=abs(fill_qty * fill_price),
                message=f"Order filled: {result.get('status', 'unknown')}",
            )
        return OrderResult(
            success=False, order_id=None, ticker=ticker,
            action="BUY" if quantity > 0 else "SELL",
            quantity=abs(quantity), price=0, value_gbp=0,
            message=f"Order failed: {result}",
        )

    def place_limit_order(self, ticker: str, quantity: float,
                          limit_price: float,
                          time_validity: str = "DAY") -> OrderResult:
        """Place a limit order."""
        data = {
            "quantity": quantity,
            "ticker": ticker,
            "limitPrice": limit_price,
            "timeValidity": time_validity,
        }
        result = self._request("POST", "equity/orders/limit", data)

        if result and "id" in result:
            return OrderResult(
                success=True,
                order_id=str(result["id"]),
                ticker=ticker,
                action="BUY" if quantity > 0 else "SELL",
                quantity=abs(quantity),
                price=limit_price,
                value_gbp=abs(quantity * limit_price),
                message=f"Limit order placed: {result.get('status', 'unknown')}",
            )
        return OrderResult(
            success=False, order_id=None, ticker=ticker,
            action="BUY" if quantity > 0 else "SELL",
            quantity=abs(quantity), price=limit_price,
            value_gbp=abs(quantity * limit_price),
            message=f"Order failed: {result}",
        )

    def place_value_order(self, ticker: str, value_gbp: float) -> OrderResult:
        """
        Buy a specific GBP value of a stock (fractional shares).
        T212 supports fractional shares on most instruments.
        """
        data = {
            "value": value_gbp,
            "ticker": ticker,
        }
        result = self._request("POST", "equity/orders/market/value", data)

        if result and "id" in result:
            fill_price = result.get("fillPrice", 0)
            fill_qty = result.get("filledQuantity", 0)
            return OrderResult(
                success=True,
                order_id=str(result["id"]),
                ticker=ticker,
                action="BUY" if value_gbp > 0 else "SELL",
                quantity=fill_qty,
                price=fill_price,
                value_gbp=abs(value_gbp),
                message=f"Value order filled",
            )
        return OrderResult(
            success=False, order_id=None, ticker=ticker,
            action="BUY", quantity=0, price=0, value_gbp=abs(value_gbp),
            message=f"Order failed: {result}",
        )

    def sell_position(self, ticker: str) -> OrderResult:
        """Sell entire position in a ticker."""
        portfolio = self.get_portfolio()
        if not portfolio:
            return OrderResult(
                success=False, order_id=None, ticker=ticker,
                action="SELL", quantity=0, price=0, value_gbp=0,
                message="Could not fetch portfolio",
            )

        position = None
        for pos in portfolio:
            if pos.get("ticker") == ticker:
                position = pos
                break

        if not position:
            return OrderResult(
                success=False, order_id=None, ticker=ticker,
                action="SELL", quantity=0, price=0, value_gbp=0,
                message=f"No position found for {ticker}",
            )

        quantity = position.get("quantity", 0)
        if quantity <= 0:
            return OrderResult(
                success=False, order_id=None, ticker=ticker,
                action="SELL", quantity=0, price=0, value_gbp=0,
                message="Position quantity is zero",
            )

        return self.place_market_order(ticker, -quantity)

    # ─── Order Management ─────────────────────────────────────────────

    def get_orders(self) -> Optional[list]:
        """Get all pending orders."""
        return self._request("GET", "equity/orders")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        result = self._request("DELETE", f"equity/orders/{order_id}")
        return result is not None

    # ─── History ──────────────────────────────────────────────────────

    def get_order_history(self, limit: int = 50) -> Optional[dict]:
        """Get historical orders."""
        return self._request("GET", f"equity/history/orders?limit={limit}")

    def get_dividend_history(self) -> Optional[dict]:
        """Get dividend payment history."""
        return self._request("GET", "equity/history/dividends")
