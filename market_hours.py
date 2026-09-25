"""Market-hours coverage (Risk SOP v2.2).

`coverage(ticker)` answers, before a fill: does this ticker's exchange have
session data, is the market open now, and is the 24x5 stop/target monitor
alive (heartbeat) so the position's hours will be covered?

CLI:  python market_hours.py XOM ISPY.L 7203.T
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Optional

import instruments
from instruments import NoSessionDataError, UnknownInstrumentError
from quotes import LONDON, now_london, to_london

HEARTBEAT_MAX_AGE_S = 180
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class MarketNotCoveredError(RuntimeError):
    """Fill refused: no session data, market closed, or monitor not running."""


def monitor_alive(data_dir: Optional[str] = None, at: Optional[datetime] = None) -> tuple[bool, Optional[float]]:
    path = os.path.join(data_dir or DEFAULT_DATA_DIR, "monitor_heartbeat.json")
    try:
        with open(path) as f:
            hb = json.load(f)
        age = (to_london(at or now_london()) - to_london(hb["at"])).total_seconds()
        return (age <= HEARTBEAT_MAX_AGE_S), age
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return False, None


def coverage(ticker: str, at: Optional[datetime] = None, data_dir: Optional[str] = None) -> dict:
    at = to_london(at or now_london())
    out = {"ticker": ticker, "at": at.isoformat(), "covered": False, "market_open": False,
           "fill_allowed": False}
    try:
        inst = instruments.resolve(ticker)
        ex = instruments.exchange_of(inst)
    except (UnknownInstrumentError, NoSessionDataError) as e:
        out["reason"] = str(e)
        return out
    state, window = ex.state(at)
    alive, age = monitor_alive(data_dir, at)
    today = ex.window_on(at.astimezone(ex.zone).date())
    out.update({
        "symbol": inst.symbol, "isin": inst.isin, "exchange": ex.code, "exchange_tz": ex.tz,
        "session_local": [f"{a}-{b}" for a, b in ex.segments] + [f"close_end {ex.close_end}"],
        "today_london": ([today[0].astimezone(LONDON).isoformat(), today[1].astimezone(LONDON).isoformat()]
                         if today else None),
        "market_state": state,
        "market_open": state == "open",
        "next_open_london": ex.next_open(at).astimezone(LONDON).isoformat(),
        "monitor_alive": alive,
        "monitor_heartbeat_age_s": None if age is None else round(age, 1),
    })
    out["covered"] = alive   # session data exists; monitor runs 24x5 over every exchange's hours
    out["fill_allowed"] = alive and state == "open"
    if not alive:
        out["reason"] = "stop/target monitor heartbeat missing or stale (>180s)"
    elif state != "open":
        out["reason"] = f"{ex.code} is {state}; next open {out['next_open_london']}"
    else:
        out["reason"] = "ok"
    return out


def require_fill_coverage(ticker: str, at: Optional[datetime] = None, data_dir: Optional[str] = None) -> dict:
    cov = coverage(ticker, at, data_dir)
    if not cov["fill_allowed"]:
        raise MarketNotCoveredError(f"{ticker}: fill refused — {cov.get('reason')}")
    return cov


if __name__ == "__main__":
    for t in sys.argv[1:] or ["ISPY.L", "XOM"]:
        print(json.dumps(coverage(t), indent=2))
