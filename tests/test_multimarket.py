"""Risk SOP v2.2: market-aware monitor, Asia position, one aggregate -£200 halt in GBP."""
import json
from datetime import datetime

import pandas as pd
import pytest

import instruments
import monitor
from instruments import Instrument
from quotes import LONDON, Quote

TOYOTA = Instrument(symbol="7203", name="Toyota Motor (test-only entry)", isin="JP3633400001",
                    exchange="TSE", listing="TSE:JPY", currency="JPY", quote_unit="JPY",
                    yf_symbol="7203.T", freetrade_ticker="", isin_verified=False,
                    freetrade_verified=False)


@pytest.fixture
def toyota():
    instruments.register(TOYOTA)
    yield TOYOTA
    instruments.unregister("7203")


def L(d, h, m, s=0):
    return datetime(2026, 9, d, h, m, s, tzinfo=LONDON)


def seed(tmp_path, positions, trades=None, halted=False):
    port = {"cash": 10000, "trade_counter": len(trades or []), "positions": {}}
    for p in positions:
        port["positions"][p["ticker"]] = {"current_price": p["entry"], "avg_entry_price": p["entry"],
                                          **{k: v for k, v in p.items() if k != "entry"}}
    (tmp_path / "portfolio.json").write_text(json.dumps(port))
    (tmp_path / "trades.json").write_text(json.dumps(trades or []))
    (tmp_path / "live_status.json").write_text(json.dumps({"as_of": "2026-09-28T00:00:00+01:00",
                                                           "status_date": "2026-09-28",
                                                           "halted": halted}))


def bars(rows):
    idx = pd.DatetimeIndex([pd.Timestamp(t, tz=LONDON) for t, *_ in rows])
    return pd.DataFrame([r[1:] for r in rows], columns=["Open", "High", "Low", "Close"], index=idx)


def make_bars_fn(table, calls):
    def f(sym, since, now):
        calls.append(sym)
        b = table[sym]
        return b[(b.index >= since) & (b.index <= now)]
    return f


def test_asia_position_checked_only_while_tse_open(tmp_path, toyota):
    # 100 sh bought at ¥3,000 = £15.00 @ GBPJPY 200
    seed(tmp_path, [{"ticker": "7203.T", "quantity": 100, "entry": 15.0, "stop_loss": 14.7,
                     "take_profit": 15.6, "opened_at": "2026-09-28T01:00:00+01:00"}])
    table = {"7203.T": bars([("2026-09-28 01:00", 3000, 3005, 2995, 3000),
                             ("2026-09-28 01:01", 3000, 3002, 2955, 2960),
                             ("2026-09-28 01:02", 2990, 2992, 2930, 2935)])}   # low ¥2930 = £14.65
    calls = []
    fx = lambda s: Quote(s, 200.0, L(28, 1, 2), "t") if s == "GBPJPY=X" else None
    # 00:30 London (08:30 JST): TSE shut -> idle, no network
    r = monitor.run_once(str(tmp_path), now=L(28, 0, 30), bars_fn=make_bars_fn(table, calls), quote_fn=fx)
    assert r["action"] == "markets_closed" and calls == []
    # 01:02:05 London (09:02 JST): open, checked, no trigger yet
    r = monitor.run_once(str(tmp_path), now=L(28, 1, 2, 5), bars_fn=make_bars_fn(table, calls), quote_fn=fx)
    assert r["checked"] == ["7203.T"] and r["closed"] == []
    # 01:03:05: the 01:02 bar broke the ¥2940 (=£14.70) stop -> exit at stop, in GBP
    r = monitor.run_once(str(tmp_path), now=L(28, 1, 3, 5), bars_fn=make_bars_fn(table, calls), quote_fn=fx)
    assert len(r["closed"]) == 1
    sell = json.loads((tmp_path / "trades.json").read_text())[-1]
    assert sell["price"] == pytest.approx(14.7) and sell["pnl"] == pytest.approx(-30.0)
    assert sell["timestamp"].startswith("2026-09-28T01:02")
    ls = json.loads((tmp_path / "live_status.json").read_text())
    assert ls["day_pnl"] == pytest.approx(-30.0) and not ls["halted"]


def test_final_check_after_close_runs_once(tmp_path):
    seed(tmp_path, [{"ticker": "ISPY.L", "quantity": 10, "entry": 37.0, "stop_loss": 30.0,
                     "take_profit": 45.0, "opened_at": "2026-09-28T09:00:00+01:00"}])
    table = {"ISPY.L": bars([("2026-09-28 16:34", 3700, 3701, 3699, 3700)])}
    calls = []
    fn = make_bars_fn(table, calls)
    # LSE checks are shifted by the 20-min Yahoo delay: final window 16:55-17:05 London
    monitor.run_once(str(tmp_path), now=L(28, 16, 57), bars_fn=fn)   # final window
    monitor.run_once(str(tmp_path), now=L(28, 16, 58), bars_fn=fn)   # already done
    r = monitor.run_once(str(tmp_path), now=L(28, 17, 20), bars_fn=fn)
    assert calls == ["ISPY.L"] and r["action"] == "markets_closed"


