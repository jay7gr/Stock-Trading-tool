"""
Demo script — generates realistic mock data to show what the dashboard looks like.
Run this to see the full system output without needing live market data.

Usage: python demo.py
"""

import random
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

import config
from strategies import Signal
from ai_analyst import AIAnalysis
from consensus import TradeDecision


def generate_mock_price_history(base_price: float, days: int = 60) -> pd.DataFrame:
    """Generate realistic OHLCV data."""
    dates = pd.date_range(end=datetime.now(), periods=days, freq="D")
    prices = [base_price]
    for _ in range(days - 1):
        change = random.gauss(0.0003, 0.015)
        prices.append(prices[-1] * (1 + change))

    df = pd.DataFrame({
        "Open": [p * random.uniform(0.998, 1.002) for p in prices],
        "High": [p * random.uniform(1.001, 1.02) for p in prices],
        "Low": [p * random.uniform(0.98, 0.999) for p in prices],
        "Close": prices,
        "Volume": [random.randint(500_000, 5_000_000) for _ in prices],
    }, index=dates)

    # Add technicals
    from market_data import compute_technicals
    df = compute_technicals(df)
    return df


def run_demo():
    print("=" * 70)
    print("  AI TRADING TOOL — DEMO (Mock Data)")
    print("  This shows what you will see when running on your local machine")
    print("=" * 70)
    print()

    # Mock stock universe with realistic prices
    mock_stocks = {
        "SHEL.L": {"name": "Shell", "price": 2654, "sector": "Energy", "pe": 12.3, "beta": 0.9},
        "AZN.L": {"name": "AstraZeneca", "price": 11890, "sector": "Healthcare", "pe": 35.2, "beta": 0.6},
        "HSBA.L": {"name": "HSBC", "price": 742, "sector": "Financials", "pe": 8.1, "beta": 1.1},
        "BP.L": {"name": "BP", "price": 412, "sector": "Energy", "pe": 11.5, "beta": 1.0},
        "BARC.L": {"name": "Barclays", "price": 268, "sector": "Financials", "pe": 7.4, "beta": 1.3},
        "BA.L": {"name": "BAE Systems", "price": 1456, "sector": "Defence", "pe": 22.1, "beta": 0.7},
        "LLOY.L": {"name": "Lloyds", "price": 62, "sector": "Financials", "pe": 8.8, "beta": 1.2},
        "RIO.L": {"name": "Rio Tinto", "price": 5234, "sector": "Mining", "pe": 9.2, "beta": 1.1},
        "GLEN.L": {"name": "Glencore", "price": 387, "sector": "Mining", "pe": 10.5, "beta": 1.4},
        "VUSA.L": {"name": "Vanguard S&P 500", "price": 8456, "sector": "ETF", "pe": None, "beta": 1.0},
    }

    decisions = []

    for ticker, info in mock_stocks.items():
        price = info["price"]
        df = generate_mock_price_history(price)
        latest = df.iloc[-1]

        # Generate signals
        from strategies import generate_all_signals
        signals = generate_all_signals(ticker, df, price)

        # Emulate AI analysis
        from ai_analyst import claude_analyse, grok_analyse
        mock_info = {
            "name": info["name"], "sector": info["sector"],
            "pe_ratio": info["pe"], "beta": info["beta"],
            "dividend_yield": random.uniform(0.01, 0.05),
            "52w_high": price * 1.15, "52w_low": price * 0.82,
        }
        claude = claude_analyse(ticker, df, price, signals, mock_info)
        grok = grok_analyse(ticker, df, price, signals, mock_info)

        # Consensus
        from consensus import make_decision
        decision = make_decision(ticker, price, signals, claude, grok, 20000)
        decisions.append(decision)

    # Sort by score
    decisions.sort(key=lambda d: (-1 if d.action == "BUY" else 1, -d.combined_score))

    # ─── Display ──────────────────────────────────────────────────────

    # Simulate some executed trades
    buys = [d for d in decisions if d.action == "BUY"]
    sells = [d for d in decisions if d.action == "SELL"]
    holds = [d for d in decisions if d.action == "HOLD"]

    print(f"SCAN RESULTS: {len(buys)} BUY | {len(sells)} SELL | {len(holds)} HOLD")
    print()

    # ─── Portfolio Overview ───────────────────────────────────────────
    print("┌────────────────────────────────────────────────────────────────┐")
    print("│  PORTFOLIO OVERVIEW                                           │")
    print("├────────────────────────────────────────────────────────────────┤")

    # Simulate some prior trades
    simulated_pnl = random.uniform(-50, 180)
    portfolio_val = 20000 + simulated_pnl
    cash = portfolio_val - sum(d.position_size_gbp for d in buys[:3])

    print(f"│  Portfolio Value:   £{portfolio_val:>10,.2f}                          │")
    print(f"│  Cash Available:    £{cash:>10,.2f}                          │")
    print(f"│  Today's P&L:       £{simulated_pnl:>+10,.2f}                          │")
    print(f"│  Total Return:       {simulated_pnl/20000*100:>+9.2f}%                          │")
    print(f"│  Open Positions:     {min(len(buys), 3):>9d}                            │")
    print(f"│  Daily Target:       {max(0, simulated_pnl/200*100):>8.0f}%                            │")
    print("└────────────────────────────────────────────────────────────────┘")
    print()

    # ─── Recommendations ─────────────────────────────────────────────
    print("┌────────────────────────────────────────────────────────────────┐")
    print("│  RECOMMENDATIONS                                              │")
    print("└────────────────────────────────────────────────────────────────┘")

    for d in decisions:
        icon = {"BUY": "▲ BUY ", "SELL": "▼ SELL", "HOLD": "● HOLD"}[d.action]
        print()
        print(f"  {icon}  {d.ticker:<10s} │ Score: {d.combined_score:.1f}/10 │ Price: £{d.entry_price:,.2f}")
        print(f"          Claude: {d.claude_analysis.score:.1f}/10  │  Grok: {d.grok_analysis.score:.1f}/10")

        if d.action == "BUY":
            rr = ((d.take_profit - d.entry_price) / max(d.entry_price - d.stop_loss, 0.01))
            print(f"          Entry: £{d.entry_price:,.2f}  │  Stop: £{d.stop_loss:,.2f}  │  Target: £{d.take_profit:,.2f}")
            print(f"          Position: £{d.position_size_gbp:,.2f}  │  Risk/Reward: {rr:.1f}:1")

        print(f"  ┌─ REASONING ────────────────────────────────────────────")
        for line in d.reasoning.split("\n"):
            if line.strip():
                print(f"  │ {line}")
        print(f"  └────────────────────────────────────────────────────────")

    # ─── Benchmark Comparison ─────────────────────────────────────────
    print()
    print("┌────────────────────────────────────────────────────────────────┐")
    print("│  BENCHMARK COMPARISON — Portfolio vs VUSA (S&P 500)           │")
    print("├──────────┬──────────────┬──────────────┬───────────┬──────────┤")
    print("│ Period   │  Portfolio   │  VUSA (S&P)  │   Alpha   │   P&L    │")
    print("├──────────┼──────────────┼──────────────┼───────────┼──────────┤")

    periods = [
        ("Daily",    random.uniform(-0.5, 0.8), random.uniform(-0.3, 0.3)),
        ("Weekly",   random.uniform(-1.0, 2.5), random.uniform(-0.5, 1.0)),
        ("Monthly",  random.uniform(-2.0, 5.0), random.uniform(-1.0, 2.0)),
        ("Quarterly",random.uniform(-3.0, 8.0), random.uniform(-2.0, 4.0)),
        ("Annual",   random.uniform(-5.0, 25.0), random.uniform(5.0, 12.0)),
    ]
    for name, port_ret, bench_ret in periods:
        alpha = port_ret - bench_ret
        pnl = 20000 * port_ret / 100
        print(f"│ {name:<8s} │ {port_ret:>+10.2f}%  │ {bench_ret:>+10.2f}%  │ {alpha:>+7.2f}%  │ £{pnl:>+7.0f} │")

    print("└──────────┴──────────────┴──────────────┴───────────┴──────────┘")
    print()

    # ─── Risk Metrics ─────────────────────────────────────────────────
    print("┌────────────────────────────────────────────────────────────────┐")
    print("│  RISK METRICS                                                 │")
    print("├────────────────────────────────────────────────────────────────┤")
    print(f"│  Sharpe Ratio:        {random.uniform(0.8, 2.5):>6.2f}                              │")
    print(f"│  Max Drawdown:        {random.uniform(-8, -2):>+5.1f}%                              │")
    print(f"│  Win Rate:            {random.uniform(52, 68):>5.1f}%                              │")
    print(f"│  Avg Daily Return:    {random.uniform(0.05, 0.2):>+5.3f}%                             │")
    print(f"│  Annual Volatility:   {random.uniform(8, 18):>5.1f}%                              │")
    print("└────────────────────────────────────────────────────────────────┘")
    print()

    print("=" * 70)
    print("  To see the full interactive dashboard with charts, run:")
    print("    streamlit run dashboard.py")
    print("=" * 70)


if __name__ == "__main__":
    run_demo()
