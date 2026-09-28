"""Delayed-bar LSE fills (Yahoo .L ~20 min late): fill at the decision-minute 1m bar
OPEN, timestamped at that bar, no look-ahead, 45-min as_of window, wall-clock coverage."""
import json
import os
from datetime import datetime

import pandas as pd
import pytest

import emulator
import lse_leg
from quotes import LONDON, Quote, StaleQuoteError, load_day_bars

# atr = Research ATR14 abs, GBP (_atr_completed_bars.json use_atr_abs; SHEL 66.845p / 100)
VUSA = dict(ticket_qty=13, stop=108.1686, entry=110.4325, target=114.9603, atr=0.9006)
SHEL = dict(ticket_qty=18, stop=34.7739, entry=36.11, target=38.7822, atr=0.6685)


def L(h, m, s=0, d=25):
    return datetime(2026, 9, d, h, m, s, tzinfo=LONDON)


def fixture_fn(sym, since, now):
    b = load_day_bars(sym, "2026-09-25", allow_fetch=False)
    return b[(b.index >= since) & (b.index <= now)]


def setup_dir(tmp_path, hb_at):
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": hb_at.isoformat(), "pid": 1}))
    return str(tmp_path)


def book(tmp_path, sym, at, now, params, bars_fn=fixture_fn, hb=None, **kw):
    d = setup_dir(tmp_path, hb or now)
    return lse_leg.book_lse_leg(sym, at, data_dir=d, bars_fn=bars_fn, now_fn=lambda: now, **params, **kw)


def test_bar_timestamped_fill_passes_freshness_and_books(tmp_path):
    r = book(tmp_path, "SHEL", L(8, 6), L(8, 36, 10), SHEL)
    assert r["ok"], r["reason"]
    t = r["trade"]
    assert t["price"] == pytest.approx(36.045)                    # 08:06 open 3604.5p / 100
    assert t["quantity"] == 18 and t["value_gbp"] == pytest.approx(18 * 36.045)
    assert t["timestamp"] == t["quote_ts"] == "2026-09-25T08:06:00+01:00"
    assert t["quote_source"] == "yahoo_1m_bar_open_delayed"
    assert t["booked_at"] == "2026-09-25T08:36:10+01:00"
    port = json.loads((tmp_path / "portfolio.json").read_text())["positions"]["SHEL.L"]
    assert port["opened_at"] == "2026-09-25T08:06:00+01:00" and port["last_check"] == ""
    fill = [json.loads(l) for l in (tmp_path / "alerts_log.jsonl").read_text().splitlines()][-1]
    assert fill["type"] == "fill" and fill["event_ts"].startswith("2026-09-25T08:06:00")
    assert fill["detected_ts"].startswith("2026-09-25T08:36:10")


def test_vusa_sized_at_bar_open(tmp_path):
    r = book(tmp_path, "VUSA", L(8, 6), L(8, 36, 10), VUSA)
    # package-level check: per-leg budget no longer binds -> full ticket qty 13 fits under £200
    leg = r["package"]["legs"][0]
    assert r["ok"] and leg["qty"] == 13 and r["trade"]["quantity"] == 13
    assert leg["eff_rps_gbp"] == pytest.approx((110.57499694824219 - 108.1686) * 1.6)
    assert r["trade"]["price"] == pytest.approx(r["bar"]["Open"])      # GBP line: no /100


@pytest.mark.parametrize("now,why", [
    (L(8, 51, 1), "min old"),          # 45 min 1 s after the bar
    (L(8, 5, 30), "future"),           # bar time after the wall clock
])
def test_stale_or_future_as_of_refused_nothing_written(tmp_path, now, why):
    r = book(tmp_path, "VUSA", L(8, 6), now, VUSA)
    assert not r["ok"] and why in r["reason"]
    assert not (tmp_path / "trades.json").exists() and not (tmp_path / "portfolio.json").exists()


def test_as_of_outside_session_refused(tmp_path):
    r = book(tmp_path, "VUSA", L(7, 55), L(8, 10), VUSA)
    assert not r["ok"] and "outside the LSE session" in r["reason"]


