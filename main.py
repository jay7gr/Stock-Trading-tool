"""
Main entry point.

Commands:
    python main.py radar            # One-shot spike radar scan of Russell
    python main.py brief            # Generate PM daily brief
    python main.py watch            # Continuous spike monitoring with alerts
    python main.py scan             # Legacy UK ISA quant scan (FTSE universe)
    python main.py scan --schedule  # Legacy scan on schedule

Dashboard:
    streamlit run dashboard.py
"""

import argparse
import time
from datetime import datetime

import config


# ─── Spike Radar Command ─────────────────────────────────────────────

def cmd_radar(args):
    """One-shot spike radar scan."""
    from spike_radar import scan_for_spikes, alerts_only
    from notifications import Notifier

    print("=" * 60)
    print("  🚨 SPIKE RADAR — US Russell Scanner")
    print("=" * 60)

    candidates = scan_for_spikes()

    if not candidates:
        print("\nNo spike candidates detected.")
        return

    # Filter to only high-conviction
    alerts = alerts_only(candidates)

    # Send alerts via Telegram if configured
    if alerts and config.TELEGRAM_BOT_TOKEN:
        notifier = Notifier()
        for c in alerts[:5]:
            msg = (
                f"🚨 SPIKE ALERT — ${c.ticker}\n"
                f"Score {c.spike_score:.0f}/100 | {c.action}\n"
                f"Price ${c.price:.2f} ({c.pct_change:+.1f}% today)\n"
                f"Volume {c.volume_ratio:.1f}x avg\n"
                f"{' | '.join(c.reasoning[:4])}"
            )
            # Use trade_alert format or generic message
            try:
                notifier.risk_alert(msg)
            except Exception as e:
                print(f"[Notify] failed: {e}")


# ─── Daily Brief Command ─────────────────────────────────────────────

def cmd_brief(args):
    """Generate the morning PM brief."""
    from pm_brief import generate_brief, save_brief
    from emulator import PaperTradingEmulator

    print("=" * 60)
    print("  📊 PORTFOLIO MANAGER DAILY BRIEF")
    print("=" * 60)

    # Pull current positions for portfolio health check
    emulator = PaperTradingEmulator()
    positions = {
        t: {
            "current_price": p.current_price,
            "pnl_pct": p.unrealised_pnl_pct,
            "value": p.value_gbp,
        }
        for t, p in emulator.positions.items()
    }

    brief = generate_brief(
        portfolio_positions=positions,
        include_spike_scan=not args.skip_scan,
    )

    md_path, json_path = save_brief(brief)
    print(f"\n✓ Brief saved to {md_path}")

    print("\n" + "=" * 70)
    print(brief.to_markdown())

    # Send to Telegram if configured
    if config.TELEGRAM_BOT_TOKEN and not args.no_notify:
        try:
            from notifications import TelegramNotifier
            notifier = TelegramNotifier()
            # Send summary — full brief is too long for one Telegram message
            summary = f"📊 Daily Brief — {brief.date}\n\n"
            for section in brief.sections[:3]:  # First 3 sections
                summary += f"*{section.title}*\n{section.content[:500]}\n\n"
            notifier.send(summary[:4000])
        except Exception as e:
            print(f"[Notify] {e}")


# ─── Watch Mode (Continuous Spike Monitor) ───────────────────────────

def cmd_watch(args):
    """Continuously scan for spikes during market hours."""
    from spike_radar import scan_for_spikes, alerts_only
    from notifications import Notifier

    interval = args.interval or config.INTRADAY_ALERT_INTERVAL_MIN
    notifier = Notifier()
    seen_alerts = set()

    print(f"[Watch] Starting continuous spike monitor (every {interval} min)")
    print(f"[Watch] Market hours: US (13:30-20:00 UTC)")

    while True:
        try:
            now = datetime.utcnow()
            # US market hours in UTC: 13:30 - 20:00
            is_us_market = (now.weekday() < 5 and
                           (13, 30) <= (now.hour, now.minute) <= (20, 0))

            if is_us_market:
                candidates = scan_for_spikes()
                alerts = alerts_only(candidates)

                for c in alerts:
                    # Dedupe: don't re-alert on the same spike
                    key = f"{c.ticker}_{now.strftime('%Y%m%d')}"
                    if key in seen_alerts:
                        continue
                    seen_alerts.add(key)

                    msg = (
                        f"🚨 ${c.ticker} | Score {c.spike_score:.0f} | "
                        f"{c.pct_change:+.1f}% on {c.volume_ratio:.1f}x vol\n"
                        f"{' | '.join(c.reasoning[:3])}"
                    )
                    print(f"[ALERT] {msg}")
                    if config.TELEGRAM_BOT_TOKEN:
                        try:
                            notifier.risk_alert(msg)
                        except Exception as e:
                            print(f"[Notify] {e}")
            else:
                print(f"[{now.strftime('%H:%M')}] Outside US market hours — sleeping")

            time.sleep(interval * 60)
        except KeyboardInterrupt:
            print("\n[Watch] Stopped by user")
            break
        except Exception as e:
            print(f"[Watch] Error: {e} — continuing in 60s")
            time.sleep(60)


# ─── Legacy UK ISA Scan ──────────────────────────────────────────────

def cmd_scan(args):
    """Legacy UK ISA quant-only scan (the older mode)."""
    import schedule
    from trading_engine import TradingEngine

    engine = TradingEngine()

    print("=" * 60)
    print("  UK ISA Quant Scan — Legacy Mode")
    print("=" * 60)
    print(f"  Universe: {len(config.STOCK_UNIVERSE)} UK stocks")

    def _scan():
        now = datetime.now()
        market_open = datetime.strptime(config.MARKET_OPEN, "%H:%M").time()
        market_close = datetime.strptime(config.MARKET_CLOSE, "%H:%M").time()
        if now.weekday() >= 5 or not (market_open <= now.time() <= market_close):
            print(f"[{now.strftime('%H:%M')}] Outside UK market hours — skipping")
            return
        engine.run_scan()

    if args.schedule:
        print(f"Scheduled: every {args.interval} min")
        schedule.every(args.interval).minutes.do(_scan)
        _scan()
        while True:
            schedule.run_pending()
            time.sleep(30)
    else:
        _scan()


# ─── Argparse Setup ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stock Trading Tool")
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_radar = subs.add_parser("radar", help="One-shot spike radar scan")
    p_radar.set_defaults(func=cmd_radar)

    p_brief = subs.add_parser("brief", help="Generate PM daily brief")
    p_brief.add_argument("--skip-scan", action="store_true",
                         help="Skip the full spike scan (faster)")
    p_brief.add_argument("--no-notify", action="store_true",
                         help="Don't send to Telegram")
    p_brief.set_defaults(func=cmd_brief)

    p_watch = subs.add_parser("watch", help="Continuous spike monitoring")
    p_watch.add_argument("--interval", type=int, default=None,
                         help="Minutes between scans")
    p_watch.set_defaults(func=cmd_watch)

    p_scan = subs.add_parser("scan", help="Legacy UK ISA quant scan")
    p_scan.add_argument("--schedule", action="store_true")
    p_scan.add_argument("--interval", type=int, default=config.SCAN_INTERVAL_MINUTES)
    p_scan.set_defaults(func=cmd_scan)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
