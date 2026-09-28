"""Trading-cost model for NET P&L reporting (added 2026-09-28).

    net = gross - costs
    costs = FX fee on non-GBP legs, charged on BOTH buy and sell notional (GBP)
            (config.FREETRADE_FX_FEE_PER_SIDE; Freetrade Plus 0.39%/side)
          + UK stamp duty on BUYS of UK shares only (Instrument.purchase_tax_pct,
            0.5% = config.UK_STAMP_DUTY_PCT; ETFs such as VUSA/ISPY and US shares pay none)

REPORTING ONLY. Ranking, sizing (sizing.py), the -£200 day halt and every risk check stay
on GROSS. Nothing here writes to the book.

Day net P&L (London day, consistent with pnl.day_pnl / SOP v2.3) is "liquidation basis":
  day_net = day_gross - day_costs, where day_costs =
    + entry costs (FX + stamp) of BUYs booked today
    + exit FX of SELLs booked today            (for a position carried overnight only the part
                                                accrued since the day ref: fx x qty x (exit - ref))
    + exit FX accrued on OPEN non-GBP legs at the mark (carried legs: fx x qty x (mark - ref))
The exit accrual makes today's net comparable with a round trip closed at the mark, and the
day-ref split keeps days additive (each pound of cost is counted once over a position's life).
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

import config
import instruments
import pnl

GBP_CCYS = ("GBP",)


def fx_fee_per_side() -> float:
    return float(getattr(config, "FREETRADE_FX_FEE_PER_SIDE", 0.0039))


def _inst(ticker: str):
    return instruments.lookup(ticker) if ticker else None


def currency_of(ticker: str) -> str:
    """Trading currency of the line. Unregistered: '.L' -> GBP (LSE GBX/GBP lines), else USD."""
    inst = _inst(ticker)
    if inst is not None:
        return inst.currency
    return "GBP" if str(ticker).upper().endswith(".L") else "USD"


def stamp_rate(ticker: str) -> float:
    """Purchase tax on BUYS. Registry value wins (SHEL 0.5%; ETFs/US 0). Unregistered lines: 0,
    and `known(ticker)` is False so callers can flag the estimate."""
    inst = _inst(ticker)
    return float(inst.purchase_tax_pct) if inst is not None else 0.0


def known(ticker: str) -> bool:
    return _inst(ticker) is not None


def fx_rate(ticker: str) -> float:
    return 0.0 if currency_of(ticker) in GBP_CCYS else fx_fee_per_side()


def buy_costs(ticker: str, notional_gbp: float) -> dict:
    n = abs(float(notional_gbp or 0.0))
    fx = n * fx_rate(ticker)
    stamp = n * stamp_rate(ticker)
    return {"fx": fx, "stamp": stamp, "total": fx + stamp}


def sell_costs(ticker: str, notional_gbp: float) -> dict:
    n = abs(float(notional_gbp or 0.0))
    fx = n * fx_rate(ticker)
    return {"fx": fx, "stamp": 0.0, "total": fx}


def round_trip_costs(ticker: str, buy_notional_gbp: float, sell_notional_gbp: float) -> float:
    return buy_costs(ticker, buy_notional_gbp)["total"] + sell_costs(ticker, sell_notional_gbp)["total"]


# ─── per trade / per position ────────────────────────────────────────

def _matching_buy(trades: list, sell) -> Optional[object]:
    """Latest BUY of the same ticker at or before the SELL (trade list order = booking order)."""
    buy = None
    for t in trades:
        if t is sell:
            break
        if t.ticker == sell.ticker and t.action == "BUY":
            buy = t
    return buy


def trade_net_pnl(trades: list, sell) -> Optional[float]:
    """Net round-trip P&L of a SELL row: pnl - (entry costs of its BUY + exit costs)."""
    if sell.action != "SELL":
        return None
    buy = _matching_buy(trades, sell)
    buy_notional = float(buy.value_gbp) if buy is not None else float(sell.quantity) * (
        float(sell.price) - float(sell.pnl) / float(sell.quantity) if sell.quantity else 0.0)
    return float(sell.pnl) - round_trip_costs(sell.ticker, buy_notional, float(sell.value_gbp))


def position_costs(pos) -> dict:
    """Entry costs paid + exit FX accrued at the current mark for one open position (GBP)."""
    entry = buy_costs(pos.ticker, float(pos.quantity) * float(pos.avg_entry_price))
    exit_ = sell_costs(pos.ticker, float(pos.quantity) * float(pos.current_price))
    return {"entry": entry["total"], "exit_accrued": exit_["total"], "total": entry["total"] + exit_["total"]}


def position_net_unrealised(pos) -> float:
    gross = float(pos.quantity) * (float(pos.current_price) - float(pos.avg_entry_price))
    return gross - position_costs(pos)["total"]


# ─── day ─────────────────────────────────────────────────────────────

def day_costs(trades: Iterable, positions: Iterable, day: date) -> dict:
    """Costs attributable to the London day (see module docstring). GBP, all >= 0."""
    trades = list(trades)
    fx_buy = stamp = fx_sell = fx_open = 0.0
    legs = []
    for t in trades:
        if pnl.trade_day(t.timestamp) != day:
            continue
        if t.action == "BUY":
            c = buy_costs(t.ticker, t.value_gbp)
            fx_buy += c["fx"]
            stamp += c["stamp"]
            legs.append({"trade_id": t.id, "ticker": t.ticker, "kind": "buy", "fx": round(c["fx"], 4),
                         "stamp": round(c["stamp"], 4)})
        elif t.action == "SELL":
            buy = _matching_buy(trades, t)
            carried = buy is not None and pnl.trade_day(buy.timestamp) != day
            notional = float(t.value_gbp)
            if carried:
                dp = getattr(t, "day_pnl", None)
                q = float(t.quantity) or 1.0
                ref = float(t.price) - (float(dp) / q if dp not in (None, "") else float(t.pnl) / q)
                notional = float(t.quantity) * (float(t.price) - ref)   # exit FX accrued since the day ref
            f = notional * fx_rate(t.ticker)
            fx_sell += f
            legs.append({"trade_id": t.id, "ticker": t.ticker, "kind": "sell", "fx": round(f, 4), "stamp": 0.0})
    for p in positions:
        if pnl.opened_after(p, day):
            continue
        ref, _src = pnl.day_ref(p, day)
        opened = pnl.trade_day(getattr(p, "opened_at", None) or None)
        base = float(p.current_price) if (opened is None or opened >= day) else float(p.current_price) - ref
        f = float(p.quantity) * base * fx_rate(p.ticker)
        fx_open += f
        if f:
            legs.append({"ticker": p.ticker, "kind": "open_exit_accrual", "fx": round(f, 4), "stamp": 0.0})
    total = fx_buy + stamp + fx_sell + fx_open
    return {"fx_buy": fx_buy, "stamp": stamp, "fx_sell": fx_sell, "fx_open_exit": fx_open,
            "fx_total": fx_buy + fx_sell + fx_open, "total": total, "legs": legs}


def day_net_pnl(trades: Iterable, positions: Iterable, day: date, gross: Optional[float] = None) -> dict:
    trades, positions = list(trades), list(positions)
    g = pnl.day_pnl(trades, positions, day) if gross is None else float(gross)
    c = day_costs(trades, positions, day)
    return {"gross": g, "costs": c["total"], "net": g - c["total"], "breakdown": c}


def total_costs_to_date(trades: Iterable, positions: Iterable) -> float:
    """All costs since inception: every BUY's entry costs + every SELL's exit FX + exit FX
    accrued on open legs at the mark (for net total return next to gross total return)."""
    trades = list(trades)
    c = 0.0
    for t in trades:
        if t.action == "BUY":
            c += buy_costs(t.ticker, t.value_gbp)["total"]
        elif t.action == "SELL":
            c += sell_costs(t.ticker, t.value_gbp)["total"]
    for p in positions:
        c += sell_costs(p.ticker, float(p.quantity) * float(p.current_price))["total"]
    return c


def cost_model() -> dict:
    """Parameters, for live_status / reports."""
    return {"plan": getattr(config, "FREETRADE_PLAN", "Plus"),
            "fx_fee_per_side": fx_fee_per_side(),
            "uk_stamp_duty_on_uk_share_buys": float(getattr(config, "UK_STAMP_DUTY_PCT", 0.005)),
            "basis": "net = gross - (FX on non-GBP buy+sell notional + UK stamp on UK share buys); "
                     "day net includes exit FX accrued on open non-GBP legs at the mark; "
                     "ranking/sizing/halt stay on gross"}