def test_refused_when_bar_open_at_or_below_stop(tmp_path):
    # VUSA 08:06 open 110.575: a stop above the open cancels the leg
    r = book(tmp_path, "VUSA", L(8, 6), L(8, 36), dict(VUSA, stop=110.58, entry=112.0))
    assert not r["ok"] and "at or below the ticket stop" in r["reason"]
    # open exactly AT the stop also cancels
    bar = pd.DataFrame([[108.1686, 108.3, 108.1, 108.2, 10]],
                       columns=["Open", "High", "Low", "Close", "Volume"],
                       index=pd.DatetimeIndex([pd.Timestamp("2026-09-25 08:06", tz=LONDON)]))
    r = book(tmp_path, "VUSA", L(8, 6), L(8, 36), VUSA, bars_fn=lambda s, a, b: bar)
    assert not r["ok"] and "at or below the ticket stop" in r["reason"]
    assert not (tmp_path / "trades.json").exists()


def test_later_bars_ignored_no_look_ahead(tmp_path):
    real = load_day_bars("SHEL.L", "2026-09-25", allow_fetch=False)
    fake_later = pd.DataFrame([[3000.0, 3000.0, 2900.0, 2950.0, 1]],
                              columns=["Open", "High", "Low", "Close", "Volume"],
                              index=pd.DatetimeIndex([pd.Timestamp("2026-09-25 08:07", tz=LONDON)]))
    both = pd.concat([real[real.index <= L(8, 6)], fake_later])
    seen = []

    def leaky(sym, since, now):          # a source that also returns bars after the decision minute
        seen.append(now)
        return both
    r = book(tmp_path, "SHEL", L(8, 6), L(8, 36), SHEL, bars_fn=leaky)
    assert r["ok"] and r["trade"]["price"] == pytest.approx(36.045)
    assert r["bar"]["Low"] == pytest.approx(3602.5)           # raw pence of the 08:06 bar, not 08:07 2900p
    assert r["package"]["legs"][0]["live_price"] == pytest.approx(36.045)


def test_missing_decision_bar_is_refused_not_replaced(tmp_path):
    real = load_day_bars("SHEL.L", "2026-09-25", allow_fetch=False)
    gap = real[real.index != L(8, 6)]                         # 08:06 not (yet) visible, 08:07 is
    r = book(tmp_path, "SHEL", L(8, 6), L(8, 36), SHEL, bars_fn=lambda s, a, b: gap)
    assert not r["ok"] and "no SHEL.L 1m bar at 08:06" in r["reason"]


def test_coverage_uses_wall_clock_heartbeat(tmp_path):
    r = book(tmp_path, "SHEL", L(8, 6), L(8, 36), SHEL, hb=L(8, 20))    # heartbeat 16 min old at wall clock
    assert not r["ok"] and "MarketNotCoveredError" in r["reason"]


def test_non_gbp_lse_line_refused(tmp_path):
    r = book(tmp_path, "CIBR", L(8, 6), L(8, 36), dict(ticket_qty=1, stop=1, entry=2))
    assert not r["ok"] and "only GBP/GBX LSE lines" in r["reason"]


def test_dry_run_writes_nothing(tmp_path):
    d = setup_dir(tmp_path, L(8, 36))
    (tmp_path / "portfolio.json").write_text('{"cash": 20000, "trade_counter": 0, "positions": {}}')
    (tmp_path / "trades.json").write_text("[]")
    before = {f: (tmp_path / f).read_bytes() for f in os.listdir(d)}
    r = lse_leg.book_lse_leg("SHEL", L(8, 6), data_dir=d, bars_fn=fixture_fn,
                             now_fn=lambda: L(8, 36), dry_run=True, **SHEL)
    assert r["ok"] and r["dry_run"] and r["trade"]["quantity"] == 18
    assert {f: (tmp_path / f).read_bytes() for f in os.listdir(d)} == before


