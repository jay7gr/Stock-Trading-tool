"""
Market Data Module — fetches real price data and technical indicators.

Primary:  Twelve Data API (free, reliable, 800 req/day)
Fallback: yfinance (free, less reliable)
"""

import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional

import config


# ─── Twelve Data API ──────────────────────────────────────────────────

class TwelveDataClient:
    """Free tier: 800 requests/day, 8 per minute."""

    BASE_URL = "https://api.twelvedata.com"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or config.TWELVE_DATA_API_KEY
        self.requests_today = 0
        self.last_request_time = 0

    def _rate_limit(self):
        """Respect 8 requests/minute limit."""
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < 7.5:  # ~8 req/min = 1 per 7.5s
            time.sleep(7.5 - elapsed)
        self.last_request_time = time.time()

    def _request(self, endpoint: str, params: dict) -> Optional[dict]:
        if not self.api_key:
            return None
        if self.requests_today >= 780:  # Leave buffer
            print("[TwelveData] Daily quota nearly exhausted, skipping")
            return None

        self._rate_limit()
        params["apikey"] = self.api_key

        try:
            resp = requests.get(f"{self.BASE_URL}/{endpoint}", params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            self.requests_today += 1

            if "status" in data and data["status"] == "error":
                print(f"[TwelveData] API error: {data.get('message', 'unknown')}")
                return None
            return data
        except Exception as e:
            print(f"[TwelveData] Request failed: {e}")
            return None

    def get_time_series(self, symbol: str, interval: str = "1day",
                        outputsize: int = 60) -> Optional[pd.DataFrame]:
        """Fetch OHLCV time series data."""
        td_symbol = config.TWELVE_DATA_SYMBOLS.get(symbol, symbol.replace(".L", ""))
        data = self._request("time_series", {
            "symbol": td_symbol,
            "exchange": config.TWELVE_DATA_EXCHANGE,
            "interval": interval,
            "outputsize": outputsize,
            "format": "JSON",
        })
        if not data or "values" not in data:
            return None

        rows = data["values"]
        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })
        return df

    def get_price(self, symbol: str) -> Optional[float]:
        """Get latest price for a symbol."""
        td_symbol = config.TWELVE_DATA_SYMBOLS.get(symbol, symbol.replace(".L", ""))
        data = self._request("price", {
            "symbol": td_symbol,
            "exchange": config.TWELVE_DATA_EXCHANGE,
        })
        if data and "price" in data:
            return float(data["price"])
        return None

    def get_quote(self, symbol: str) -> Optional[dict]:
        """Get full quote (price, volume, change, etc.)."""
        td_symbol = config.TWELVE_DATA_SYMBOLS.get(symbol, symbol.replace(".L", ""))
        data = self._request("quote", {
            "symbol": td_symbol,
            "exchange": config.TWELVE_DATA_EXCHANGE,
        })
        return data

    def get_indicator(self, symbol: str, indicator: str,
                      interval: str = "1day", **kwargs) -> Optional[dict]:
        """Get a specific technical indicator calculated server-side."""
        td_symbol = config.TWELVE_DATA_SYMBOLS.get(symbol, symbol.replace(".L", ""))
        params = {
            "symbol": td_symbol,
            "exchange": config.TWELVE_DATA_EXCHANGE,
            "interval": interval,
            **kwargs,
        }
        return self._request(indicator, params)


# ─── yfinance Fallback ────────────────────────────────────────────────

def _yfinance_get_data(ticker: str, period: str = "3mo",
                       interval: str = "1d") -> Optional[pd.DataFrame]:
    """Fallback: fetch data via yfinance."""
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, interval=interval)
        if df.empty:
            return None
        return df
    except Exception as e:
        print(f"[yfinance] Error fetching {ticker}: {e}")
        return None


def _yfinance_get_price(ticker: str) -> Optional[float]:
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info = stock.fast_info
        return float(info.get("lastPrice", 0) or info.get("previousClose", 0))
    except Exception:
        try:
            df = _yfinance_get_data(ticker, period="5d")
            if df is not None and not df.empty:
                return float(df["Close"].iloc[-1])
        except Exception:
            pass
    return None


