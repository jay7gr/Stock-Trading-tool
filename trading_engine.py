"""
Trading Engine — the main orchestrator that ties everything together.

Scans the stock universe, runs strategies, gets AI analysis,
builds consensus, checks risk, and executes paper trades.
"""

from datetime import datetime
from typing import Optional

import config
from market_data import scan_universe, compute_technicals, get_stock_data
from strategies import generate_all_signals
from ai_analyst import claude_analyse, grok_analyse
from consensus import make_decision, TradeDecision
from risk_manager import RiskManager
from emulator import PaperTradingEmulator


class TradingEngine:
    def __init__(self):
        self.emulator = PaperTradingEmulator()
        self.risk = RiskManager()
        self.last_scan_results: list[TradeDecision] = []
        self.last_scan_time: Optional[str] = None

    def run_scan(self) -> list[TradeDecision]:
        """
        Full pipeline: scan universe → strategies → AI analysis → consensus → execute.
        Returns list of all trade decisions (including HOLDs).
        """
        print(f"\n{'='*60}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Running market scan...")
        print(f"{'='*60}")

        # Check if trading is allowed
        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            print(f"[RiskManager] {reason}")
            self.last_scan_time = datetime.now().isoformat()
            return []

        # Check stops and targets on existing positions
        closed = self.emulator.check_stops_and_targets()
        for trade in closed:
            print(f"[AutoClose] {trade.ticker}: {trade.reasoning} | P&L: £{trade.pnl:.2f}")
            self.risk.record_trade(trade.pnl)

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

            # 1. Generate quantitative signals
            signals = generate_all_signals(ticker, df, price)

            # 2. AI analysis (Claude + Grok)
            claude_result = claude_analyse(ticker, df, price, signals, info)
            grok_result = grok_analyse(ticker, df, price, signals, info)

            # 3. Consensus decision
            decision = make_decision(
                ticker, price, signals,
                claude_result, grok_result,
                available_capital,
            )
            decisions.append(decision)

            # 4. Execute BUY decisions
            if decision.action == "BUY":
                valid, msg = self.risk.validate_order(
                    ticker, decision.position_size_gbp, price,
                    self.emulator.portfolio_value(),
                    self.emulator.get_positions_dict(),
                )
                if valid:
                    trade = self.emulator.execute_buy(
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
                    if trade:
                        print(f"[BUY] {ticker} | £{trade.value_gbp:.2f} @ {price:.2f}")
                        print(f"       Stop: {decision.stop_loss:.2f} | Target: {decision.take_profit:.2f}")
                        available_capital = self.emulator.cash
                else:
                    print(f"[BLOCKED] {ticker}: {msg}")

            # 5. Execute SELL decisions for existing positions
            elif decision.action == "SELL" and ticker in self.emulator.positions:
                trade = self.emulator.execute_sell(ticker, reason=decision.reasoning[:100])
                if trade:
                    print(f"[SELL] {ticker} | P&L: £{trade.pnl:.2f}")
                    self.risk.record_trade(trade.pnl)

        # Update risk manager
        self.risk.update_unrealised(self.emulator.total_unrealised_pnl())
        self.risk.update_positions_count(len(self.emulator.positions))

        # Sort: BUYs first, then by score
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

    def get_dashboard_data(self) -> dict:
        """Aggregate all data needed for the dashboard."""
        summary = self.emulator.get_summary()
        risk_status = self.risk.get_status()

        return {
            "summary": summary,
            "risk": risk_status,
            "positions": {
                t: {
                    "ticker": p.ticker,
                    "quantity": p.quantity,
                    "avg_entry": p.avg_entry_price,
                    "current_price": p.current_price,
                    "value": p.value_gbp,
                    "pnl": p.unrealised_pnl,
                    "pnl_pct": p.unrealised_pnl_pct,
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
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