def test_emulator_as_of_freshness_is_against_fill_time(make_emulator):
    now = L(8, 36)
    e = make_emulator(now)
    kw = dict(ticket_symbol="SHEL", as_of=L(8, 6), qty=18)
    # quote 2 min before the fill time -> stale vs as_of
    with pytest.raises(StaleQuoteError):
        e.execute_buy("SHEL.L", 0, None, 34.7739, 38.7822, "t", 0, 0, 0,
                      quote=Quote("SHEL.L", 3604.5, L(8, 4), "t"), **kw)
    # quote AFTER the fill time = look-ahead -> refused
    with pytest.raises(StaleQuoteError, match="after"):
        e.execute_buy("SHEL.L", 0, None, 34.7739, 38.7822, "t", 0, 0, 0,
                      quote=Quote("SHEL.L", 3604.5, L(8, 20), "t"), **kw)
    # as_of without an explicit quote -> refused (no live re-quote)
    with pytest.raises(emulator.AsOfError):
        e.execute_buy("SHEL.L", 0, None, 34.7739, 38.7822, "t", 0, 0, 0, **kw)
    t = e.execute_buy("SHEL.L", 0, None, 34.7739, 38.7822, "t", 0, 0, 0,
                      quote=Quote("SHEL.L", 3604.5, L(8, 6), "yahoo_1m_bar_open_delayed"), **kw)
    assert t.timestamp.startswith("2026-09-25T08:06:00") and t.booked_at.startswith("2026-09-25T08:36")


def test_cli_now_requires_dry_run():
    with pytest.raises(SystemExit):
        lse_leg.main(["SHEL", "--at", "08:06", "--ticket-qty", "18", "--stop", "34.7739",
                      "--entry", "36.11", "--now", "2026-09-25T08:36:10"])


# ─── package check inside the LSE leg ────────────────────────────────

def _seed_book(tmp_path, positions, trades=()):
    (tmp_path / "portfolio.json").write_text(json.dumps({"cash": 20000, "trade_counter": len(trades),
                                                          "positions": positions}))
    (tmp_path / "trades.json").write_text(json.dumps(list(trades)))


def test_lse_leg_trimmed_by_open_risk_and_realised_loss(tmp_path):
    # realised -£150 today + open VUSA 13 @ 110.4325 (full risk £47.09 to stop) -> headroom £2.91
    _seed_book(tmp_path, {"VUSA.L": {"ticker": "VUSA.L", "quantity": 13, "avg_entry_price": 110.4325,
                                     "current_price": 107.0, "stop_loss": 108.1686, "take_profit": 114.9603,
                                     "opened_at": "2026-09-25T08:01:00+01:00"}},
               [{"id": "T00001", "ticker": "IESU.L", "action": "SELL", "quantity": 1, "price": 1,
                 "value_gbp": 1, "timestamp": "2026-09-25T08:03:00+01:00", "reasoning": "", "claude_score": 0,
                 "grok_score": 0, "combined_score": 0, "stop_loss": 0, "take_profit": 0, "pnl": -150.0}])
    r = book(tmp_path, "SHEL", L(8, 6), L(8, 36), SHEL)
    pkg = r["package"]
    assert pkg["open_risk_gbp"] == pytest.approx(13 * 3.6222, abs=0.01)   # full risk even though mark < stop
    assert pkg["realised_loss_today"] == 150.0
    assert r["ok"] and r["trade"]["quantity"] == 1                        # floor(2.91 / 2.214) = 1
    assert pkg["combined_risk_gbp"] <= 200.0


def test_pending_higher_rank_leg_reserves_headroom_lower_ranks_do_not(tmp_path):
    _seed_book(tmp_path, {}, [{"id": "T00001", "ticker": "IESU.L", "action": "SELL", "quantity": 1,
                               "price": 1, "value_gbp": 1, "timestamp": "2026-09-25T08:03:00+01:00",
                               "reasoning": "", "claude_score": 0, "grok_score": 0, "combined_score": 0,
                               "stop_loss": 0, "take_profit": 0, "pnl": -150.0}])
    # headroom £50: SHEL 18 x 2.214 = £39.85 fits alone ...
    r = book(tmp_path, "SHEL", L(8, 6), L(8, 36), SHEL,
             pending=[{"symbol": "WMT", "ticket_qty": 7, "stop": 103.704, "price": 107.98},
                      {"symbol": "MSFT", "ticket_qty": 1, "stop": 492.6326, "price": 516.17}])
    assert r["trade"]["quantity"] == 18                  # lower-ranked pending legs are cut first
    # ... but a pending TSM (rank 1, £28.11) is protected: SHEL trimmed to floor(21.89/2.214) = 9
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    _seed_book(tmp2, {}, json.loads((tmp_path / "trades.json").read_text())[:1])
    r = book(tmp2, "SHEL", L(8, 6), L(8, 36), SHEL,
             pending=[{"symbol": "TSM", "ticket_qty": 1, "stop": 429.5215, "price": 450.61}])
    assert r["trade"]["quantity"] == 9


