"""25 Sep 2026 ISPY replay on real 1m bars (tests/fixtures/ISPY.L_1m_2026-09-25.csv)."""
import json
from datetime import datetime

import pytest

import monitor
from quotes import LONDON, Quote, load_day_bars, to_london


def _loader(sym, day):
    return load_day_bars(sym, day, allow_fetch=False)


def test_fixture_is_real_day(ispy_bars):
    assert str(ispy_bars.index[0].date()) == "2026-09-25"
    b = ispy_bars[ispy_bars.index == "2026-09-25 09:10:00+01:00"].iloc[0]
    assert b.Low == 3728.0
    # 3775p printed only once, in the 08:04 bar
    hits = ispy_bars[(ispy_bars.Low <= 3775) & (ispy_bars.High >= 3775)]
    assert [t.strftime("%H:%M") for t in hits.index] == ["08:04"]
    b822 = ispy_bars[ispy_bars.index == "2026-09-25 08:22:00+01:00"].iloc[0]
    assert b822.Open == b822.Close == pytest.approx(3753.513916, abs=1e-5)


def test_replay_flags_ispy_stop_by_0915():
    spec = monitor.parse_position("ISPY.L:37.75:37.3725:38.6937:2026-09-25T08:22")
    evs = monitor.replay_day("2026-09-25", [spec], _loader)
    stop = next(e for e in evs if e["type"] == "stop")
    assert to_london(stop["event_ts"]) == datetime(2026, 9, 25, 9, 10, tzinfo=LONDON)
    assert to_london(stop["detected_ts"]) <= datetime(2026, 9, 25, 9, 15, tzinfo=LONDON)
    assert stop["details"]["price"] == pytest.approx(37.28)  # gapped below 37.3725 -> bar Open
    assert stop["notified"] is False


def test_replay_restated_position_skips_1428_bad_tick_stops_at_1434():
    spec = monitor.parse_position("ISPY.L:37.5351:37.1597:38.4735:2026-09-25T08:22:21")
    evs = monitor.replay_day("2026-09-25", [spec], _loader)
    stop = next(e for e in evs if e["type"] == "stop")
    assert to_london(stop["event_ts"]) == datetime(2026, 9, 25, 14, 34, tzinfo=LONDON)
    skips = [e for e in evs if e["type"] == "skip"]
    assert [e["event_ts"] for e in skips] == ["2026-09-25T14:28:00+01:00"]
    assert skips[0]["details"]["bar"]["Low"] == 3665.0
    assert stop["details"]["gapped"] and stop["details"]["price"] == pytest.approx(36.95)


def test_cli_replay(capsys):
    rc = monitor.main(["--replay", "2026-09-25", "--no-fetch",
                       "--position", "ISPY.L:37.75:37.3725:38.6937:2026-09-25T08:22"])
    out = capsys.readouterr().out
    assert rc == 0 and "REPLAY STOP ISPY.L" in out and "flagged at 2026-09-25T09:11:00+01:00" in out


def _seed_position(tmp_path, qty, entry, stop, target, opened):
    (tmp_path / "portfolio.json").write_text(json.dumps({
        "cash": 20000 - qty * entry, "trade_counter": 1,
        "positions": {"ISPY.L": {"ticker": "ISPY.L", "quantity": qty, "avg_entry_price": entry,
                                 "current_price": entry, "stop_loss": stop, "take_profit": target,
                                 "opened_at": opened}}}))
    (tmp_path / "trades.json").write_text(json.dumps([{
        "id": "T00001", "ticker": "ISPY.L", "action": "BUY", "quantity": qty, "price": entry,
        "value_gbp": qty * entry, "timestamp": opened, "reasoning": "seed", "claude_score": 0,
        "grok_score": 0, "combined_score": 0, "stop_loss": stop, "take_profit": target}]))
    (tmp_path / "live_status.json").write_text(json.dumps({"as_of": opened, "halted": False}))


def _bars_fn(ispy_bars):
    def f(sym, since, now):
        assert sym == "ISPY.L"
        return ispy_bars[(ispy_bars.index >= since) & (ispy_bars.index <= now)]
    return f


