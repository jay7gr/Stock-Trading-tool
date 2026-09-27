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

PACKAGE-LEVEL CHECK (Risk execution ruling 27 Sep; replaces per-leg budgets):
    headroom = day_limit (£200) - realised LOSS today - full effective risk of every OPEN leg
    open leg risk = qty x eff_rps(day ref -> its stop)   (day ref = entry if opened today, else
                    previous close) — full risk to stop even if its price has moved, so a
                    not-yet-visible LSE stop-out can never free headroom
    filling legs start at ticket qty (legs at/below stop are skipped); while the combined live
    effective risk of the filling legs > headroom, remove ONE share from the lowest-ranked leg
    that still has shares (2026-09-28 rank: TSM 1, VUSA 2, SHEL 3, WMT 4, MSFT 5 -> skip MSFT,
    then trim WMT share by share, then SHEL, VUSA, TSM). Never above ticket qty; freed risk
    is not reused.
    python sizing.py package --gbpusd 1.3253 --from-book \
        --leg TSM:1:429.5215 --leg WMT:7:103.704 --leg MSFT:1:492.6326 \
        --price TSM=451.20 --price WMT=108.05 --price MSFT=516.90        # or --live
    add --book (or --book --dry-run) to also book the fills via the emulator on fresh (<=60s)
    quotes — for real-time lines (US). LSE legs use lse_leg.py (delayed 1m bars).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, asdict
from typing import Optional

import instruments

# 2026-09-28 package rank (Risk tickets; 1 = highest). Cuts go from the bottom up.
MONDAY_RANK = ("TSM", "VUSA", "SHEL", "WMT", "MSFT")
DAY_LIMIT_GBP = 200.0

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


@dataclass
class PackageLeg:
    symbol: str
    ticket_qty: int
    stop: float                    # ticket stop, major units of the line's currency
    live_price: Optional[float]    # major units
    rank: int                      # 1 = highest priority (cut last)
    fx: Optional[float] = None     # GBP{ccy}; None for GBP lines
    target: Optional[float] = None


@dataclass
class OpenLeg:
    symbol: str
    qty: float
    stop: float                    # major units of the line's currency
    ref_price: float               # day ref (entry if opened today, else previous close), major units
    fx: Optional[float] = None


def open_leg_risk_gbp(leg: OpenLeg) -> tuple[float, str]:
    """Full effective risk of an open leg from its day ref to its stop (GBP)."""
    inst = _inst(leg.symbol)
    if leg.ref_price <= leg.stop:
        # at/through its stop: it exits at/after the open; count the cost part only, flag it
        rate = 1.0 if inst.currency == "GBP" else (leg.fx or 0)
        cost = leg.ref_price * ((FX_ROUND_TRIP if inst.currency != "GBP" else 0) + inst.purchase_tax_pct)
        return (leg.qty * cost / rate if rate else float("inf")), "ref at/below stop (exit due)"
    r = effective_risk_per_share(leg.ref_price, leg.stop, inst.currency, leg.fx,
                                 purchase_tax=inst.purchase_tax_pct)
    return leg.qty * r, "ok"