def _yfinance_get_info(ticker: str) -> dict:
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "name": info.get("shortName", ticker),
            "sector": info.get("sector", "Unknown"),
            "market_cap": info.get("marketCap", 0),
            "pe_ratio": info.get("trailingPE"),
            "dividend_yield": info.get("dividendYield"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume", 0),
            "beta": info.get("beta", 1.0),
        }
    except Exception as e:
        return {"name": ticker, "error": str(e)}


# ─── Unified Interface ───────────────────────────────────────────────

# Global Twelve Data client
_td_client: Optional[TwelveDataClient] = None


def _get_td_client() -> Optional[TwelveDataClient]:
    global _td_client
    if _td_client is None and config.TWELVE_DATA_API_KEY:
        _td_client = TwelveDataClient()
    return _td_client


def get_stock_data(ticker: str, period: str = "3mo",
                   interval: str = "1d") -> Optional[pd.DataFrame]:
    """Fetch OHLCV data. Tries Twelve Data first, falls back to yfinance."""
    td = _get_td_client()
    if td:
        outputsize = {"1mo": 22, "3mo": 60, "6mo": 126, "1y": 252}.get(period, 60)
        td_interval = {"1d": "1day", "1h": "1h", "15m": "15min"}.get(interval, "1day")
        df = td.get_time_series(ticker, interval=td_interval, outputsize=outputsize)
        if df is not None and not df.empty:
            return df
        print(f"[MarketData] Twelve Data failed for {ticker}, trying yfinance")

    return _yfinance_get_data(ticker, period=period, interval=interval)


def get_current_price(ticker: str) -> Optional[float]:
    """Get latest price. Tries Twelve Data first, falls back to yfinance."""
    td = _get_td_client()
    if td:
        price = td.get_price(ticker)
        if price:
            return price

    return _yfinance_get_price(ticker)


def get_stock_info(ticker: str) -> dict:
    """Get fundamental info. Uses yfinance (Twelve Data free tier limited)."""
    return _yfinance_get_info(ticker)


def compute_technicals(df: pd.DataFrame) -> pd.DataFrame:
    """Add technical indicators to a price DataFrame."""
    if df is None or df.empty or len(df) < 20:
        return df

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # Moving averages
    df["SMA_10"] = close.rolling(window=10).mean()
    df["SMA_20"] = close.rolling(window=20).mean()
    df["SMA_50"] = close.rolling(window=50).mean()
    df["EMA_12"] = close.ewm(span=12, adjust=False).mean()
    df["EMA_26"] = close.ewm(span=26, adjust=False).mean()

    # MACD
    df["MACD"] = df["EMA_12"] - df["EMA_26"]
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]

    # RSI (14-period)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))

    # Bollinger Bands (20-period, 2 std)
    df["BB_Mid"] = close.rolling(window=20).mean()
    bb_std = close.rolling(window=20).std()
    df["BB_Upper"] = df["BB_Mid"] + 2 * bb_std
    df["BB_Lower"] = df["BB_Mid"] - 2 * bb_std
    df["BB_Width"] = (df["BB_Upper"] - df["BB_Lower"]) / df["BB_Mid"]

    # Average True Range (ATR)
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["ATR"] = true_range.rolling(window=14).mean()

    # Volume moving average
    df["Vol_SMA_20"] = volume.rolling(window=20).mean()
    df["Vol_Ratio"] = volume / df["Vol_SMA_20"]

    # Price rate of change
    df["ROC_5"] = close.pct_change(periods=5) * 100
    df["ROC_10"] = close.pct_change(periods=10) * 100

    # Stochastic Oscillator
    low_14 = low.rolling(window=14).min()
    high_14 = high.rolling(window=14).max()
    df["Stoch_K"] = 100 * (close - low_14) / (high_14 - low_14)
    df["Stoch_D"] = df["Stoch_K"].rolling(window=3).mean()

    return df


def get_benchmark_data(period: str = "1y") -> Optional[pd.DataFrame]:
    """Fetch VUSA benchmark data."""
    return get_stock_data(config.BENCHMARK_TICKER, period=period)


def scan_universe(tickers: list[str]) -> dict[str, dict]:
    """Scan all tickers and return dict of ticker -> {price, df, info}."""
    results = {}
    for ticker in tickers:
        df = get_stock_data(ticker, period="3mo", interval="1d")
        if df is None or df.empty:
            continue
        df = compute_technicals(df)
        price = float(df["Close"].iloc[-1])
        results[ticker] = {
            "price": price,
            "df": df,
            "info": get_stock_info(ticker),
        }
    return results
