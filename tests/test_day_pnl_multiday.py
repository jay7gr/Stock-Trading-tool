"""SOP v2.3 day P&L: realised today + change in open MTM since the PREVIOUS CLOSE
(entry for positions opened today); monitor stamps prev closes and the -£200 halt
uses the day number, not P&L since entry."""
import json
from datetime import date, datetime
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import emulator
import monitor
import pnl
from quotes import LONDON, Quote

MON, TUE = date(2026, 9, 28), date(2026, 9, 29)


def L(d, h, m, s=0):
    return datetime(2026, 9, d, h, m, s, tzinfo=LONDON)


def P(**kw):
    base = dict(quantity=10, avg_entry_price=10.0, current_price=10.0,
                opened_at="2026-09-28T09:00:00+01:00", prev_close=0.0, prev_close_date="",
                prev_close_source="")
    base.update(kw)
    return NS(**base)


def test_opened_today_measures_from_entry():
    p = P(current_price=9.5)
    assert pnl.day_ref(p, MON) == (10.0, "entry")
    assert pnl.day_pnl([], [p], MON) == pytest.approx(-5.0)


def test_carried_position_measures_from_previous_close():
    # bought Mon at £10, closed Mon at £8 (-£20 on Monday), Tue mark £8.50
    p = P(current_price=8.5, prev_close=8.0, prev_close_date="2026-09-29", prev_close_source="x")
    assert pnl.day_ref(p, TUE) == (8.0, "x")
    assert pnl.day_pnl([], [p], TUE) == pytest.approx(+5.0)       # not -15 (since entry)
    assert pnl.open_mtm([p]) == pytest.approx(-15.0)                # since-entry kept for info


def test_carried_position_without_prev_close_falls_back_to_entry_and_says_so():
    p = P(current_price=8.5, prev_close=8.0, prev_close_date="2026-09-28")   # stale date
    assert pnl.day_ref(p, TUE) == (10.0, "entry_fallback_no_prev_close")


def test_per_market_prev_close_gbp_mixed_book():
    shel = P(quantity=18, avg_entry_price=36.11, current_price=36.50,
             prev_close=36.00, prev_close_date="2026-09-29")                  # LSE close, GBP
    tsm = P(quantity=1, avg_entry_price=340.0, current_price=345.0,
            prev_close=342.0, prev_close_date="2026-09-29")                   # NYSE close in GBP
    new = P(quantity=5, avg_entry_price=20.0, current_price=19.0,
            opened_at="2026-09-29T08:30:00+01:00")                          # opened today
    assert pnl.day_pnl([], [shel, tsm, new], TUE) == pytest.approx(18 * 0.5 + 3.0 - 5.0)


def test_realised_today_uses_trade_day_pnl_when_present():
    trades = [NS(action="SELL", pnl=-30.0, day_pnl=+4.0, timestamp="2026-09-29T10:00:00+01:00"),
              NS(action="SELL", pnl=-7.0, timestamp="2026-09-29T11:00:00+01:00"),        # legacy row
              NS(action="SELL", pnl=-9.0, day_pnl=None, timestamp="2026-09-29T12:00:00+01:00")]
    assert pnl.realised_on(trades, TUE) == pytest.approx(4.0 - 7.0 - 9.0)


def _seed(tmp_path, positions, status_date="2026-09-28"):
    (tmp_path / "portfolio.json").write_text(json.dumps({"cash": 10000, "trade_counter": 1,
                                                          "positions": positions}))
    (tmp_path / "trades.json").write_text("[]")
    (tmp_path / "live_status.json").write_text(json.dumps({"status_date": status_date, "halted": False,
                                                           "as_of": f"{status_date}T21:10:00+01:00"}))


def test_monitor_stamps_prev_close_and_sell_books_day_pnl_from_it(tmp_path):
    # VUSA bought Mon at £110.50, last Mon mark £109.00 (bar ending 16:30) -> Tue ref £109.00
    _seed(tmp_path, {"VUSA.L": {"ticker": "VUSA.L", "quantity": 10, "avg_entry_price": 110.5,
                                "current_price": 109.0, "stop_loss": 108.0, "take_profit": 115.0,
                                "opened_at": "2026-09-28T09:00:00+01:00",
                                "last_check": "2026-09-28T16:30:00+01:00"}})
    r = monitor.run_once(str(tmp_path), now=L(29, 0, 1, 5))          # LSE shut: no network
    assert r["action"] == "markets_closed"
    p = json.loads((tmp_path / "portfolio.json").read_text())["positions"]["VUSA.L"]
    assert p["prev_close"] == 109.0 and p["prev_close_date"] == "2026-09-29"
    assert p["prev_close_source"].startswith("last_mark_before_midnight")
    # Tue 08:01 bar gaps through the £108 stop at £107.80 -> exit at the open
    bars = pd.DataFrame([[107.8, 107.9, 107.7, 107.85]], columns=["Open", "High", "Low", "Close"],
                        index=pd.DatetimeIndex([pd.Timestamp("2026-09-29 08:01", tz=LONDON)]))
    r = monitor.run_once(str(tmp_path), now=L(29, 8, 2, 5),
                         bars_fn=lambda s, a, b: bars[(bars.index >= a) & (bars.index <= b)])
    sell = json.loads((tmp_path / "trades.json").read_text())[-1]
    assert sell["price"] == pytest.approx(107.8)
    assert sell["pnl"] == pytest.approx(10 * (107.8 - 110.5))         # since entry: -£27
    assert sell["day_pnl"] == pytest.approx(10 * (107.8 - 109.0))     # today: -£12
    ls = json.loads((tmp_path / "live_status.json").read_text())
    assert ls["day_pnl"] == pytest.approx(-12.0) and not ls["halted"]


