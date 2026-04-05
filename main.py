"""
Main entry point — run the trading engine on a schedule or as a one-shot scan.

Usage:
    python main.py              # One-shot scan
    python main.py --schedule   # Run on schedule during market hours
    streamlit run dashboard.py  # Launch the web dashboard
"""

import argparse
import time
from datetime import datetime

import schedule

import config
from trading_engine import TradingEngine


def is_market_hours() -> bool:
    """Check if current time is within LSE trading hours (UK time)."""
    now = datetime.now()
    market_open = datetime.strptime(config.MARKET_OPEN, "%H:%M").time()
    market_close = datetime.strptime(config.MARKET_CLOSE, "%H:%M").time()
    # Skip weekends
    if now.weekday() >= 5:
        return False
    return market_open <= now.time() <= market_close


def run_scan(engine: TradingEngine):
    """Execute a single scan cycle."""
    if not is_market_hours():
        print(f"[{datetime.now().strftime('%H:%M')}] Outside market hours — skipping scan")
        return

    decisions = engine.run_scan()

    # Print summary
    buys = [d for d in decisions if d.action == "BUY"]
    if buys:
        print("\n--- BUY Recommendations ---")
        for d in buys:
            print(f"  {d.ticker}: Score {d.combined_score:.1f} | "
                  f"£{d.entry_price:.2f} → Target £{d.take_profit:.2f} | "
                  f"Size: £{d.position_size_gbp:.2f}")

    summary = engine.emulator.get_summary()
    risk = engine.risk.get_status()
    print(f"\nPortfolio: £{summary['portfolio_value']:,.2f} | "
          f"P&L: £{summary['total_pnl']:+,.2f} ({summary['total_pnl_pct']:+.2f}%) | "
          f"Daily P&L: £{risk['daily_pnl']:+,.2f} | "
          f"Target: {risk['attainment_pct']:.0f}%")


def main():
    parser = argparse.ArgumentParser(description="AI Trading Tool")
    parser.add_argument("--schedule", action="store_true",
                        help="Run on schedule during market hours")
    parser.add_argument("--interval", type=int, default=config.SCAN_INTERVAL_MINUTES,
                        help=f"Scan interval in minutes (default: {config.SCAN_INTERVAL_MINUTES})")
    args = parser.parse_args()

    engine = TradingEngine()

    print("=" * 60)
    print("  AI Trading Tool — Paper Trading Mode")
    print("=" * 60)
    print(f"  Capital: £{config.INITIAL_CAPITAL:,.0f}")
    print(f"  Universe: {len(config.STOCK_UNIVERSE)} stocks")
    print(f"  Consensus: {config.CONSENSUS_MODE} mode")
    print(f"  Daily target: £{config.DAILY_PROFIT_TARGET_MIN}-{config.DAILY_PROFIT_TARGET_MAX}")
    print(f"  Daily stop-loss: £{abs(config.DAILY_STOP_LOSS)}")
    print(f"  AI: Claude (emulated) + Grok (emulated)")
    print("=" * 60)

    if args.schedule:
        print(f"\nScheduled mode: scanning every {args.interval} minutes during market hours")
        print(f"Market hours: {config.MARKET_OPEN} - {config.MARKET_CLOSE} (Mon-Fri)")

        schedule.every(args.interval).minutes.do(run_scan, engine)

        # Run immediately
        run_scan(engine)

        while True:
            schedule.run_pending()
            time.sleep(30)
    else:
        # One-shot scan
        run_scan(engine)


if __name__ == "__main__":
    main()