def test_monitor_minute_loop_books_stop(tmp_path, ispy_bars):
    qty = 12000 / 37.75
    _seed_position(tmp_path, qty, 37.75, 37.3725, 38.6937, "2026-09-25T08:22:21")
    fired = None
    t = datetime(2026, 9, 25, 8, 23, 5, tzinfo=LONDON)
    while t.hour < 10:
        res = monitor.run_once(str(tmp_path), now=t, bars_fn=_bars_fn(ispy_bars))
        if res.get("closed"):
            fired = t
            break
        t = t.replace(minute=(t.minute + 1) % 60, hour=t.hour + (1 if t.minute == 59 else 0))
    assert fired is not None and fired <= datetime(2026, 9, 25, 9, 15, tzinfo=LONDON)
    trades = json.loads((tmp_path / "trades.json").read_text())
    sell = trades[-1]
    assert sell["action"] == "SELL" and sell["price"] == pytest.approx(37.28)
    assert sell["timestamp"].startswith("2026-09-25T09:10") and sell["quote_source"] == "yfinance_1m_bar"
    assert trades[0]["status"] == "stopped_out"
    ls = json.loads((tmp_path / "live_status.json").read_text())
    assert ls["day_pnl"] == pytest.approx(-149.40, abs=0.01)
    assert ls["remaining_day_risk_gbp"] == pytest.approx(50.60, abs=0.01)
    assert ls["halted"] is False and ls["open_positions"] == []
    alerts = [json.loads(l) for l in (tmp_path / "alerts_log.jsonl").read_text().splitlines()]
    st = [a for a in alerts if a["type"] == "stop"]
    assert len(st) == 1 and st[0]["event_ts"].startswith("2026-09-25T09:10")
    assert set(st[0]) == {"event_ts", "detected_ts", "type", "ticker", "details", "notified"}
    port = json.loads((tmp_path / "portfolio.json").read_text())
    assert port["positions"] == {} and port["cash"] == pytest.approx(19850.60, abs=0.01)


def test_monitor_halt_at_minus_200_flattens_all(tmp_path, ispy_bars):
    # oversized ISPY (700 sh @ 37.75) stops at 09:10 -> -£329 realised -> halt,
    # and a second open position (IESU) gets flattened on a fresh quote.
    _seed_position(tmp_path, 700, 37.75, 37.3725, 38.6937, "2026-09-25T08:22:21")
    port = json.loads((tmp_path / "portfolio.json").read_text())
    port["positions"]["IESU.L"] = {"ticker": "IESU.L", "quantity": 100, "avg_entry_price": 10.0,
                                   "current_price": 10.0, "stop_loss": 9.0, "take_profit": 11.0,
                                   "opened_at": "2026-09-25T08:30:00"}
    (tmp_path / "portfolio.json").write_text(json.dumps(port))
    import pandas as pd
    iesu = pd.DataFrame([(1000, 1001, 999, 1000)], columns=["Open", "High", "Low", "Close"],
                        index=pd.DatetimeIndex([pd.Timestamp("2026-09-25 09:10", tz=LONDON)]))

    def bars_fn(sym, since, now):
        src = ispy_bars if sym == "ISPY.L" else iesu
        return src[(src.index >= since) & (src.index <= now)]
    now = datetime(2026, 9, 25, 9, 11, 5, tzinfo=LONDON)
    quote_fn = lambda s: Quote("IESU.L", 1002.0, datetime(2026, 9, 25, 9, 11, 0, tzinfo=LONDON), "test")
    res = monitor.run_once(str(tmp_path), now=now, bars_fn=bars_fn, quote_fn=quote_fn)
    assert res["action"] == "halt_flatten_all"
    assert res["day_pnl"] == pytest.approx(-700 * 0.47 + 100 * 0.02, abs=0.01)
    ls = json.loads((tmp_path / "live_status.json").read_text())
    assert ls["halted"] is True and ls["halted_date"] == "2026-09-25"
    assert ls["remaining_day_risk_gbp"] == 0 and ls["open_positions"] == []
    alerts = [json.loads(l)["type"] for l in (tmp_path / "alerts_log.jsonl").read_text().splitlines()]
    assert alerts.count("stop") == 1 and "exit" in alerts and "halt" in alerts
    trades = json.loads((tmp_path / "trades.json").read_text())
    iesu_sell = [t for t in trades if t["ticker"] == "IESU.L" and t["action"] == "SELL"][0]
    assert iesu_sell["price"] == pytest.approx(10.02) and iesu_sell["quote_ts"].startswith("2026-09-25T09:11:00")


def test_monitor_flat_is_idle(tmp_path, monkeypatch):
    (tmp_path / "portfolio.json").write_text(json.dumps({"cash": 1, "trade_counter": 0, "positions": {}}))
    def boom(*a, **k):
        raise AssertionError("network used while flat")
    res = monitor.run_once(str(tmp_path), now=datetime(2026, 9, 28, 8, 1, tzinfo=LONDON),
                           bars_fn=boom, quote_fn=boom)
    assert res["action"] == "flat"


def test_market_state_for_lse_position():
    st, ex, _ = monitor.market_state("ISPY.L", datetime(2026, 9, 28, 8, 0, tzinfo=LONDON))
    assert st == "open" and ex.code == "LSE"
    assert monitor.market_state("ISPY.L", datetime(2026, 9, 28, 16, 40, tzinfo=LONDON))[0] == "final"
    assert monitor.market_state("ISPY.L", datetime(2026, 9, 28, 21, 0, tzinfo=LONDON))[0] == "closed"
    assert monitor.market_state("ISPY.L", datetime(2026, 9, 26, 12, 0, tzinfo=LONDON))[0] == "closed"  # Sat
