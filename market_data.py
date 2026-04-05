"""
Market Data Module — fetches real price data, technicals, and fundamentals.
Uses yfinance for free LSE/global market data.
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional


def get_stock_data(ticker: str, period: str = "3mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    """Fetch OHLCV data for a ticker."""
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, interval=interval)
        if df.empty:
            return None
        return df
    except Exception as e:
        print(f"[MarketData] Error fetching {ticker}: {e}")
        return None


def get_intraday_data(ticker: str, period: str = "5d", interval: str = "15m") -> Optional[pd.DataFrame]:
    """Fetch intraday data for short-term signals."""
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, interval=interval)
        if df.empty:
            return None
        return df
    except Exception as e:
        print(f"[MarketData] Error fetching intraday {ticker}: {e}")
        return None


def get_current_price(ticker: str) -> Optional[float]:
    """Get the latest available price for a ticker."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.fast_info
        return float(info.get("lastPrice", 0) or info.get("previousClose", 0))
    except Exception:
        try:
            df = get_stock_data(ticker, period="5d", interval="1d")
            if df is not None and not df.empty:
                return float(df["Close"].iloc[-1])
        except Exception:
            pass
    return None


def compute_technicals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add technical indicators to a price DataFrame.
    Returns the same DataFrame with new columns added.
    """
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

    # Average True Range (ATR) — volatility measure
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


def get_stock_info(ticker: str) -> dict:
    """Get fundamental info for a stock."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "name": info.get("shortName", ticker),
            "sector": info.get("sector", "Unknown"),
            "market_cap": info.get("marketCap", 0),
            "pe_ratio": info.get("trailingPE", None),
            "dividend_yield": info.get("dividendYield", None),
            "52w_high": info.get("fiftyTwoWeekHigh", None),
            "52w_low": info.get("fiftyTwoWeekLow", None),
            "avg_volume": info.get("averageVolume", 0),
            "beta": info.get("beta", 1.0),
        }
    except Exception as e:
        return {"name": ticker, "error": str(e)}


def get_benchmark_data(period: str = "1y") -> Optional[pd.DataFrame]:
    """Fetch VUSA (S&P 500 ETF) benchmark data."""
    from config import BENCHMARK_TICKER
    return get_stock_data(BENCHMARK_TICKER, period=period)


def scan_universe(tickers: list[str]) -> dict[str, dict]:
    """
    Scan all tickers in the universe and return a dict of
    ticker -> {price, technicals_df, info}.
    """
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
