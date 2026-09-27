#!/usr/bin/env python3
"""
Execution-time sizing for Risk tickets (Risk SOP v2.3, PAPER desk).

Ticket qty is a CEILING. At order time the executor recomputes effective risk
per share from the LIVE quote with the ticket's stop price unchanged, and uses

    executable_qty = min(ticket_qty, floor(ticket_effective_risk_budget / live_effective_risk_per_share))

and refuses the leg if the live price is at or below the stop.

Effective risk per share (GBP) — reproduces the Risk ticket figures:

    stop_distance = price - stop                               (line's own currency, major units)
    eff_rps_ccy   = stop_distance x slippage_buffer            (1.6x, SOP v2.3)
                  + price x fx_round_trip                      (0.78% for non-GBP lines, else 0)
                  + price x purchase_tax                       (e.g. UK stamp duty 0.5% on SHEL)
    eff_rps_gbp   = eff_rps_ccy / GBP{ccy}                     (1 for GBP / GBX lines)

    ticket_effective_risk_budget = ticket_qty x eff_rps_gbp(ticket entry, ticket stop, ticket FX)

Check vs the 2026-09-28 tickets at Friday entries, GBPUSD 1.325346:
    TSM  (450.61-429.5215)x1.6 + 450.61x0.78%  = $37.2564 / 1.325346 = £28.1107
    VUSA (110.4325-108.1686)x1.6               = £3.6222
    SHEL (36.11-34.7739)x1.6 + 36.11x0.5%      = £2.3183
    WMT  (107.98-103.704)x1.6 + 107.98x0.78%   = $7.6838 / 1.325346 = £5.7976
    MSFT (516.17-492.6326)x1.6 + 516.17x0.78%  = $41.6860 / 1.325346 = £31.4529

Prices here are MAJOR units (GBP pounds for SHEL, not pence) — the same units as
the ticket. Use `from_vendor_quote()` / `--quote-raw` to pass a raw Yahoo quote
(pence for GBX lines); pence passed as pounds is refused.

CLI (no network unless --live; --live reads public yfinance quotes, never a broker):
    python sizing.py TSM --ticket-qty 1 --stop 429.5215 --entry 450.61 --price 452.10 --gbpusd 1.3253
    python sizing.py SHEL --ticket-qty 18 --stop 34.7739 --entry 36.11 --quote-raw 3598
    python sizing.py WMT --ticket-qty 7 --stop 103.704 --entry 107.98 --live
Exit code 0 = executable qty > 0, 2 = refused / qty 0.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from typing import Optional

import instruments

SLIPPAGE_BUFFER = 1.6          # SOP v2.3 (was 1.25 in v2.2)
FX_ROUND_TRIP = 0.0078         # Freetrade Plus 0.39% per side on non-GBP lines
TICKET_GBPUSD = 1.325346       # FX on the 2026-09-28 Risk tickets (Fri close)
# Budgets recomputed from 4-dp ticket figures carry up to ~0.00005/share rounding;
# this relative tolerance stops a price exactly at the ticket entry flooring to qty-1.
ROUNDING_TOL = 1e-4


class SizingError(ValueError):
    pass


@dataclass
class SizingResult:
    symbol: str
    ok: bool
    qty: int
    ticket_qty: int
    reason: str
    live_price: float
    stop: float
    currency: str
    fx: float
    live_eff_rps_gbp: Optional[float]
    budget_gbp: float
    effective_risk_gbp: float            # qty x live effective risk per share
    notional_gbp: float

    def to_dict(self) -> dict:
        return asdict(self)


def effective_risk_per_share(price: float, stop: float, currency: str = "GBP",
                             fx: Optional[float] = None,
                             slippage: float = SLIPPAGE_BUFFER,
                             fx_round_trip: Optional[float] = None,
                             purchase_tax: float = 0.0) -> float:
    """GBP effective risk per share. `price`/`stop` in the line's major currency;
    `fx` = units of that currency per 1 GBP (GBPUSD for USD). Refuses price <= stop."""
    if price is None or stop is None or price <= 0 or stop <= 0:
        raise SizingError(f"price {price} and stop {stop} must be positive")
    if price <= stop:
        raise SizingError(f"price {price} is at or below the stop {stop}: cancel the leg")
    currency = (currency or "GBP").upper()
    if currency == "GBP":
        rate, fxc = 1.0, 0.0 if fx_round_trip is None else fx_round_trip
    else:
        if not fx or fx <= 0:
            raise SizingError(f"GBP{currency} rate required for a {currency} line")
        rate, fxc = float(fx), FX_ROUND_TRIP if fx_round_trip is None else fx_round_trip
    per_share_ccy = (price - stop) * slippage + price * (fxc + purchase_tax)
    return per_share_ccy / rate


def from_vendor_quote(inst: instruments.Instrument, raw: float) -> float:
    """Raw vendor quote (pence for GBX lines) -> major units."""
    return inst.to_major(raw)


def _inst(symbol_or_inst) -> instruments.Instrument:
    if isinstance(symbol_or_inst, instruments.Instrument):
        return symbol_or_inst
    return instruments.resolve(symbol_or_inst)


def executable_qty(symbol, ticket_qty: int, ticket_stop: float, live_price: float,
                   gbpusd: Optional[float] = None, *,
                   fx: Optional[float] = None,
                   ticket_entry: Optional[float] = None,
                   ticket_eff_rps_gbp: Optional[float] = None,
                   ticket_budget_gbp: Optional[float] = None,
                   ticket_fx: Optional[float] = None,
                   slippage: float = SLIPPAGE_BUFFER,
                   fx_round_trip: Optional[float] = None,
                   purchase_tax: Optional[float] = None) -> SizingResult:
    """Executable qty for one ticket leg at the live quote.

    Budget (first given wins): ticket_budget_gbp (leg effective risk in £) |
    ticket_qty x ticket_eff_rps_gbp | ticket_qty x eff_rps(ticket_entry, ticket_stop,
    ticket_fx or TICKET_GBPUSD). `fx` overrides `gbpusd` for non-USD foreign lines.
    Never raises for a refusal: returns ok=False, qty=0 with the reason."""
    inst = _inst(symbol)
    ccy = inst.currency
    tax = inst.purchase_tax_pct if purchase_tax is None else purchase_tax
    rate = 1.0 if ccy == "GBP" else (fx or (gbpusd if ccy == "USD" else None))
    base = dict(symbol=inst.symbol, ticket_qty=int(ticket_qty), live_price=live_price,
                stop=ticket_stop, currency=ccy, fx=rate or 0.0)
    if ticket_qty is None or int(ticket_qty) != ticket_qty or ticket_qty <= 0:
        return SizingResult(ok=False, qty=0, reason=f"ticket qty {ticket_qty} must be a positive integer",
                            live_eff_rps_gbp=None, budget_gbp=0.0, effective_risk_gbp=0.0,
                            notional_gbp=0.0, **base)
    # pence passed as pounds (GBX lines): e.g. 3598 vs a 34.77 stop
    if inst.quote_unit == "GBX" and live_price and ticket_stop and live_price > ticket_stop * 20:
        return SizingResult(ok=False, qty=0,
                            reason=f"live price {live_price} looks like pence; pass pounds (or use --quote-raw)",
                            live_eff_rps_gbp=None, budget_gbp=0.0, effective_risk_gbp=0.0,
                            notional_gbp=0.0, **base)
    # budget
    try:
        if ticket_budget_gbp is not None:
            budget = float(ticket_budget_gbp)
        elif ticket_eff_rps_gbp is not None:
            budget = ticket_qty * float(ticket_eff_rps_gbp)
        elif ticket_entry is not None:
            tfx = ticket_fx or (TICKET_GBPUSD if ccy == "USD" else rate)
            budget = ticket_qty * effective_risk_per_share(
                ticket_entry, ticket_stop, ccy, tfx, slippage, fx_round_trip, tax)
        else:
            raise SizingError("need ticket_budget_gbp, ticket_eff_rps_gbp or ticket_entry")
        live_rps = effective_risk_per_share(live_price, ticket_stop, ccy, rate, slippage,
                                            fx_round_trip, tax)
    except SizingError as e:
        return SizingResult(ok=False, qty=0, reason=str(e), live_eff_rps_gbp=None,
                            budget_gbp=0.0, effective_risk_gbp=0.0, notional_gbp=0.0, **base)
    risk_qty = math.floor(budget / live_rps * (1 + ROUNDING_TOL))
    qty = max(0, min(int(ticket_qty), risk_qty))
    notional = qty * live_price / (rate or 1.0)
    if qty <= 0:
        reason = (f"live effective risk £{live_rps:.4f}/sh exceeds the ticket budget £{budget:.2f}: "
                  f"0 shares executable (cancel the leg; do not re-base the stop)")
    elif qty < ticket_qty:
        reason = f"risk-capped: {qty} < ticket {ticket_qty} (live £{live_rps:.4f}/sh vs budget £{budget:.2f})"
    else:
        reason = "ticket qty fits the budget"
    return SizingResult(ok=qty > 0, qty=qty, reason=reason, live_eff_rps_gbp=round(live_rps, 6),
                        budget_gbp=round(budget, 4), effective_risk_gbp=round(qty * live_rps, 4),
                        notional_gbp=round(notional, 2), **base)


def _live_inputs(inst: instruments.Instrument) -> tuple[float, Optional[float], str]:
    """Public yfinance quote (NOT a broker) -> (major price, GBPUSD or None, note)."""
    import quotes
    q = quotes.fetch_quote(inst.yf_symbol)
    if q is None:
        raise SizingError(f"no quote for {inst.yf_symbol}")
    note = f"{inst.yf_symbol} {q.price} @ {q.ts.isoformat()} ({q.source}); age {q.age_s():.0f}s"
    fxv = None
    if inst.currency != "GBP":
        fq = quotes.fetch_quote(f"GBP{inst.currency}=X")
        fxv = float(fq.price) if fq else None
        note += f"; GBP{inst.currency} {fxv}"
    return inst.to_major(q.price), fxv, note


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    ap.add_argument("--ticket-qty", type=int, required=True)
    ap.add_argument("--stop", type=float, required=True, help="ticket stop, major units of the line's currency")
    ap.add_argument("--entry", type=float, help="ticket entry (budget = ticket qty x eff risk at entry)")
    ap.add_argument("--ticket-rps", type=float, help="ticket effective risk per share £ (alternative budget)")
    ap.add_argument("--budget", type=float, help="ticket leg effective risk £ (alternative budget)")
    ap.add_argument("--ticket-fx", type=float, default=None, help=f"ticket GBPUSD (default {TICKET_GBPUSD})")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--price", type=float, help="live price, major units (pounds for SHEL)")
    g.add_argument("--quote-raw", type=float, help="live raw vendor quote (pence for GBX lines)")
    g.add_argument("--live", action="store_true", help="fetch public yfinance quote (+ GBPUSD)")
    ap.add_argument("--gbpusd", type=float, help="live GBPUSD for USD lines")
    a = ap.parse_args(argv)
    inst = instruments.resolve(a.symbol)
    note = ""
    gbpusd = a.gbpusd
    if a.live:
        price, fxv, note = _live_inputs(inst)
        gbpusd = gbpusd or fxv
    elif a.quote_raw is not None:
        price = from_vendor_quote(inst, a.quote_raw)
    else:
        price = a.price
    r = executable_qty(inst, a.ticket_qty, a.stop, price, gbpusd, ticket_entry=a.entry,
                       ticket_eff_rps_gbp=a.ticket_rps, ticket_budget_gbp=a.budget,
                       ticket_fx=a.ticket_fx)
    out = r.to_dict()
    if note:
        out["quote_note"] = note
    print(json.dumps(out, indent=2))
    return 0 if r.ok else 2


if __name__ == "__main__":
    sys.exit(main())