def test_halt_uses_day_change_not_since_entry(tmp_path):
    """Carried leg is -£250 since entry but only -£50 today -> no halt (v2.2 would halt).
    A further -£160 today -> -£210 -> halt."""
    _seed(tmp_path, {"VUSA.L": {"ticker": "VUSA.L", "quantity": 100, "avg_entry_price": 112.5,
                                "current_price": 110.5, "stop_loss": 100.0, "take_profit": 130.0,
                                "opened_at": "2026-09-28T09:00:00+01:00",
                                "last_check": "2026-09-28T16:30:00+01:00"}})
    monitor.run_once(str(tmp_path), now=L(29, 0, 1, 5))                 # stamps prev close 110.5
    mk = lambda c: pd.DataFrame([[c, c, c, c]], columns=["Open", "High", "Low", "Close"],
                                index=pd.DatetimeIndex([pd.Timestamp("2026-09-29 09:00", tz=LONDON)]))
    b1 = mk(110.0)
    r = monitor.run_once(str(tmp_path), now=L(29, 9, 1, 5),
                         bars_fn=lambda s, a, b: b1[(b1.index >= a) & (b1.index <= b)])
    assert r["day_pnl"] == pytest.approx(-50.0) and r["action"] != "halt_flatten_all"
    ls = json.loads((tmp_path / "live_status.json").read_text())
    row = ls["open_positions"][0]
    assert row["day_ref_gbp"] == 110.5 and row["day_change_gbp"] == pytest.approx(-50.0)
    b2 = pd.DataFrame([[108.4, 108.4, 108.4, 108.4]], columns=["Open", "High", "Low", "Close"],
                      index=pd.DatetimeIndex([pd.Timestamp("2026-09-29 09:01", tz=LONDON)]))
    q = {"VUSA.L": Quote("VUSA.L", 108.4, L(29, 9, 2, 0), "t")}
    r = monitor.run_once(str(tmp_path), now=L(29, 9, 2, 5),
                         bars_fn=lambda s, a, b: b2[(b2.index >= a) & (b2.index <= b)], quote_fn=q.get)
    assert r["action"] == "halt_flatten_all" and r["day_pnl"] == pytest.approx(-210.0)


def test_opened_today_position_is_not_stamped(tmp_path):
    _seed(tmp_path, {"VUSA.L": {"ticker": "VUSA.L", "quantity": 1, "avg_entry_price": 110.0,
                                "current_price": 110.0, "stop_loss": 100, "take_profit": 120,
                                "opened_at": "2026-09-29T08:30:00+01:00",
                                "last_check": "2026-09-29T08:31:00+01:00"}})
    assert monitor.stamp_prev_close(str(tmp_path), L(29, 8, 32)) == []


def test_top_up_of_carried_position_blends_day_ref(make_emulator, tmp_path):
    now = L(29, 10, 0, 30)
    e = make_emulator(now, {"VUSA.L": Quote("VUSA.L", 111.0, L(29, 10, 0, 10), "t")})
    e.positions["VUSA.L"] = emulator.Position(
        ticker="VUSA.L", quantity=10, avg_entry_price=110.5, current_price=109.0, value_gbp=1090,
        unrealised_pnl=0, unrealised_pnl_pct=0, stop_loss=108.0, take_profit=115.0,
        opened_at="2026-09-28T09:00:00+01:00", prev_close=109.0, prev_close_date="2026-09-29",
        prev_close_source="t")
    e.execute_buy("VUSA", 10 * 111.0, 111.0, 108.0, 115.0, "t", 0, 0, 0)
    p = e.positions["VUSA.L"]
    assert p.quantity == pytest.approx(20) and p.prev_close == pytest.approx(110.0)
    p.current_price = 111.0
    assert pnl.day_pnl([], [p], TUE) == pytest.approx(10 * 2.0 + 10 * 0.0)
    # persisted
    e2 = emulator.PaperTradingEmulator(data_dir=str(tmp_path))
    assert e2.positions["VUSA.L"].prev_close == pytest.approx(110.0)
    assert e2.positions["VUSA.L"].prev_close_date == "2026-09-29"
