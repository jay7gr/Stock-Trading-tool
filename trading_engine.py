"""
Trading Engine — orchestrates the full pipeline.

Scan → Strategies → AI Analysis → Consensus → Risk → Execute → Notify
"""

from datetime import datetime
from typing import Optional

import config
from market_data import scan_universe, get_current_price
from strategies import generate_all_signals
from ai_analyst import claude_analyse, grok_analyse
from consensus import make_decision, TradeDecision
from risk_manager import RiskManager
from emulator import PaperTradingEmulator
from broker_t212 import Trading212Client
from notifications import Notifier


class TradingEngine:
    def __init__(self):
        self.emulator = PaperTradingEmulator()
        self.broker = Trading212Client() if config.T212_API_KEY else None
        self.risk = RiskManager()
        self.notifier = Notifier()
        self.last_scan_results: list[TradeDecision] = []
        self.last_scan_time: Optional[str] = None

        # Report which mode we're running
        mode_parts = []
        if config.EMULATOR_MODE:
            mode_parts.append("Paper Trading")
        else:
            mode_parts.append("LIVE Trading (T212)")
        if config.CLAUDE_API_KEY:
            mode_parts.append("Claude AI")
        else:
            mode_parts.append("Local Conservative Engine")
        if config.GROK_API_KEY:
            mode_parts.append("Grok AI")
        else:
            mode_parts.append("Local Aggressive Engine")
        if config.TWELVE_DATA_API_KEY:
            mode_parts.append("Twelve Data")
        else:
            mode_parts.append("yfinance")

        self.mode_description = " | ".join(mode_parts)
        print(f"[Engine] Mode: {self.mode_description}")

    def run_scan(self) -> list[TradeDecision]:
        """Full pipeline: scan → strategies → AI → consensus → execute."""
        print(f"\n{'='*60}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Running market scan...")
        print(f"{'='*60}")

        # Check if trading is allowed
        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            print(f"[RiskManager] {reason}")
            self.notifier.risk_alert(reason)
            self.last_scan_time = datetime.now().isoformat()
            return []

        # Check stops on existing positions
        closed = self.emulator.check_stops_and_targets()
        for trade in closed:
            print(f"[AutoClose] {trade.ticker}: {trade.reasoning} | P&L: £{trade.pnl:.2f}")
            self.risk.record_trade(trade.pnl)
            self.notifier.stop_hit(trade.ticker, trade.pnl, trade.reasoning)

        # Scan universe
        print(f"Scanning {len(config.STOCK_UNIVERSE)} stocks...")
        universe_data = scan_universe(config.STOCK_UNIVERSE)
        print(f"Got data for {len(universe_data)} stocks")

        decisions = []
        available_capital = self.emulator.cash

        for ticker, data in universe_data.items():
            price = data["price"]
            df = data["df"]
            info = data["info"]

            # 1. Quantitative signals
            signals = generate_all_signals(ticker, df, price)

            # 2. AI analysis (real API or local fallback)
            claude_result = claude_analyse(ticker, df, price, signals, info)
            grok_result = grok_analyse(ticker, df, price, signals, info)

            # 3. Consensus
            decision = make_decision(
                ticker, price, signals,
                claude_result, grok_result,
                available_capital,
            )
            decisions.append(decision)

            # 4. Execute BUY
            if decision.action == "BUY":
                valid, msg = self.risk.validate_order(
                    ticker, decision.position_size_gbp, price,
                    self.emulator.portfolio_value(),
                    self.emulator.get_positions_dict(),
                )
                if valid:
                    trade = self._execute_buy(
                        ticker, decision, claude_result, grok_result, price,
                    )
                    if trade:
                        available_capital = self.emulator.cash
                        self.notifier.trade_alert(
                            ticker, "BUY", price,
                            decision.position_size_gbp,
                            decision.combined_score,
                            decision.reasoning[:300],
                            decision.stop_loss,
                            decision.take_profit,
                        )
                else:
                    print(f"[BLOCKED] {ticker}: {msg}")

            # 5. Execute SELL for existing positions
            elif decision.action == "SELL" and ticker in self.emulator.positions:
                trade = self._execute_sell(ticker, decision.reasoning[:100])
                if trade:
                    self.risk.record_trade(trade.pnl)
                    self.notifier.trade_alert(
                        ticker, "SELL", trade.price,
                        trade.value_gbp, decision.combined_score,
                        f"P&L: £{trade.pnl:+.2f}\n{decision.reasoning[:200]}",
                        0, 0,
                    )

        # Update risk manager
        self.risk.update_unrealised(self.emulator.total_unrealised_pnl())
        self.risk.update_positions_count(len(self.emulator.positions))

        # Sort
        decisions.sort(key=lambda d: (-1 if d.action == "BUY" else 1, -d.combined_score))
        self.last_scan_results = decisions
        self.last_scan_time = datetime.now().isoformat()

        # Summary
        buys = [d for d in decisions if d.action == "BUY"]
        sells = [d for d in decisions if d.action == "SELL"]
        holds = [d for d in decisions if d.action == "HOLD"]
        print(f"\nScan complete: {len(buys)} BUY, {len(sells)} SELL, {len(holds)} HOLD")
        print(f"Portfolio: £{self.emulator.portfolio_value():.2f} | "
              f"Cash: £{self.emulator.cash:.2f} | "
              f"Positions: {len(self.emulator.positions)}")

        return decisions

    def _execute_buy(self, ticker, decision, claude_result, grok_result, price):
        """Execute a buy — paper or live."""
        if config.EMULATOR_MODE or not self.broker:
            return self.emulator.execute_buy(
                ticker=ticker,
                size_gbp=decision.position_size_gbp,
                price=price,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                reasoning=decision.reasoning,
                claude_score=claude_result.score,
                grok_score=grok_result.score,
                combined_score=decision.combined_score,
            )
        else:
            # Live execution via Trading 212
            result = self.broker.place_value_order(ticker, decision.position_size_gbp)
            if result.success:
                print(f"[LIVE BUY] {ticker} | £{result.value_gbp:.2f} @ {result.price:.2f}")
                # Also record in emulator for tracking
                return self.emulator.execute_buy(
                    ticker=ticker, size_gbp=result.value_gbp,
                    price=result.price, stop_loss=decision.stop_loss,
                    take_profit=decision.take_profit,
                    reasoning=decision.reasoning,
                    claude_score=claude_result.score,
                    grok_score=grok_result.score,
                    combined_score=decision.combined_score,
                )
            else:
                print(f"[LIVE BUY FAILED] {ticker}: {result.message}")
                return None

    def _execute_sell(self, ticker, reason):
        """Execute a sell — paper or live."""
        if config.EMULATOR_MODE or not self.broker:
            return self.emulator.execute_sell(ticker, reason)
        else:
            result = self.broker.sell_position(ticker)
            if result.success:
                print(f"[LIVE SELL] {ticker} | £{result.value_gbp:.2f}")
                return self.emulator.execute_sell(ticker, reason)
            else:
                print(f"[LIVE SELL FAILED] {ticker}: {result.message}")
                return None

    def send_daily_summary(self):
        """Send end-of-day summary notification."""
        summary = self.emulator.get_summary()
        risk = self.risk.get_status()
        self.notifier.daily_summary(
            summary["portfolio_value"],
            risk["daily_pnl"],
            summary["total_pnl"],
            risk["trades_today"],
            risk["attainment_pct"],
        )

    def get_dashboard_data(self) -> dict:
        summary = self.emulator.get_summary()
        risk_status = self.risk.get_status()
        return {
            "mode": self.mode_description,
            "summary": summary,
            "risk": risk_status,
            "positions": {
                t: {
                    "ticker": p.ticker, "quantity": p.quantity,
                    "avg_entry": p.avg_entry_price,
                    "current_price": p.current_price,
                    "value": p.value_gbp, "pnl": p.unrealised_pnl,
                    "pnl_pct": p.unrealised_pnl_pct,
                    "stop_loss": p.stop_loss, "take_profit": p.take_profit,
                }
                for t, p in self.emulator.positions.items()
            },
            "recent_trades": [
                {
                    "id": t.id, "ticker": t.ticker, "action": t.action,
                    "price": t.price, "value": t.value_gbp,
                    "timestamp": t.timestamp, "reasoning": t.reasoning,
                    "claude_score": t.claude_score, "grok_score": t.grok_score,
                    "combined_score": t.combined_score, "pnl": t.pnl,
                    "status": t.status,
                }
                for t in reversed(self.trade_history_recent(20))
            ],
            "decisions": [
                {
                    "ticker": d.ticker, "action": d.action,
                    "score": d.combined_score,
                    "claude_score": d.claude_analysis.score,
                    "grok_score": d.grok_analysis.score,
                    "claude_model": d.claude_analysis.model,
                    "grok_model": d.grok_analysis.model,
                    "entry_price": d.entry_price,
                    "stop_loss": d.stop_loss,
                    "take_profit": d.take_profit,
                    "position_size": d.position_size_gbp,
                    "reasoning": d.reasoning,
                }
                for d in self.last_scan_results
            ],
            "last_scan": self.last_scan_time,
        }

    def trade_history_recent(self, n: int = 20) -> list:
        return self.emulator.trade_history[-n:]