def package_qty(legs: list, open_legs: list = (), realised_pnl_today: float = 0.0,
                day_limit: float = DAY_LIMIT_GBP) -> dict:
    """Package-level sizing (see module doc). Returns per-leg qty + an audit trail."""
    realised_loss = max(0.0, -float(realised_pnl_today or 0.0))
    open_rows, open_risk = [], 0.0
    for ol in open_legs:
        risk, note = open_leg_risk_gbp(ol)
        open_risk += risk
        open_rows.append({"symbol": ol.symbol, "qty": ol.qty, "ref_price": ol.ref_price,
                          "stop": ol.stop, "risk_gbp": round(risk, 4), "note": note})
    headroom = day_limit - realised_loss - open_risk
    rows = {}
    for lg in legs:
        inst = _inst(lg.symbol)
        row = {"symbol": inst.symbol, "rank": lg.rank, "ticket_qty": int(lg.ticket_qty), "qty": 0,
               "stop": lg.stop, "live_price": lg.live_price, "currency": inst.currency,
               "fx": lg.fx, "eff_rps_gbp": None, "status": "", "target": lg.target}
        try:
            if inst.quote_unit == "GBX" and lg.live_price and lg.live_price > lg.stop * 20:
                raise SizingError(f"live price {lg.live_price} looks like pence; pass pounds")
            rps_ = effective_risk_per_share(lg.live_price, lg.stop, inst.currency, lg.fx,
                                            purchase_tax=inst.purchase_tax_pct)
            row.update(eff_rps_gbp=round(rps_, 6), qty=int(lg.ticket_qty), status="fill")
            row["_rps"] = rps_
        except (SizingError, TypeError) as e:
            row.update(status=f"skip: {e}")
            row["_rps"] = 0.0
        rows[inst.symbol] = row
    cuts = []
    total = lambda: sum(r["qty"] * r["_rps"] for r in rows.values())
    order = sorted(rows.values(), key=lambda r: -r["rank"])       # lowest rank first
    for r in order:
        while total() > headroom + 1e-9 and r["qty"] > 0:
            r["qty"] -= 1
            cuts.append(f"-1 {r['symbol']} (rank {r['rank']})")
        if total() <= headroom + 1e-9:
            break
    for r in rows.values():
        if r["status"] == "fill" and r["qty"] < r["ticket_qty"]:
            r["status"] = "skipped: no headroom" if r["qty"] == 0 else f"trimmed to {r['qty']}"
        r["risk_gbp"] = round(r["qty"] * r["_rps"], 4)
        r.pop("_rps")
    new_risk = sum(r["risk_gbp"] for r in rows.values())
    return {
        "day_limit": day_limit, "realised_loss_today": round(realised_loss, 2),
        "open_legs": open_rows, "open_risk_gbp": round(open_risk, 4),
        "headroom_gbp": round(headroom, 4), "new_risk_gbp": round(new_risk, 4),
        "combined_risk_gbp": round(open_risk + new_risk + realised_loss, 4),
        "legs": sorted(rows.values(), key=lambda r: r["rank"]), "cuts": cuts,
        "ok": any(r["qty"] > 0 for r in rows.values()),
    }


def rank_of(symbol: str, ranks=MONDAY_RANK) -> int:
    sym = _inst(symbol).symbol
    return (list(ranks).index(sym) + 1) if sym in ranks else len(ranks) + 1


def book_state(data_dir: Optional[str] = None, now=None, fx_fn=None) -> tuple[list, float]:
    """(open legs, realised P&L today) from the paper book — READ ONLY."""
    import emulator as emu_mod
    import pnl
    from quotes import now_london, to_london
    now = to_london(now or now_london())
    emu = emu_mod.PaperTradingEmulator(data_dir=data_dir, quote_fn=lambda s: None,
                                       now_fn=lambda: now, write_alerts=False)
    legs = []
    for tkr, p in emu.positions.items():
        inst = instruments.lookup(tkr)
        if inst is None:
            continue
        ref_gbp, _ = pnl.day_ref(p, now.date())
        if inst.currency == "GBP":
            fx, ref, stop = None, ref_gbp, p.stop_loss
        else:
            fx = p.fx_ref or (fx_fn(inst.currency) if fx_fn else None)
            if not fx:
                raise SizingError(f"no GBP{inst.currency} rate for open leg {tkr}")
            ref = ref_gbp * fx
            stop = p.stop_loss if emu.native_levels(p) else p.stop_loss * fx
        legs.append(OpenLeg(inst.symbol, p.quantity, stop, ref, fx))
    return legs, pnl.realised_on(emu.trade_history, now.date())


STATE_FILES = ("portfolio.json", "trades.json", "live_status.json", "monitor_heartbeat.json")


def dry_copy(real_dir: str, simulated_now=None) -> str:
    """Temp copy of the book for --dry-run (nothing is ever written to real_dir)."""
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="pm_dry_")
    for f in STATE_FILES:
        src = os.path.join(real_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp, f))
    if simulated_now is not None:     # heartbeat as the monitor would have written it then
        with open(os.path.join(tmp, "monitor_heartbeat.json"), "w") as fh:
            json.dump({"at": simulated_now.isoformat(), "pid": 0, "simulated": True}, fh)
    return tmp


