"""
Portfolio Tracker — P&L calculation, benchmark comparison, and performance metrics.
Compares emulator performance against VUSA (S&P 500) on daily/monthly/quarterly/annual views.
"""

import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from typing import Optional

import config
from market_data import get_stock_data


def get_benchmark_returns(period: str = "1y") -> pd.DataFrame:
    """Get VUSA benchmark returns for comparison."""
    df = get_stock_data(config.BENCHMARK_TICKER, period=period, interval="1d")
    if df is None or df.empty:
        return pd.DataFrame()
    df["benchmark_return"] = df["Close"].pct_change()
    df["benchmark_cumulative"] = (1 + df["benchmark_return"]).cumprod() - 1
    return df[["Close", "benchmark_return", "benchmark_cumulative"]].rename(
        columns={"Close": "benchmark_price"}
    )


def calculate_portfolio_history(trade_history: list, initial_capital: float) -> pd.DataFrame:
    """
    Build a daily portfolio value series from trade history.
    Returns DataFrame with date index and columns: portfolio_value, daily_return, cumulative_return.
    """
    if not trade_history:
        today = date.today()
        idx = pd.date_range(start=today, periods=1, freq="D")
        return pd.DataFrame({
            "portfolio_value": [initial_capital],
            "daily_return": [0.0],
            "cumulative_return": [0.0],
            "daily_pnl": [0.0],
            "cumulative_pnl": [0.0],
        }, index=idx)

    # Group trades by date and compute daily P&L
    daily_pnl = {}
    for trade in trade_history:
        trade_date = trade.timestamp[:10] if isinstance(trade.timestamp, str) else trade.timestamp.date().isoformat()
        if trade_date not in daily_pnl:
            daily_pnl[trade_date] = 0
        if hasattr(trade, 'pnl'):
            daily_pnl[trade_date] += trade.pnl

    if not daily_pnl:
        return pd.DataFrame()

    # Build time series
    dates = sorted(daily_pnl.keys())
    start = pd.to_datetime(dates[0])
    end = pd.to_datetime(max(dates[-1], date.today().isoformat()))
    idx = pd.date_range(start=start, end=end, freq="D")

    values = []
    cumulative = initial_capital
    for d in idx:
        d_str = d.strftime("%Y-%m-%d")
        pnl = daily_pnl.get(d_str, 0)
        cumulative += pnl
        values.append({
            "date": d,
            "portfolio_value": cumulative,
            "daily_pnl": pnl,
        })

    df = pd.DataFrame(values).set_index("date")
    df["daily_return"] = df["portfolio_value"].pct_change().fillna(0)
    df["cumulative_return"] = (df["portfolio_value"] / initial_capital - 1)
    df["cumulative_pnl"] = df["portfolio_value"] - initial_capital
    return df


def performance_vs_benchmark(portfolio_df: pd.DataFrame,
                              benchmark_df: pd.DataFrame) -> pd.DataFrame:
    """Merge portfolio and benchmark for direct comparison."""
    if portfolio_df.empty or benchmark_df.empty:
        return pd.DataFrame()

    # Align indices (both are DatetimeIndex)
    portfolio_df.index = pd.to_datetime(portfolio_df.index).tz_localize(None)
    benchmark_df.index = pd.to_datetime(benchmark_df.index).tz_localize(None)

    merged = portfolio_df.join(benchmark_df, how="outer")
    merged = merged.ffill().dropna(subset=["portfolio_value", "benchmark_price"])
    return merged


def compute_period_returns(portfolio_df: pd.DataFrame,
                            benchmark_df: pd.DataFrame) -> dict:
    """
    Compute returns for different time periods.
    Returns dict with daily, monthly, quarterly, annual views.
    """
    if portfolio_df.empty:
        return _empty_returns()

    today = pd.Timestamp(date.today())
    result = {}

    periods = {
        "daily": today - timedelta(days=1),
        "weekly": today - timedelta(weeks=1),
        "monthly": today - timedelta(days=30),
        "quarterly": today - timedelta(days=90),
        "annual": today - timedelta(days=365),
        "all_time": portfolio_df.index.min(),
    }

    for period_name, start_date in periods.items():
        port_slice = portfolio_df[portfolio_df.index >= start_date]
        bench_slice = benchmark_df[benchmark_df.index >= start_date] if not benchmark_df.empty else pd.DataFrame()

        if port_slice.empty or len(port_slice) < 2:
            result[period_name] = {
                "portfolio_return": 0.0,
                "benchmark_return": 0.0,
                "alpha": 0.0,
                "portfolio_pnl": 0.0,
            }
            continue

        port_return = (
            port_slice["portfolio_value"].iloc[-1] /
            port_slice["portfolio_value"].iloc[0] - 1
        ) * 100

        bench_return = 0.0
        if not bench_slice.empty and len(bench_slice) >= 2 and "benchmark_price" in bench_slice.columns:
            bench_return = (
                bench_slice["benchmark_price"].iloc[-1] /
                bench_slice["benchmark_price"].iloc[0] - 1
            ) * 100

        result[period_name] = {
            "portfolio_return": round(port_return, 2),
            "benchmark_return": round(bench_return, 2),
            "alpha": round(port_return - bench_return, 2),
            "portfolio_pnl": round(
                port_slice["portfolio_value"].iloc[-1] -
                port_slice["portfolio_value"].iloc[0], 2
            ),
        }

    return result


def compute_risk_metrics(portfolio_df: pd.DataFrame) -> dict:
    """Compute risk-adjusted performance metrics."""
    if portfolio_df.empty or len(portfolio_df) < 5:
        return {"sharpe_ratio": 0, "max_drawdown": 0, "win_rate": 0, "avg_daily_return": 0}

    returns = portfolio_df["daily_return"].dropna()

    # Sharpe ratio (annualised, assuming 252 trading days, risk-free ~4.5% UK)
    risk_free_daily = 0.045 / 252
    excess = returns - risk_free_daily
    sharpe = (excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0

    # Max drawdown
    cum_returns = (1 + returns).cumprod()
    peak = cum_returns.expanding().max()
    drawdown = (cum_returns - peak) / peak
    max_dd = drawdown.min() * 100

    # Win rate
    trading_days = returns[returns != 0]
    win_rate = (trading_days > 0).mean() * 100 if len(trading_days) > 0 else 0

    return {
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown": round(max_dd, 2),
        "win_rate": round(win_rate, 1),
        "avg_daily_return": round(returns.mean() * 100, 3),
        "volatility_annual": round(returns.std() * np.sqrt(252) * 100, 2),
    }


def _empty_returns():
    periods = ["daily", "weekly", "monthly", "quarterly", "annual", "all_time"]
    return {
        p: {"portfolio_return": 0, "benchmark_return": 0, "alpha": 0, "portfolio_pnl": 0}
        for p in periods
    }
