"""Day P&L (GBP, London calendar day) and GBP formatting — shared by the
dashboard and the monitor so both show the same number.

Risk SOP v2.3 (multi-day holds):
day P&L = realised today + change in open mark-to-market since the PREVIOUS CLOSE
  * each position's day reference ("day ref") is
      - its entry price if it was opened on this London day, else
      - its market's previous close in GBP (Position.prev_close, stamped by the
        monitor at the first pass of the London day from the last mark taken
        before midnight = the last 1m close of that market's previous session,
        converted to GBP at the FX used for that mark);
      - if a carried position has no prev_close for today, entry is used and
        the source is reported as "entry_fallback_no_prev_close".
  * open part   = sum qty x (mark - day ref)                 (all markets, GBP)
  * realised part = SELLs booked on the London day, measured from the same day
    ref (Trade.day_pnl, set at sell time); older rows without day_pnl use pnl.
Positions opened today therefore still measure from entry, exactly as before.
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


def _trade_day_pnl(t) -> float:
    v = getattr(t, "day_pnl", None)
    return float(t.pnl) if v is None or v == "" else float(v)


def realised_on(trades: Iterable, day: date) -> float:
    """Realised P&L of SELLs booked on the London day, measured from each
    position's day reference (previous close for carried positions)."""
    return sum(_trade_day_pnl(t) for t in trades
               if t.action == "SELL" and trade_day(t.timestamp) == day)


def open_mtm(positions: Iterable) -> float:
    """Open mark-to-market vs ENTRY (since inception; not the day number)."""
    return sum(float(p.quantity) * (float(p.current_price) - float(p.avg_entry_price))
               for p in positions)


def day_ref(pos, day: date) -> tuple[float, str]:
    """(GBP reference price for today's P&L, source) for one open position."""
    entry = float(pos.avg_entry_price)
    opened = trade_day(getattr(pos, "opened_at", None) or None)
    if opened is None or opened >= day:
        return entry, "entry"
    pc = getattr(pos, "prev_close", None)
    if pc and float(pc) > 0 and getattr(pos, "prev_close_date", "") == day.isoformat():
        return float(pc), getattr(pos, "prev_close_source", "") or "prev_close"
    return entry, "entry_fallback_no_prev_close"


def open_day_change(positions: Iterable, day: date) -> float:
    """Change in open mark-to-market since the previous close (entry if opened today)."""
    return sum(float(p.quantity) * (float(p.current_price) - day_ref(p, day)[0])
               for p in positions)


def day_pnl(trades: Iterable, positions: Iterable, day: date) -> float:
    return realised_on(list(trades), day) + open_day_change(list(positions), day)


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
