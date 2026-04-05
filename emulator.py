"""
Paper Trading Emulator — simulates trade execution without real money.

Tracks positions, fills, P&L, and trade history as if connected to a real broker.
Uses real market prices from yfinance.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Optional

import config
from market_data import get_current_price


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TRADES_FILE = os.path.join(DATA_DIR, "trades.json")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")


@dataclass
class Trade:
    id: str
    ticker: str
    action: str          # BUY or SELL
    quantity: float
    price: float
    value_gbp: float
    timestamp: str
    reasoning: str
    claude_score: float
    grok_score: float
    combined_score: float
    stop_loss: float
    take_profit: float
    status: str = "open"  # open, closed, stopped_out, take_profit_hit
    close_price: float = 0.0
    close_timestamp: str = ""
    pnl: float = 0.0


@dataclass
class Position:
    ticker: str
    quantity: float
    avg_entry_price: float
    current_price: float
    value_gbp: float
    unrealised_pnl: float
    unrealised_pnl_pct: float
    stop_loss: float
    take_profit: float
    opened_at: str


class PaperTradingEmulator:
    def __init__(self):
        self.cash = config.INITIAL_CAPITAL
        self.positions: dict[str, Position] = {}
        self.trade_history: list[Trade] = []
        self.trade_counter = 0
        self._ensure_data_dir()
        self._load_state()

    def _ensure_data_dir(self):
        os.makedirs(DATA_DIR, exist_ok=True)

    def execute_buy(self, ticker: str, size_gbp: float, price: float,
                    stop_loss: float, take_profit: float,
                    reasoning: str, claude_score: float, grok_score: float,
                    combined_score: float) -> Optional[Trade]:
        """Execute a paper buy order."""
        if size_gbp > self.cash:
            size_gbp = self.cash  # Use available cash
        if size_gbp < config.MIN_POSITION_SIZE:
            return None

        quantity = size_gbp / price
        self.cash -= size_gbp
        self.trade_counter += 1

        trade = Trade(
            id=f"T{self.trade_counter:05d}",
            ticker=ticker, action="BUY",
            quantity=quantity, price=price, value_gbp=size_gbp,
            timestamp=datetime.now().isoformat(),
            reasoning=reasoning,
            claude_score=claude_score, grok_score=grok_score,
            combined_score=combined_score,
            stop_loss=stop_loss, take_profit=take_profit,
        )

        # Update or create position
        if ticker in self.positions:
            pos = self.positions[ticker]
            total_qty = pos.quantity + quantity
            pos.avg_entry_price = (
                (pos.avg_entry_price * pos.quantity + price * quantity) / total_qty
            )
            pos.quantity = total_qty
            pos.stop_loss = stop_loss
            pos.take_profit = take_profit
        else:
            self.positions[ticker] = Position(
                ticker=ticker, quantity=quantity,
                avg_entry_price=price, current_price=price,
                value_gbp=size_gbp, unrealised_pnl=0, unrealised_pnl_pct=0,
                stop_loss=stop_loss, take_profit=take_profit,
                opened_at=datetime.now().isoformat(),
            )

        self.trade_history.append(trade)
        self._save_state()
        return trade

    def execute_sell(self, ticker: str, reason: str = "signal") -> Optional[Trade]:
        """Sell entire position in a ticker."""
        if ticker not in self.positions:
            return None

        pos = self.positions[ticker]
        current_price = get_current_price(ticker) or pos.current_price
        sell_value = pos.quantity * current_price
        pnl = sell_value - (pos.quantity * pos.avg_entry_price)

        self.cash += sell_value
        self.trade_counter += 1

        trade = Trade(
            id=f"T{self.trade_counter:05d}",
            ticker=ticker, action="SELL",
            quantity=pos.quantity, price=current_price, value_gbp=sell_value,
            timestamp=datetime.now().isoformat(),
            reasoning=f"SELL — {reason}",
            claude_score=0, grok_score=0, combined_score=0,
            stop_loss=0, take_profit=0,
            status="closed", close_price=current_price,
            close_timestamp=datetime.now().isoformat(),
            pnl=pnl,
        )

        del self.positions[ticker]
        self.trade_history.append(trade)
        self._save_state()
        return trade

    def check_stops_and_targets(self) -> list[Trade]:
        """Check all positions for stop-loss or take-profit hits."""
        closed_trades = []
        tickers_to_close = []

        for ticker, pos in self.positions.items():
            price = get_current_price(ticker)
            if price is None:
                continue
            pos.current_price = price

            if price <= pos.stop_loss:
                tickers_to_close.append((ticker, "stopped_out",
                    f"Stop-loss hit at {price:.2f} (stop: {pos.stop_loss:.2f})"))
            elif price >= pos.take_profit:
                tickers_to_close.append((ticker, "take_profit_hit",
                    f"Take-profit hit at {price:.2f} (target: {pos.take_profit:.2f})"))

        for ticker, status, reason in tickers_to_close:
            trade = self.execute_sell(ticker, reason)
            if trade:
                trade.status = status
                closed_trades.append(trade)

        self._update_positions()
        self._save_state()
        return closed_trades

    def _update_positions(self):
        """Update current prices and unrealised P&L for all positions."""
        for ticker, pos in self.positions.items():
            price = get_current_price(ticker)
            if price:
                pos.current_price = price
                pos.value_gbp = pos.quantity * price
                cost_basis = pos.quantity * pos.avg_entry_price
                pos.unrealised_pnl = pos.value_gbp - cost_basis
                pos.unrealised_pnl_pct = (
                    (pos.unrealised_pnl / cost_basis * 100) if cost_basis > 0 else 0
                )

    def portfolio_value(self) -> float:
        """Total portfolio value = cash + positions."""
        self._update_positions()
        positions_value = sum(p.value_gbp for p in self.positions.values())
        return self.cash + positions_value

    def total_unrealised_pnl(self) -> float:
        return sum(p.unrealised_pnl for p in self.positions.values())

    def total_realised_pnl(self) -> float:
        return sum(t.pnl for t in self.trade_history if t.action == "SELL")

    def total_pnl(self) -> float:
        return self.total_realised_pnl() + self.total_unrealised_pnl()

    def daily_trades(self, d: Optional[date] = None) -> list[Trade]:
        d = d or date.today()
        return [
            t for t in self.trade_history
            if t.timestamp[:10] == d.isoformat()
        ]

    def daily_pnl(self, d: Optional[date] = None) -> float:
        trades = self.daily_trades(d)
        return sum(t.pnl for t in trades if t.action == "SELL")

    def get_positions_dict(self) -> dict:
        """Return positions as dict for risk manager compatibility."""
        return {
            ticker: {"value": pos.value_gbp, "quantity": pos.quantity}
            for ticker, pos in self.positions.items()
        }

    def get_summary(self) -> dict:
        pv = self.portfolio_value()
        return {
            "cash": self.cash,
            "positions_value": pv - self.cash,
            "portfolio_value": pv,
            "total_pnl": self.total_pnl(),
            "total_pnl_pct": (pv / config.INITIAL_CAPITAL - 1) * 100,
            "realised_pnl": self.total_realised_pnl(),
            "unrealised_pnl": self.total_unrealised_pnl(),
            "open_positions": len(self.positions),
            "total_trades": len(self.trade_history),
        }

    # ─── Persistence ──────────────────────────────────────────────────

    def _save_state(self):
        state = {
            "cash": self.cash,
            "trade_counter": self.trade_counter,
            "positions": {
                t: {
                    "ticker": p.ticker, "quantity": p.quantity,
                    "avg_entry_price": p.avg_entry_price,
                    "current_price": p.current_price,
                    "stop_loss": p.stop_loss, "take_profit": p.take_profit,
                    "opened_at": p.opened_at,
                }
                for t, p in self.positions.items()
            },
        }
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(state, f, indent=2)

        trades = [asdict(t) for t in self.trade_history]
        with open(TRADES_FILE, "w") as f:
            json.dump(trades, f, indent=2, default=str)

    def _load_state(self):
        if os.path.exists(PORTFOLIO_FILE):
            try:
                with open(PORTFOLIO_FILE) as f:
                    state = json.load(f)
                self.cash = state.get("cash", config.INITIAL_CAPITAL)
                self.trade_counter = state.get("trade_counter", 0)
                for t, p in state.get("positions", {}).items():
                    self.positions[t] = Position(
                        ticker=p["ticker"], quantity=p["quantity"],
                        avg_entry_price=p["avg_entry_price"],
                        current_price=p.get("current_price", p["avg_entry_price"]),
                        value_gbp=p["quantity"] * p.get("current_price", p["avg_entry_price"]),
                        unrealised_pnl=0, unrealised_pnl_pct=0,
                        stop_loss=p.get("stop_loss", 0),
                        take_profit=p.get("take_profit", 0),
                        opened_at=p.get("opened_at", ""),
                    )
            except (json.JSONDecodeError, KeyError):
                pass

        if os.path.exists(TRADES_FILE):
            try:
                with open(TRADES_FILE) as f:
                    trades = json.load(f)
                self.trade_history = [Trade(**t) for t in trades]
            except (json.JSONDecodeError, KeyError):
                pass
