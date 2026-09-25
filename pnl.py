"""Day P&L (GBP, London calendar day) and GBP formatting — shared by the
dashboard and the monitor so both show the same number.

day P&L = realised P&L of SELLs booked on the London day
        + mark-to-market of all open positions vs entry (all markets, GBP).
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Iterable, Optional

import config
from quotes import to_london


def fmt_gbp(x: float, signed: bool = False, dp: int = 2) -> str:
    """-£187.06 / £1,234.00 / +£12.00 (signed=True). Never '£-187.06'."""
    if x is None:
        return "-"
    x = round(float(x), dp)
    if x == 0:
        x = 0.0  # avoid '-£0.00'
    sign = "-" if x < 0 else ("+" if signed and x > 0 else "")
    return f"{sign}£{abs(x):,.{dp}f}"


def trade_day(ts) -> Optional[date]:
    try:
        return to_london(ts).date()
    except Exception:  # noqa: BLE001
        return None


def realised_on(trades: Iterable, day: date) -> float:
    return sum(float(t.pnl) for t in trades
               if t.action == "SELL" and trade_day(t.timestamp) == day)


def open_mtm(positions: Iterable) -> float:
    return sum(float(p.quantity) * (float(p.current_price) - float(p.avg_entry_price))
               for p in positions)


def day_pnl(trades: Iterable, positions: Iterable, day: date) -> float:
    return realised_on(list(trades), day) + open_mtm(list(positions))


def read_live_status(data_dir: str) -> dict:
    try:
        with open(os.path.join(data_dir, "live_status.json")) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def halted_on(ls: dict, day: date) -> bool:
    if not ls.get("halted"):
        return False
    hd = ls.get("halted_date") or str(ls.get("as_of", ""))[:10]
    return hd == day.isoformat()


def day_risk(pnl: float, target: float = None, stop: float = None) -> dict:
    target = config.DAILY_PROFIT_TARGET_MIN if target is None else target
    stop = config.DAILY_STOP_LOSS if stop is None else stop
    return {
        "day_pnl": pnl,
        "target": target,
        "stop": stop,
        "to_target": target - pnl,
        "headroom_to_halt": max(0.0, pnl - stop),
        "target_progress": max(0.0, min(1.0, pnl / target)) if target else 0.0,
        "halt_used": max(0.0, min(1.0, -pnl / abs(stop))) if stop else 0.0,
        "breached": pnl <= stop,
    }
