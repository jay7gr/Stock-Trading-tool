#!/usr/bin/env python3
"""
One LSE ticket leg, filled honestly from DELAYED Yahoo 1m bars (PAPER, emulator only).

Yahoo .L data is ~20 min delayed, so a live LSE quote can never pass the 60s
rule. Instead the leg is filled at the 1m bar of the DECISION time, once that
bar is visible (~20 min later):

  decision 08:06 -> run at ~08:26-08:50 -> fetch the 08:06 1m bar
  fill price  = that bar's OPEN (raw vendor units -> GBP via the registry; pence /100)
  fill time   = quote_ts = the bar timestamp (08:06:00);  booked_at = wall clock
  quote_source = "yahoo_1m_bar_open_delayed"
  sizing      = PACKAGE-level check (sizing.package_qty, Risk ruling 27 Sep): ticket qty is a
                ceiling; the leg fills while realised loss today + full effective risk of
                every open leg (day ref -> stop, so an LSE stop-out not yet visible still
                counts) + this leg (bar open -> stop, 1.6x, tax) + any --pending legs
                reserved for later (e.g. the US legs at ticket entry) stays <= £200; cuts
                go from the lowest rank up (MSFT, WMT, SHEL, VUSA, TSM), whole shares.
  cancel      = bar open <= ticket stop (nothing booked)
  ATR floor   = |bar open - stop| / ATR14 (GBP) < config.MIN_STOP_ATR_AT_FILL (Risk, default 1.0)
                -> refused before booking (skip alert reason atr_floor; --atr is required,
                a leg without it is refused as atr_missing)
  no look-ahead: bars after the decision minute are discarded before anything is used.

Refused (nothing booked): no bar at that exact minute (illiquid or not yet visible),
as_of in the future / > 45 min old / outside the session, non-GBP or non-LSE lines,
stale monitor heartbeat or market closed at the WALL clock, day halted, qty 0.
After booking, the monitor checks stops from the fill bar onward (bars
between the fill bar and now are checked on its next pass).

CLI
  python lse_leg.py VUSA --at 08:06 --ticket-qty 13 --stop 108.1686 --entry 110.4325 --target 114.9603 \
      --pending TSM:1:429.5215:450.61 --pending WMT:7:103.704:107.98 --pending MSFT:1:492.6326:516.17 --gbpusd 1.325346
  python lse_leg.py SHEL --at 08:06 --ticket-qty 18 --stop 34.7739 --entry 36.11 --target 38.7822 --atr 0.668 --dry-run
  --at HH:MM (today, London) or a full ISO time; --dry-run writes nothing to data/ (runs the
  emulator on a temp copy). --now ISO (dry-run only) simulates the wall clock, e.g. to replay Friday.
  --pending SYM:QTY:STOP:REF_PRICE reserves headroom for legs that fill later (rank-aware);
  --gbpusd is the rate used to value USD pending legs (default: ticket rate 1.325346).
  --entry is informational (ticket entry, reported with the leg); per-leg budgets no longer bind.
Exit code 0 = booked (or would book in dry run), 2 = refused.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from typing import Callable, Optional

import config

assert config.EMULATOR_MODE is True, "lse_leg.py is paper-only: EMULATOR_MODE must be True"

import emulator as emu_mod
import instruments
import quotes
import sizing
from quotes import LONDON, Quote, now_london, to_london

SOURCE = "yahoo_1m_bar_open_delayed"


def parse_at(at: str, now: datetime) -> datetime:
    """'08:06' -> today 08:06 London; ISO strings pass through (naive = London)."""
    if len(at) <= 5 and ":" in at:
        h, m = at.split(":")
        return now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    return to_london(at)


def bar_at(bars, as_of: datetime):
    """The 1m bar starting exactly at as_of, using ONLY bars <= as_of (no look-ahead)."""
    if bars is None or not len(bars):
        return None
    bars = quotes._normalise_bars(bars)
    bars = bars[bars.index <= as_of]                  # discard everything after the decision minute
    if not len(bars) or bars.index[-1] != as_of:
        return None
    return bars.iloc[-1]


def book_lse_leg(symbol: str, at: datetime, ticket_qty: int, stop: float, entry: float,
                 target: Optional[float] = None, *, dry_run: bool = False,
                 data_dir: Optional[str] = None,
                 bars_fn: Optional[Callable] = None,
                 now_fn: Optional[Callable[[], datetime]] = None,
                 simulated_clock: bool = False,
                 reasoning: str = "",
                 pending: Optional[list] = None,
                 gbpusd: Optional[float] = None,
                 ranks=sizing.MONDAY_RANK,
                 day_limit: float = sizing.DAY_LIMIT_GBP,
                 atr: Optional[float] = None,
                 atr_floor: Optional[float] = None) -> dict:
    """Fill one LSE leg at the delayed 1m bar OPEN of `at`. Returns a result dict
    (ok, reason, bar, sizing, trade). Books into data_dir (default data/) unless dry_run."""
    now_fn = now_fn or now_london
    now = to_london(now_fn())
    as_of = to_london(at).replace(second=0, microsecond=0)
    out = {"symbol": symbol, "as_of": as_of.isoformat(), "wall_clock": now.isoformat(),
           "dry_run": dry_run, "simulated_clock": simulated_clock, "ok": False}
    try:
        inst = instruments.resolve(symbol)
    except instruments.UnknownInstrumentError as e:
        out["reason"] = str(e)
        return out
    out.update({"yf_symbol": inst.yf_symbol, "isin": inst.isin, "quote_unit": inst.quote_unit})
    if inst.exchange != "LSE" or inst.currency != "GBP":
        out["reason"] = (f"{inst.symbol} is {inst.listing}: only GBP/GBX LSE lines use the delayed-bar "
                         f"path (a non-GBP line would need FX at as_of)")
        return out
    try:
        emu_mod.PaperTradingEmulator.validate_as_of(inst, as_of, now)
    except emu_mod.AsOfError as e:
        out["reason"] = f"as_of refused: {e}"
        return out

    fetch = bars_fn or quotes.fetch_bars
    bars = fetch(inst.yf_symbol, as_of - timedelta(minutes=10), as_of + timedelta(minutes=1))
    bar = bar_at(bars, as_of)
    if bar is None:
        out["reason"] = (f"no {inst.yf_symbol} 1m bar at {as_of:%H:%M} yet (Yahoo .L ~20 min delay, "
                         f"or no trades in that minute); nothing booked")
        return out
    raw_open = float(bar["Open"])
    px = inst.to_major(raw_open)
    out["bar"] = {"ts": as_of.isoformat(), **{k: float(bar[k]) for k in ("Open", "High", "Low", "Close")
                                              if k in bar}, "open_major": px}
    if px <= stop:
        out["reason"] = f"bar open {px:.4f} is at or below the ticket stop {stop}: leg cancelled"
        return out
    real_dir = data_dir or emu_mod.DATA_DIR
    tmp = sizing.dry_copy(real_dir, now if simulated_clock else None) if dry_run else None
    run_dir = tmp or real_dir
    try:
        # package-level check against the book as it stands at the WALL clock
        fxv = gbpusd or sizing.TICKET_GBPUSD
        try:
            open_legs, realised = sizing.book_state(run_dir if (dry_run or data_dir) else None, now,
                                                    fx_fn=lambda c: fxv if c == "USD" else None)
        except sizing.SizingError as e:
            out["reason"] = f"package check failed: {e}"
            return out
        legs = [sizing.PackageLeg(inst.symbol, ticket_qty, stop, px, sizing.rank_of(inst.symbol, ranks))]
        for sp in pending or []:
            pi = instruments.resolve(sp["symbol"])
            legs.append(sizing.PackageLeg(pi.symbol, sp["ticket_qty"], sp["stop"], sp["price"],
                                          sizing.rank_of(pi.symbol, ranks),
                                          None if pi.currency == "GBP" else fxv))
        pkg = sizing.package_qty(legs, open_legs, realised, day_limit)
        mine = next(r for r in pkg["legs"] if r["symbol"] == inst.symbol)
        out["package"] = pkg
        out["ticket_entry"] = entry
        qty = mine["qty"]
        if qty <= 0:
            out["reason"] = f"package check: {mine['status']} (headroom £{pkg['headroom_gbp']:.2f})"
            return out
        # hard live ATR-floor refusal BEFORE booking (fill = bar open, GBP, same ccy as the stop)
        chk = sizing.atr_floor_check(px, stop, atr, atr_floor)
        out["atr_check"] = chk
        if not chk["ok"]:
            out["skip"] = sizing.record_atr_skip(run_dir, inst.symbol, chk, now,
                                                 path="lse_leg.book_lse_leg", currency=inst.currency)
            out["reason"] = (f"refused: {chk['reason']} (stop/ATR {chk['ratio']} < {chk['floor']}); nothing booked"
                             if chk["reason"] == sizing.ATR_FLOOR_REASON
                             else f"refused: {chk['reason']} (pass --atr); nothing booked")
            return out
        emu = emu_mod.PaperTradingEmulator(
            data_dir=run_dir if (dry_run or data_dir) else None,
            quote_fn=lambda s: None, now_fn=lambda: now, write_alerts=True)
        emu.requote_wait_s = 0
        q = Quote(inst.yf_symbol, raw_open, as_of, SOURCE)
        try:
            tr = emu.execute_buy(
                inst.yf_symbol, qty * inst.to_book_gbp(raw_open), px, stop, target or 0,
                reasoning or f"LSE delayed-bar fill: {as_of:%Y-%m-%d %H:%M} 1m bar open, ticket qty "
                             f"{ticket_qty} -> {qty} ({mine['status']}; package risk "
                             f"£{pkg['combined_risk_gbp']:.2f} <= £{day_limit:.0f})",
                0, 0, 0, ticket_symbol=inst.symbol, quote=q, as_of=as_of, qty=qty)
        except Exception as e:  # noqa: BLE001  (coverage, stale, levels, as_of, instrument)
            out["reason"] = f"{type(e).__name__}: {e}"
            return out
        if tr is None:
            out["reason"] = "not booked (day halted, insufficient cash or below minimum size); see alerts"
            return out
        out["trade"] = {k: getattr(tr, k) for k in ("id", "ticker", "action", "quantity", "price",
                                                     "value_gbp", "timestamp", "quote_ts",
                                                     "quote_source", "booked_at", "stop_loss",
                                                     "take_profit", "isin")}
        out["cash_after"] = round(emu.cash, 2)
        out["ok"] = True
        out["reason"] = "would book (dry run: nothing written to data/)" if dry_run else f"booked into {run_dir}"
        return out
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    ap.add_argument("--at", required=True, help="decision time: HH:MM today (London) or ISO datetime")
    ap.add_argument("--ticket-qty", type=int, required=True)
    ap.add_argument("--stop", type=float, required=True, help="ticket stop, GBP (pounds, also for GBX lines)")
    ap.add_argument("--entry", type=float, required=True, help="ticket entry, GBP")
    ap.add_argument("--target", type=float, default=None, help="ticket target, GBP")
    ap.add_argument("--dry-run", action="store_true", help="write nothing to data/")
    ap.add_argument("--now", default=None, help="simulated wall clock (ISO, London); --dry-run only")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--pending", action="append", default=[],
                    help="SYM:QTY:STOP:REF_PRICE leg filling later (reserves headroom by rank)")
    ap.add_argument("--gbpusd", type=float, default=None)
    ap.add_argument("--rank", default=",".join(sizing.MONDAY_RANK))
    ap.add_argument("--atr", type=float, default=None,
                    help="ATR14 absolute, GBP (required: live stop/ATR floor checked before booking)")
    a = ap.parse_args(argv)
    pending = []
    for p in a.pending:
        sym, q, st, px = p.split(":")
        pending.append({"symbol": sym, "ticket_qty": int(q), "stop": float(st), "price": float(px)})
    if a.now and not a.dry_run:
        ap.error("--now is only allowed with --dry-run")
    now_fn = (lambda: to_london(a.now)) if a.now else now_london
    at = parse_at(a.at, to_london(now_fn()))
    res = book_lse_leg(a.symbol, at, a.ticket_qty, a.stop, a.entry, a.target, dry_run=a.dry_run,
                       data_dir=a.data_dir, now_fn=now_fn, simulated_clock=bool(a.now),
                       pending=pending, gbpusd=a.gbpusd, atr=a.atr,
                       ranks=tuple(x.strip().upper() for x in a.rank.split(",")))
    print(json.dumps(res, indent=2, default=str))
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