def parse_leg(spec: str) -> dict:
    """SYM:TICKET_QTY:STOP[:TARGET]"""
    parts = spec.split(":")
    if len(parts) not in (3, 4):
        raise SizingError(f"leg spec {spec!r} must be SYM:QTY:STOP[:TARGET]")
    return {"symbol": parts[0], "ticket_qty": int(parts[1]), "stop": float(parts[2]),
            "target": float(parts[3]) if len(parts) == 4 else None}


def book_package(leg_specs: list, *, data_dir: Optional[str] = None, dry_run: bool = False,
                 now_fn=None, quote_fn=None, gbpusd: Optional[float] = None,
                 ranks=MONDAY_RANK, day_limit: float = DAY_LIMIT_GBP,
                 simulated_clock: bool = False) -> dict:
    """Size a package of REAL-TIME legs (e.g. the US legs) at package level and book
    the fills via the emulator on fresh (<=60s) quotes. Delayed-feed legs (LSE) are
    refused here: use lse_leg.py. dry_run books into a temp copy (data/ untouched)."""
    import shutil
    import time as _time
    import emulator as emu_mod
    import quotes
    from quotes import now_london, to_london
    now_fn = now_fn or now_london
    now = to_london(now_fn())
    quote_fn = quote_fn or quotes.fetch_quote
    real_dir = data_dir or emu_mod.DATA_DIR
    out = {"wall_clock": now.isoformat(), "dry_run": dry_run, "simulated_clock": simulated_clock,
           "ok": False, "fills": []}
    legs, qs, fx_used = [], {}, {}
    try:
        for sp in leg_specs:
            inst = _inst(sp["symbol"])
            if instruments.feed_delay(inst).total_seconds() > 0:
                raise SizingError(f"{inst.symbol} is on a delayed feed ({inst.exchange}); use lse_leg.py")
            q = quotes.get_fresh_quote(inst.yf_symbol, at=now, fetch=quote_fn, requote_wait_s=0)
            qs[inst.symbol] = q
            fx = None
            if inst.currency != "GBP":
                if inst.currency not in fx_used:
                    if gbpusd and inst.currency == "USD":
                        fx_used["USD"] = float(gbpusd)
                    else:
                        fq = quotes.get_fresh_quote(f"GBP{inst.currency}=X", at=now, fetch=quote_fn,
                                                    max_age_s=emu_mod.FX_MAX_AGE_S, requote_wait_s=0)
                        fx_used[inst.currency] = float(fq.price)
                fx = fx_used[inst.currency]
            legs.append(PackageLeg(inst.symbol, sp["ticket_qty"], sp["stop"], inst.to_major(q.price),
                                   rank_of(inst.symbol, ranks), fx, sp.get("target")))
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"{type(e).__name__}: {e}"
        return out
    run_dir = dry_copy(real_dir, now if simulated_clock else None) if dry_run else real_dir
    try:
        open_legs, realised = book_state(run_dir if (dry_run or data_dir) else None, now,
                                         fx_fn=lambda c: fx_used.get(c))
        pkg = package_qty(legs, open_legs, realised, day_limit)
        out["package"] = pkg
        emu = emu_mod.PaperTradingEmulator(data_dir=run_dir if (dry_run or data_dir) else None,
                                           quote_fn=quote_fn, now_fn=lambda: now)
        emu.requote_wait_s = 0
        for c, r in fx_used.items():                       # book at the same FX used for sizing
            emu._fx_cache[c] = (r, _time.monotonic())
        for row in pkg["legs"]:
            if row["qty"] <= 0:
                continue
            inst = _inst(row["symbol"])
            try:
                tr = emu.execute_buy(
                    inst.yf_symbol, 0, None, row["stop"], row["target"] or 0,
                    f"package fill: rank {row['rank']}, ticket {row['ticket_qty']} -> {row['qty']} "
                    f"(combined live eff risk £{pkg['combined_risk_gbp']:.2f} <= £{day_limit:.0f})",
                    0, 0, 0, ticket_symbol=inst.symbol, quote=qs[inst.symbol],
                    levels_ccy=inst.currency, qty=row["qty"])
                out["fills"].append({"symbol": inst.symbol, "ok": tr is not None,
                                     **({k: getattr(tr, k) for k in ("id", "quantity", "price", "value_gbp",
                                                                     "timestamp", "quote_ts", "quote_source")}
                                        if tr else {"reason": "not booked (halted/cash); see alerts"})})
            except Exception as e:  # noqa: BLE001
                out["fills"].append({"symbol": inst.symbol, "ok": False, "reason": f"{type(e).__name__}: {e}"})
        out["ok"] = any(f["ok"] for f in out["fills"])
        out["cash_after"] = round(emu.cash, 2)
        out["reason"] = ("dry run: nothing written to data/" if dry_run else f"booked into {run_dir}")
        return out
    finally:
        if dry_run:
            shutil.rmtree(run_dir, ignore_errors=True)