def test_aggregate_halt_across_lse_and_nyse(tmp_path):
    """LSE stop realises -£120, NYSE position is -£100 MTM -> -£220 <= -£200:
    one halt, NYSE flattened on a fresh quote."""
    seed(tmp_path, [
        {"ticker": "ISPY.L", "quantity": 400, "entry": 37.5, "stop_loss": 37.2, "take_profit": 39.0,
         "opened_at": "2026-09-28T09:00:00+01:00"},
        {"ticker": "XOM", "quantity": 20, "entry": 130.0, "stop_loss": 120.0, "take_profit": 140.0,
         "opened_at": "2026-09-28T14:31:00+01:00"},
    ])
    table = {"ISPY.L": bars([("2026-09-28 14:40", 3725, 3726, 3715, 3716)]),        # stop 37.20 hit (20-min delayed .L bar)
             "XOM": bars([("2026-09-28 15:00", 156.0, 156.2, 155.9, 156.25)])}      # $156.25/1.25 = £125
    q = {"GBPUSD=X": Quote("GBPUSD=X", 1.25, L(28, 15, 0, 30), "t"),
         "XOM": Quote("XOM", 156.25, L(28, 15, 1, 0), "t")}
    calls = []
    r = monitor.run_once(str(tmp_path), now=L(28, 15, 1, 5), bars_fn=make_bars_fn(table, calls),
                         quote_fn=lambda s: q.get(s))
    assert sorted(calls) == ["ISPY.L", "XOM"]
    assert r["action"] == "halt_flatten_all"
    assert r["day_pnl"] == pytest.approx(-120.0 - 100.0, abs=0.01)
    ls = json.loads((tmp_path / "live_status.json").read_text())
    assert ls["halted"] and ls["open_positions"] == [] and ls["remaining_day_risk_gbp"] == 0
    types = [json.loads(l)["type"] for l in (tmp_path / "alerts_log.jsonl").read_text().splitlines()]
    assert types.count("halt") == 1 and "stop" in types and "exit" in types


def test_aggregate_halt_queues_closed_market_until_open(tmp_path, toyota):
    """NYSE stop at 20:00 London realises -£150; TSE position (market shut) is -£60 MTM ->
    -£210: halt; Toyota is queued and sold at the next TSE open."""
    seed(tmp_path, [
        {"ticker": "XOM", "quantity": 30, "entry": 130.0, "stop_loss": 125.0, "take_profit": 140.0,
         "opened_at": "2026-09-28T14:31:00+01:00"},
        {"ticker": "7203.T", "quantity": 100, "entry": 15.0, "stop_loss": 13.0, "take_profit": 17.0,
         "opened_at": "2026-09-28T01:00:00+01:00", "current_price": 14.4},
    ])
    table = {"XOM": bars([("2026-09-28 20:00", 156.0, 156.1, 155.0, 155.5)]),       # $156.25 stop? see below
             "7203.T": bars([("2026-09-29 01:00", 2880, 2885, 2875, 2880)])}
    # XOM stop £125 = $156.25 at 1.25 -> 20:00 bar opened $156.00 below it -> exit $156.00 = £124.80
    q = {"GBPUSD=X": Quote("GBPUSD=X", 1.25, L(28, 20, 0, 30), "t")}
    calls = []
    r = monitor.run_once(str(tmp_path), now=L(28, 20, 1, 5), bars_fn=make_bars_fn(table, calls),
                         quote_fn=lambda s: q.get(s))
    assert calls == ["XOM"]                                   # TSE closed: no network for 7203.T
    assert r["action"] == "halt_flatten_all" and r["pending_flatten"] == ["7203.T"]
    assert r["day_pnl"] == pytest.approx(30 * (124.8 - 130.0) + 100 * (14.4 - 15.0), abs=0.01)
    ls = json.loads((tmp_path / "live_status.json").read_text())
    assert ls["halted"] and ls["pending_flatten"] == ["7203.T"]
    # next TSE open (Tue 29 Sep 09:00 JST = 01:00 London): queued flatten executes
    q2 = {"7203.T": Quote("7203.T", 2880.0, L(29, 1, 0, 50), "t"),
          "GBPJPY=X": Quote("GBPJPY=X", 200.0, L(29, 1, 0, 30), "t")}
    r = monitor.run_once(str(tmp_path), now=L(29, 1, 1, 5), bars_fn=make_bars_fn(table, calls),
                         quote_fn=lambda s: q2.get(s))
    assert r["pending_flatten"] == [] and len(r["closed"]) == 1
    port = json.loads((tmp_path / "portfolio.json").read_text())
    assert port["positions"] == {}
    sell = json.loads((tmp_path / "trades.json").read_text())[-1]
    assert sell["ticker"] == "7203.T" and sell["price"] == pytest.approx(14.4)
