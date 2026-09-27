#!/usr/bin/env python3
"""
Stop/target monitor for the paper book (EMULATOR ONLY — never talks to a broker).

Modes
  python monitor.py                 one pass over open positions (live yfinance 1m bars)
  python monitor.py --loop          run every minute, around the clock (Risk SOP v2.2):
                                    network checks only for positions whose exchange is
                                    open (plus one final check after each close)
  python monitor.py --replay 2026-09-25 \
      --position ISPY.L:37.75:37.3725:38.6937:2026-09-25T08:22
                                    replay a day minute-by-minute on real 1m bars
                                    (tests/fixtures cache first, else yfinance, then cached)

Each pass (live):
  * pulls 1m bars since max(floor(opened_at), last_check) per position
    (bar_checks: Low<=stop -> exit min(stop, Open); High>=target -> target;
    both in one bar -> stop), books SELLs via the emulator (data/trades.json,
    data/portfolio.json), stores last_check;
  * first pass of each London day stamps every carried position's previous
    close (SOP v2.3: prev_close = last mark taken before midnight, GBP);
  * recomputes ONE day P&L across all markets in GBP (realised on the London
    day + change in open mark-to-market since the previous close, or since
    entry for positions opened today; pnl.py); at <= config.DAILY_STOP_LOSS (-£200)
    sets halted and flattens every position whose market is open; positions
    on closed markets are queued (pending_flatten) and sold at their next open;
  * refreshes data/live_status.json (day_pnl, remaining_day_risk_gbp, halted,
    open_positions, closed_today, last_monitor) and data/dashboard_snapshot.json;
  * appends events to data/alerts_log.jsonl
    {event_ts, detected_ts, type, ticker, details, notified:false}.
When flat, or when every open position's market is closed, it only reads
portfolio.json and touches a heartbeat file (no network).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, date
from typing import Callable, Optional

import config

assert config.EMULATOR_MODE is True, "monitor.py is paper-only: EMULATOR_MODE must be True"

import instruments
import quotes
from quotes import now_london, to_london, StaleQuoteError
from bar_checks import check_position, floor_minute, BAR
from alerts import append_alert
import emulator as emu_mod
import pnl

NO_TRIGGER_EVERY_S = 30 * 60


# ─── small file helpers ──────────────────────────────────────────────

def _read_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: str, obj) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def market_state(ticker: str, now: datetime):
    """('open'|'final'|'closed'|'unknown', Exchange|None, window)."""
    inst = instruments.lookup(ticker)
    if inst is None:
        return "unknown", None, None
    try:
        ex = instruments.exchange_of(inst)
    except instruments.NoSessionDataError:
        return "unknown", None, None
    st, w = ex.state(now)
    return st, ex, w


def due_tickers(positions: dict, now: datetime, final_done: dict) -> tuple[list, list]:
    """Tickers needing a bar check now, and final-check keys consumed.
    'unknown' (legacy, no session data) is always checked — conservative."""
    due, keys = [], []
    for tkr in positions:
        st, ex, w = market_state(tkr, now)
        if st in ("open", "unknown"):
            due.append(tkr)
        elif st == "final":
            key = f"{tkr}@{w[1].isoformat()}"
            if key not in final_done:
                due.append(tkr)
                keys.append(key)
    return due, keys


def day_realised(emu, day: date) -> float:
    return pnl.realised_on(emu.trade_history, day)


def _pos_rows(emu, today: Optional[date] = None) -> list[dict]:
    rows = []
    for t, p in emu.positions.items():
        inst = instruments.lookup(t)
        ref, ref_src = pnl.day_ref(p, today) if today else (p.avg_entry_price, "entry")
        stop_gbp = emu.level_to_gbp(p, p.stop_loss)
        tgt_gbp = emu.level_to_gbp(p, p.take_profit)
        rows.append({
            "ticker": t,
            "freetrade_ticker": inst.freetrade_ticker if inst else None,
            "isin": p.isin or (inst.isin if inst else None),
            "qty": p.quantity,
            "entry_gbp": p.avg_entry_price,
            "stop_gbp": None if stop_gbp is None else round(stop_gbp, 4),
            "target_gbp": None if tgt_gbp is None else round(tgt_gbp, 4),
            "level_ccy": p.level_ccy or "GBP",
            "stop_level": p.stop_loss,
            "target_level": p.take_profit,
            "day_ref_gbp": ref,
            "day_ref_source": ref_src,
            "day_change_gbp": round(p.quantity * (p.current_price - ref), 2),
            "mark_gbp": p.current_price,
            "value_gbp": round(p.quantity * p.current_price, 2),
            "unrealised_gbp": round(p.quantity * (p.current_price - p.avg_entry_price), 2),
            "opened_at": p.opened_at,
            "last_check": p.last_check,
        })
    return rows


def write_status(emu, data_dir: str, now: datetime, closed: list, action: str,
                 reason: str, halted: Optional[bool] = None) -> dict:
    """Merge monitor fields into live_status.json and rewrite dashboard_snapshot.json."""
    ls_path = os.path.join(data_dir, "live_status.json")
    ls = _read_json(ls_path, {})
    today = now.date()
    unreal = pnl.open_mtm(emu.positions.values())          # since entry (info)
    day_pnl = round(pnl.day_pnl(emu.trade_history, emu.positions.values(), today), 2)  # SOP v2.3
    if halted is None:
        halted = bool(ls.get("halted")) and (ls.get("halted_date") or str(ls.get("as_of", ""))[:10]) == today.isoformat()
    ls.update({
        "as_of": now.isoformat(),
        "status_date": today.isoformat(),
        "mode": ls.get("mode", "paper_emulator"),
        "cash": round(emu.cash, 2),
        "open_positions": _pos_rows(emu, today),
        "day_pnl": day_pnl,
        "day_stop": config.DAILY_STOP_LOSS,
        "remaining_day_risk_gbp": round(max(0.0, day_pnl - config.DAILY_STOP_LOSS), 2),
        "combined_stop_risk": round(sum(max(0.0, p.quantity * (p.avg_entry_price - (emu.level_to_gbp(p, p.stop_loss) or 0.0)))
                                        for p in emu.positions.values()), 2),
        # SOP v2.3 5b: open risk measured from today's reference (prev close) to each stop
        "open_risk_from_day_ref_gbp": round(sum(max(0.0, p.quantity * (pnl.day_ref(p, today)[0] - (emu.level_to_gbp(p, p.stop_loss) or 0.0)))
                                                for p in emu.positions.values()), 2),
        "halted": halted,
        "last_check": now.isoformat(),
    })
    if halted:
        ls["halted_date"] = ls.get("halted_date") if ls.get("halted_date") == today.isoformat() else today.isoformat()
    ct = ls.setdefault("closed_today", [])
    for t in closed:
        inst = instruments.lookup(t.ticker)
        buy = next((b for b in reversed(emu.trade_history)
                    if b.ticker == t.ticker and b.action == "BUY"), None)
        ct.append({
            "trade_id": t.id, "ticker": t.ticker,
            "freetrade_ticker": inst.freetrade_ticker if inst else None,
            "side": "SELL", "status": t.status,
            "entry_gbp": buy.price if buy else None, "exit_gbp": t.price,
            "qty": t.quantity, "pnl_gbp": round(t.pnl, 2),
            "stop_gbp": buy.stop_loss if buy else None,
            "exit_at": t.timestamp, "detected_at": now.isoformat(),
            "quote_ts": t.quote_ts, "quote_source": t.quote_source,
        })
    ls["last_monitor"] = {
        "at": now.isoformat(), "action": action, "reason": reason,
        "closed_trades": [t.id for t in closed],
        "source": "monitor.py",
    }
    _write_json(ls_path, ls)

    pv = emu.cash + sum(p.quantity * p.current_price for p in emu.positions.values())
    snap = {
        "updated_at": now.isoformat(),
        "cash": round(emu.cash, 2),
        "portfolio_value": round(pv, 2),
        "unrealised_pnl": round(unreal, 2),
        "realised_pnl": round(sum(t.pnl for t in emu.trade_history if t.action == "SELL"), 2),
        "day_pnl": day_pnl,
        "positions": _pos_rows(emu, today),
    }
    _write_json(os.path.join(data_dir, "dashboard_snapshot.json"), snap)
    return ls


def rollover_if_new_day(data_dir: str, now: datetime) -> bool:
    """First pass of a new London day: reset halted/day P&L, archive closed_today."""
    ls_path = os.path.join(data_dir, "live_status.json")
    ls = _read_json(ls_path, None)
    if not ls:
        return False
    today = now.date().isoformat()
    status_date = ls.get("status_date") or str(ls.get("as_of", ""))[:10]
    if status_date == today:
        return False
    ls["closed_previous_session"] = {"date": status_date, "trades": ls.get("closed_today", []),
                                     "day_pnl": ls.get("day_pnl"), "halted": ls.get("halted")}
    ls["closed_today"] = []
    ls["status_date"] = today
    ls["day_pnl"] = 0.0
    ls["remaining_day_risk_gbp"] = round(-config.DAILY_STOP_LOSS, 2)
    if ls.get("halted") and ls.get("halted_date") != today:
        ls["halted"] = False
    ls["as_of"] = now.isoformat()
    _write_json(ls_path, ls)
    print(f"[monitor] new-day rollover {status_date} -> {today}", flush=True)
    return True


def stamp_prev_close(data_dir: str, now: datetime) -> list[str]:
    """SOP v2.3: on the London day's first pass, record each carried position's
    previous close (GBP) as its day reference.

    The reference is the last mark taken before London midnight — the monitor's
    final post-close check sets that mark from the last completed 1m bar of the
    market's previous session (converted to GBP at the FX used for that mark).
    Positions opened today are skipped (they measure from entry). If the mark
    is missing or not from before midnight, nothing is stamped and pnl.day_ref
    falls back to entry with source "entry_fallback_no_prev_close".
    Returns the tickers stamped."""
    path = os.path.join(data_dir, "portfolio.json")
    port = _read_json(path, None)
    if not port or not port.get("positions"):
        return []
    today = now.date()
    midnight = datetime(today.year, today.month, today.day, tzinfo=quotes.LONDON)
    stamped = []
    for tkr, p in port["positions"].items():
        opened = pnl.trade_day(p.get("opened_at") or None)
        if opened is None or opened >= today or p.get("prev_close_date") == today.isoformat():
            continue
        lc = to_london(p["last_check"]) if p.get("last_check") else None
        mark = p.get("current_price")
        if lc is None or lc > midnight or not mark:
            continue
        p["prev_close"] = float(mark)
        p["prev_close_date"] = today.isoformat()
        p["prev_close_source"] = f"last_mark_before_midnight(last_bar_end={lc.isoformat()})"
        stamped.append(tkr)
    if stamped:
        _write_json(path, port)
        print(f"[monitor] prev_close stamped for {today}: "
              f"{ {t: port['positions'][t]['prev_close'] for t in stamped} }", flush=True)
    return stamped


# ─── one live pass ───────────────────────────────────────────────────

def _heartbeat(data_dir: str, now: datetime, extra: Optional[dict] = None) -> None:
    os.makedirs(data_dir, exist_ok=True)
    hb = {"at": now.isoformat(), "pid": os.getpid(), **(extra or {})}
    tmp = os.path.join(data_dir, f"monitor_heartbeat.json.tmp.{os.getpid()}")
    with open(tmp, "w") as f:
        json.dump(hb, f)
    os.replace(tmp, os.path.join(data_dir, "monitor_heartbeat.json"))


def _flatten(emu, tkr: str, now: datetime, data_dir: str, why: str):
    """Sell one position on a fresh quote; fall back to the last 1m close."""
    pos = emu.positions[tkr]
    try:
        tr = emu.execute_sell(tkr, why, status="closed")
    except Exception as e:  # noqa: BLE001  (stale quote / fetch failure)
        last_bar = to_london(pos.last_check) - BAR if pos.last_check else now
        tr = emu.execute_sell(tkr, f"{why} at last 1m close (fresh quote unavailable: {e})",
                              fill_price=pos.current_price, fill_ts=now,
                              quote_ts=last_bar, quote_source="yfinance_1m_bar_close_fallback",
                              status="closed")
    if tr:
        append_alert(data_dir, "exit", tkr, {"trade_id": tr.id, "reason": why, "price": tr.price,
                                             "pnl": tr.pnl, "quote_ts": tr.quote_ts},
                     event_ts=now, detected_ts=now)
    return tr


def run_once(data_dir: Optional[str] = None, now: Optional[datetime] = None,
             bars_fn: Optional[Callable] = None, quote_fn: Optional[Callable] = None) -> dict:
    data_dir = data_dir or emu_mod.DATA_DIR
    now = to_london(now or now_london())
    _heartbeat(data_dir, now)

    lock = open(os.path.join(data_dir, ".monitor.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return {"action": "skipped_locked"}
    try:
        rolled = rollover_if_new_day(data_dir, now)
        stamp_prev_close(data_dir, now)
        port = _read_json(os.path.join(data_dir, "portfolio.json"), {})
        positions = port.get("positions") or {}
        if not positions:
            return {"action": "flat", "rolled": rolled}   # idle: no network

        st_path = os.path.join(data_dir, "monitor_state.json")
        st = _read_json(st_path, {})
        final_done = st.setdefault("final_checks_done", {})
        pending = [t for t in st.get("pending_flatten", []) if t in positions]
        due, final_keys = due_tickers(positions, now, final_done)
        pending_open = [t for t in pending if market_state(t, now)[0] in ("open", "unknown")]
        if not due and not pending_open:
            return {"action": "markets_closed", "open_positions": sorted(positions),
                    "rolled": rolled}                     # idle: no network

        emu = emu_mod.PaperTradingEmulator(data_dir=data_dir if data_dir != emu_mod.DATA_DIR else None,
                                           bars_fn=bars_fn, quote_fn=quote_fn,
                                           now_fn=lambda: now)
        emu.requote_wait_s = 3.0
        closed = emu.check_stops_and_targets(now, tickers=due)
        for k in final_keys:
            final_done[k] = now.isoformat()
        # keep the final-check ledger small
        cutoff = (now - timedelta(days=4)).isoformat()
        st["final_checks_done"] = {k: v for k, v in final_done.items() if v >= cutoff}

        ls = _read_json(os.path.join(data_dir, "live_status.json"), {})
        today = now.date()
        already_halted = pnl.halted_on(ls, today)
        day_pnl = pnl.day_pnl(emu.trade_history, emu.positions.values(), today)
        action, reason, halted = "no_change", "no stop/target touched", None
        if closed:
            action = "exits_booked"
            reason = "; ".join(f"{t.ticker} {t.status} @ {t.price:.4f} (P&L {t.pnl:+.2f})" for t in closed)

        # queued flattens (halt hit while their market was closed)
        for tkr in [t for t in pending if t in emu.positions]:
            if market_state(tkr, now)[0] in ("open", "unknown"):
                tr = _flatten(emu, tkr, now, data_dir, "halt: queued flatten at market open")
                if tr:
                    closed.append(tr)
                    pending.remove(tkr)
                    action = "pending_flatten_done"

        if day_pnl <= config.DAILY_STOP_LOSS and not already_halted:
            flat, queued = [], []
            for tkr in list(emu.positions.keys()):
                if market_state(tkr, now)[0] in ("open", "final", "unknown"):
                    tr = _flatten(emu, tkr, now, data_dir, "halt: day P&L <= -£200, flatten all")
                    if tr:
                        flat.append(tr)
                else:
                    queued.append(tkr)
                    if tkr not in pending:
                        pending.append(tkr)
            closed += flat
            day_pnl = pnl.day_pnl(emu.trade_history, emu.positions.values(), today)
            append_alert(data_dir, "halt", "*", {"day_pnl": round(day_pnl, 2),
                                                "limit": config.DAILY_STOP_LOSS,
                                                "flattened": [t.id for t in flat],
                                                "queued_until_market_open": queued},
                         event_ts=now, detected_ts=now)
            action, halted = "halt_flatten_all", True
            reason = (f"day P&L {day_pnl:+.2f} <= {config.DAILY_STOP_LOSS:.2f} (all markets, GBP): "
                      f"flattened {len(flat)}, queued {queued}; " + reason)
        st["pending_flatten"] = pending

        # periodic no_trigger heartbeat per checked ticker
        nt = st.setdefault("last_no_trigger", {})
        for tkr in due:
            if tkr not in emu.positions:
                continue
            pos = emu.positions[tkr]
            last = nt.get(tkr)
            if not last or (now - to_london(last)).total_seconds() >= NO_TRIGGER_EVERY_S:
                append_alert(data_dir, "no_trigger", tkr,
                             {"mark": pos.current_price, "stop": pos.stop_loss,
                              "target": pos.take_profit, "last_check": pos.last_check},
                             detected_ts=now)
                nt[tkr] = now.isoformat()
        _write_json(st_path, st)

        ls = write_status(emu, data_dir, now, closed, action, reason, halted=halted)
        if pending:
            ls["pending_flatten"] = pending
            _write_json(os.path.join(data_dir, "live_status.json"), ls)
        return {"action": action, "checked": due, "closed": [t.id for t in closed],
                "day_pnl": round(day_pnl, 2), "pending_flatten": pending}
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def loop(data_dir: Optional[str] = None) -> None:
    """Around-the-clock loop (Risk SOP v2.2). Every minute (5s past) it writes a
    heartbeat and runs a pass; the pass only touches the network for positions
    whose exchange is open or in its post-close final-check window."""
    # Singleton: hold an exclusive lock for the loop's lifetime so a second
    # loop (e.g. started by another job) exits instead of double-running.
    ddir = data_dir or emu_mod.DATA_DIR
    os.makedirs(ddir, exist_ok=True)
    loop_lock = open(os.path.join(ddir, ".monitor_loop.lock"), "a+")
    try:
        fcntl.flock(loop_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"[monitor] another monitor loop holds {ddir}/.monitor_loop.lock; pid={os.getpid()} exiting",
              flush=True)
        return
    loop_lock.seek(0)
    loop_lock.truncate()
    loop_lock.write(str(os.getpid()))
    loop_lock.flush()
    print(f"[monitor] loop start pid={os.getpid()} at {now_london().isoformat()} (24x5 market-aware)",
          flush=True)
    last_log, last_action = 0.0, None
    while True:
        now = now_london()
        try:
            res = run_once(data_dir, now)
            act = res.get("action")
            quiet = act in ("flat", "no_change", "markets_closed") and act == last_action
            if not quiet or time.time() - last_log > 1800:
                print(f"[monitor] {now:%Y-%m-%d %H:%M:%S} {res}", flush=True)
                last_log = time.time()
            last_action = act
        except Exception:  # noqa: BLE001
            print(f"[monitor] {now:%H:%M:%S} ERROR\n{traceback.format_exc()}", flush=True)
        # wake 5s after the next minute boundary so the just-closed bar is available
        nxt = (now + timedelta(minutes=1)).replace(second=5, microsecond=0)
        time.sleep(max(1.0, (nxt - now_london()).total_seconds()))


# ─── replay ──────────────────────────────────────────────────────────

def parse_position(spec: str) -> dict:
    sym, entry, stop, target, opened = spec.split(":", 4)
    return {"ticker": sym, "entry": float(entry), "stop": float(stop),
            "target": float(target), "opened_at": opened}


def replay_day(day: str, specs: list[dict], bars_loader: Callable[[str, str], "pd.DataFrame"] = None,
               end_hm: tuple = (16, 35), alerts_path: Optional[str] = None) -> list[dict]:
    """Minute-by-minute replay on real bars; returns alert-shaped events.

    Levels are in major units of the instrument's trading currency
    (GBP for GBX/GBP lines, USD for USD lines)."""
    bars_loader = bars_loader or (lambda sym, d: quotes.load_day_bars(sym, d))
    events = []
    for sp in specs:
        inst = instruments.resolve(sp["ticker"])
        bars = bars_loader(inst.yf_symbol, day)
        to_book = inst.to_major   # levels in major units of the trading currency
        opened = to_london(sp["opened_at"])
        events.append({"event_ts": opened.isoformat(), "detected_ts": opened.isoformat(),
                       "type": "fill", "ticker": inst.yf_symbol,
                       "details": {"entry": sp["entry"], "stop": sp["stop"], "target": sp["target"],
                                   "replay": True}, "notified": False})
        d = date.fromisoformat(day)
        end = datetime(d.year, d.month, d.day, *end_hm, tzinfo=quotes.LONDON)
        clock = floor_minute(opened) + BAR
        last_check = None
        hit = None
        while clock <= end:
            res = check_position(bars, stop=sp["stop"], target=sp["target"], opened_at=opened,
                                 last_check=last_check, now=clock, to_book=to_book)
            for bt in res.skipped_bad_ticks:
                row = bars[bars.index == bt].iloc[0]
                events.append({"event_ts": bt.isoformat(), "detected_ts": clock.isoformat(),
                               "type": "skip", "ticker": inst.yf_symbol,
                               "details": {"reason": "bad_tick_unconfirmed_by_next_bar",
                                           "bar": {k: float(row[k]) for k in ("Open", "High", "Low", "Close", "Volume") if k in row},
                                           "replay": True},
                               "notified": False})
            if res.exit:
                hit = res
                break
            last_check = res.last_check
            clock += BAR
        if hit:
            ex = hit.exit
            events.append({"event_ts": ex.bar_ts.isoformat(), "detected_ts": clock.isoformat(),
                           "type": ex.kind, "ticker": inst.yf_symbol,
                           "details": {**ex.to_dict(), "entry": sp["entry"],
                                       "pnl_per_unit": round(ex.price - sp["entry"], 6),
                                       "replay": True},
                           "notified": False})
        else:
            events.append({"event_ts": end.isoformat(), "detected_ts": end.isoformat(),
                           "type": "no_trigger", "ticker": inst.yf_symbol,
                           "details": {"replay": True, "note": "no stop/target touched"},
                           "notified": False})
    if alerts_path:
        with open(alerts_path, "a") as f:
            for e in events:
                f.write(json.dumps(e, default=str) + "\n")
    return events


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--replay", metavar="YYYY-MM-DD")
    ap.add_argument("--position", action="append", default=[],
                    help="SYMBOL:entry:stop:target:opened_at (replay)")
    ap.add_argument("--no-fetch", action="store_true", help="replay: use cached fixtures only")
    ap.add_argument("--alerts-out", default=None, help="replay: also append events to this file")
    a = ap.parse_args(argv)

    if a.replay:
        if not a.position:
            ap.error("--replay needs at least one --position")
        loader = None
        if a.no_fetch:
            loader = lambda s, d: quotes.load_day_bars(s, d, allow_fetch=False)
        evs = replay_day(a.replay, [parse_position(p) for p in a.position], loader,
                         alerts_path=a.alerts_out)
        for e in evs:
            print(json.dumps(e, default=str))
        for e in evs:
            if e["type"] in ("stop", "target"):
                print(f"REPLAY {e['type'].upper()} {e['ticker']}: bar {e['event_ts']} "
                      f"flagged at {e['detected_ts']} exit {e['details']['price']:.4f}"
                      f"{' (gapped)' if e['details']['gapped'] else ''}")
        return 0
    if a.loop:
        loop(a.data_dir)
        return 0
    print(json.dumps(run_once(a.data_dir), default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