# ─── LSE stop exits at the true breach bar under the 20-min delay ─────

def _lse_pos(tmp_path, stop):
    _seed_book(tmp_path, {"SHEL.L": {"ticker": "SHEL.L", "quantity": 18, "avg_entry_price": 36.045,
                                     "current_price": 36.045, "stop_loss": stop, "take_profit": 38.7822,
                                     "opened_at": "2026-09-28T08:06:00+01:00"}})
    (tmp_path / "live_status.json").write_text(json.dumps({"status_date": "2026-09-28", "halted": False,
                                                           "as_of": "2026-09-28T08:00:00+01:00"}))


def _frame(rows):
    return pd.DataFrame([r[1:] for r in rows], columns=["Open", "High", "Low", "Close"],
                        index=pd.DatetimeIndex([pd.Timestamp(t, tz=LONDON) for t, *_ in rows]))


def test_lse_stop_booked_at_true_breach_bar_not_alert_time(tmp_path):
    import monitor
    _lse_pos(tmp_path, 35.80)
    base = [("2026-09-28 08:%02d" % m, 3604, 3606, 3600, 3603) for m in range(6, 30)]
    partial = _frame(base + [("2026-09-28 08:30", 3590, 3592, 3585, 3586)])     # still forming
    final = _frame(base + [("2026-09-28 08:30", 3590, 3592, 3570, 3575)])       # low 3570p < 3580p stop
    # 08:50:05 wall = feed ~08:30:05: the 08:30 bar is still forming -> NOT consumed
    r = monitor.run_once(str(tmp_path), now=L(8, 50, 5, d=28),
                         bars_fn=lambda s, a, b: partial[(partial.index >= a) & (partial.index <= b)])
    assert r["closed"] == []
    port = json.loads((tmp_path / "portfolio.json").read_text())["positions"]["SHEL.L"]
    assert port["last_check"] == "2026-09-28T08:30:00+01:00"
    # 08:51:05: the completed 08:30 bar breaches -> exit at the stop, timestamped 08:30 (not 08:51)
    r = monitor.run_once(str(tmp_path), now=L(8, 51, 5, d=28),
                         bars_fn=lambda s, a, b: final[(final.index >= a) & (final.index <= b)])
    assert len(r["closed"]) == 1
    sell = json.loads((tmp_path / "trades.json").read_text())[-1]
    assert sell["price"] == pytest.approx(35.80) and sell["timestamp"] == "2026-09-28T08:30:00+01:00"
    assert sell["quote_ts"] == "2026-09-28T08:30:00+01:00" and sell["pnl"] == pytest.approx(18 * (35.80 - 36.045))


def test_lse_gap_through_stop_booked_at_bar_open(tmp_path):
    import monitor
    _lse_pos(tmp_path, 35.80)
    bars = _frame([("2026-09-28 08:%02d" % m, 3604, 3606, 3600, 3603) for m in range(6, 12)]
                  + [("2026-09-28 08:12", 3560, 3565, 3555, 3560),     # opens 3560p through the 3580p stop
                     ("2026-09-28 08:13", 3558, 3561, 3550, 3555)])    # confirms (bad-tick guard)
    r = monitor.run_once(str(tmp_path), now=L(8, 35, 5, d=28),
                         bars_fn=lambda s, a, b: bars[(bars.index >= a) & (bars.index <= b)])
    assert len(r["closed"]) == 1
    sell = json.loads((tmp_path / "trades.json").read_text())[-1]
    assert sell["price"] == pytest.approx(35.60) and sell["timestamp"] == "2026-09-28T08:12:00+01:00"
    assert "gapped" in sell["reasoning"]


def test_lse_not_checked_before_its_bars_are_visible(tmp_path):
    import monitor
    _lse_pos(tmp_path, 35.80)
    calls = []
    r = monitor.run_once(str(tmp_path), now=L(8, 15, 0, d=28),
                         bars_fn=lambda s, a, b: calls.append(s) or _frame([]))
    assert calls == [] and r["action"] == "markets_closed"     # feed time 07:55: LSE not open yet
    assert monitor.check_state("SHEL.L", L(16, 50, 0, d=28))[0] == "open"
    assert monitor.check_state("SHEL.L", L(16, 58, 0, d=28))[0] == "final"
    assert monitor.market_state("SHEL.L", L(16, 50, 0, d=28))[0] == "closed"  # wall clock unchanged