def package_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="sizing.py package", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leg", action="append", required=True, help="SYM:TICKET_QTY:STOP[:TARGET]")
    ap.add_argument("--price", action="append", default=[], help="SYM=live price, major units (sizing only)")
    ap.add_argument("--live", action="store_true", help="sizing only: fetch public yfinance quotes")
    ap.add_argument("--gbpusd", type=float)
    ap.add_argument("--realised", type=float, default=None, help="realised P&L today £ (loss negative)")
    ap.add_argument("--open", action="append", default=[], help="open leg SYM:QTY:STOP:REF_PRICE (major units)")
    ap.add_argument("--from-book", action="store_true", help="open legs + realised today from data/ (read-only)")
    ap.add_argument("--rank", default=",".join(MONDAY_RANK), help="comma list, highest first")
    ap.add_argument("--day-limit", type=float, default=DAY_LIMIT_GBP)
    ap.add_argument("--book", action="store_true", help="book the fills on fresh <=60s quotes (real-time legs)")
    ap.add_argument("--dry-run", action="store_true", help="with --book: temp copy, nothing written")
    ap.add_argument("--now", default=None, help="simulated wall clock (ISO); --book --dry-run only")
    ap.add_argument("--data-dir", default=None)
    a = ap.parse_args(argv)
    ranks = tuple(x.strip().upper() for x in a.rank.split(","))
    specs = [parse_leg(x) for x in a.leg]
    if a.book:
        if a.price or a.live:
            ap.error("--book takes quotes from the live feed itself; drop --price/--live")
        if a.now and not a.dry_run:
            ap.error("--now is only allowed with --dry-run")
        from quotes import to_london
        now_fn = (lambda: to_london(a.now)) if a.now else None
        res = book_package(specs, data_dir=a.data_dir, dry_run=a.dry_run, now_fn=now_fn,
                           gbpusd=a.gbpusd, ranks=ranks, day_limit=a.day_limit,
                           simulated_clock=bool(a.now))
        print(json.dumps(res, indent=2, default=str))
        return 0 if res.get("ok") else 2
    prices = dict(x.split("=", 1) for x in a.price)
    legs, fxv = [], a.gbpusd
    for sp in specs:
        inst = _inst(sp["symbol"])
        if inst.symbol in prices:
            px = float(prices[inst.symbol])
        elif a.live:
            px, f, _ = _live_inputs(inst)
            fxv = fxv or f
        else:
            ap.error(f"no price for {inst.symbol}: give --price {inst.symbol}=PX or --live")
        legs.append(PackageLeg(inst.symbol, sp["ticket_qty"], sp["stop"], px, rank_of(inst.symbol, ranks),
                               None if inst.currency == "GBP" else fxv, sp["target"]))
    open_legs = []
    realised = a.realised or 0.0
    if a.from_book:
        ol, rb = book_state(a.data_dir, fx_fn=lambda c: fxv)
        open_legs += ol
        if a.realised is None:
            realised = rb
    for o in a.open:
        sym, q, st, ref = o.split(":")
        inst = _inst(sym)
        open_legs.append(OpenLeg(inst.symbol, float(q), float(st), float(ref),
                                 None if inst.currency == "GBP" else fxv))
    res = package_qty(legs, open_legs, realised, a.day_limit)
    print(json.dumps(res, indent=2, default=str))
    return 0 if res["ok"] else 2


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
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "package":
        return package_main(argv[1:])
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
