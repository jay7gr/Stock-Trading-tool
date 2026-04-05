"""
Risk Manager — enforces daily stop-loss, position limits, and ISA rules.
This is the safety net that prevents catastrophic losses.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import config


@dataclass
class DailyRiskState:
    date: date
    realised_pnl: float = 0.0
    unrealised_pnl: float = 0.0
    trades_executed: int = 0
    positions_open: int = 0
    is_halted: bool = False
    halt_reason: str = ""


class RiskManager:
    def __init__(self):
        self.daily_state = DailyRiskState(date=date.today())
        self.total_deposited = 0.0  # Track ISA contributions

    def reset_daily(self):
        """Reset daily tracking at start of each trading day."""
        self.daily_state = DailyRiskState(date=date.today())

    def check_new_day(self):
        """Auto-reset if a new day has started."""
        if self.daily_state.date != date.today():
            self.reset_daily()

    def can_trade(self) -> tuple[bool, str]:
        """Check if trading is allowed right now."""
        self.check_new_day()

        if self.daily_state.is_halted:
            return False, f"Trading halted: {self.daily_state.halt_reason}"

        # Check daily stop-loss
        total_daily_pnl = self.daily_state.realised_pnl + self.daily_state.unrealised_pnl
        if total_daily_pnl <= config.DAILY_STOP_LOSS:
            self.daily_state.is_halted = True
            self.daily_state.halt_reason = (
                f"Daily stop-loss hit: £{total_daily_pnl:.2f} "
                f"(limit: £{config.DAILY_STOP_LOSS:.2f})"
            )
            return False, self.daily_state.halt_reason

        # Check max open positions
        if self.daily_state.positions_open >= config.MAX_OPEN_POSITIONS:
            return False, f"Max open positions reached ({config.MAX_OPEN_POSITIONS})"

        return True, "Trading allowed"

    def validate_order(self, ticker: str, size_gbp: float, price: float,
                       portfolio_value: float, current_positions: dict) -> tuple[bool, str]:
        """Validate a specific order before execution."""
        can, reason = self.can_trade()
        if not can:
            return False, reason

        # Minimum position size
        if size_gbp < config.MIN_POSITION_SIZE:
            return False, f"Position too small: £{size_gbp:.2f} (min: £{config.MIN_POSITION_SIZE:.2f})"

        # Maximum position concentration
        max_size = portfolio_value * config.MAX_POSITION_PCT
        existing = current_positions.get(ticker, {}).get("value", 0)
        total_exposure = existing + size_gbp
        if total_exposure > max_size:
            return False, (
                f"Position too large: £{total_exposure:.2f} "
                f"(max {config.MAX_POSITION_PCT*100:.0f}% = £{max_size:.2f})"
            )

        # ISA contribution limit
        if self.total_deposited + size_gbp > config.ISA_ANNUAL_LIMIT:
            remaining = config.ISA_ANNUAL_LIMIT - self.total_deposited
            return False, f"ISA limit: only £{remaining:.2f} remaining of £{config.ISA_ANNUAL_LIMIT:.2f}"

        return True, "Order validated"

    def record_trade(self, pnl: float = 0):
        """Record a completed trade and its P&L."""
        self.check_new_day()
        self.daily_state.trades_executed += 1
        self.daily_state.realised_pnl += pnl

    def update_unrealised(self, unrealised_pnl: float):
        """Update unrealised P&L from open positions."""
        self.check_new_day()
        self.daily_state.unrealised_pnl = unrealised_pnl

        # Check if unrealised losses breach stop-loss
        total = self.daily_state.realised_pnl + unrealised_pnl
        if total <= config.DAILY_STOP_LOSS:
            self.daily_state.is_halted = True
            self.daily_state.halt_reason = (
                f"Daily stop-loss breached by unrealised losses: £{total:.2f}"
            )

    def update_positions_count(self, count: int):
        self.daily_state.positions_open = count

    def daily_pnl(self) -> float:
        return self.daily_state.realised_pnl + self.daily_state.unrealised_pnl

    def daily_target_attainment(self) -> dict:
        """How close are we to the daily target?"""
        pnl = self.daily_pnl()
        return {
            "daily_pnl": pnl,
            "target_min": config.DAILY_PROFIT_TARGET_MIN,
            "target_max": config.DAILY_PROFIT_TARGET_MAX,
            "attainment_pct": max(0, pnl / config.DAILY_PROFIT_TARGET_MIN * 100),
            "stop_loss_remaining": pnl - config.DAILY_STOP_LOSS,
            "is_halted": self.daily_state.is_halted,
            "trades_today": self.daily_state.trades_executed,
        }

    def get_status(self) -> dict:
        return {
            **self.daily_target_attainment(),
            "positions_open": self.daily_state.positions_open,
            "halt_reason": self.daily_state.halt_reason,
        }
