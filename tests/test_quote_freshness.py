import json

import pytest

import emulator
from quotes import Quote, StaleQuoteError, get_fresh_quote


def test_stale_quote_rejected_even_after_requote(make_emulator, london, tmp_path):
    now = london(8, 22, 21)
    stale = Quote("ISPY.L", 3775.0, london(8, 4, 0), "test")          # the 08:04 print
    e = make_emulator(now, {"ISPY.L": [stale, stale]})
    with pytest.raises(StaleQuoteError):
        e.execute_buy("ISPY.L", 12000, 37.75, 37.3725, 38.6937, "t", 0, 0, 0,
                      ticket_symbol="ISPY", quote=stale)
    assert e.trade_history == [] and e.cash == 20000
    assert "stale_quote" in (tmp_path / "alerts_log.jsonl").read_text()


def test_stale_quote_requoted_and_stored(make_emulator, london):
    now = london(8, 22, 21)
    stale = Quote("ISPY.L", 3775.0, london(8, 4, 0), "test")
    fresh = Quote("ISPY.L", 3753.513916, london(8, 22, 0), "yfinance_1m_bar_start")
    e = make_emulator(now, {"ISPY.L": [fresh]})
    t = e.execute_buy("ISPY.L", 12000, 37.75, 37.1597, 38.4735, "t", 0, 0, 0,
                      ticket_symbol="ISPY", quote=stale)
    assert t.price == pytest.approx(37.53513916)
    assert t.quantity == pytest.approx(12000 / 37.53513916)
    assert t.quote_ts == "2026-09-25T08:22:00+01:00" and t.quote_source == "yfinance_1m_bar_start"
    assert t.isin == "IE00BYPLS672" and t.ticket_symbol == "ISPY"
    assert "fill 37.5351 from fresh quote vs ref 37.7500" in t.reasoning
    pos = e.positions["ISPY.L"]
    assert pos.isin == "IE00BYPLS672" and pos.opened_at.startswith("2026-09-25T08:22:21")


def test_quote_exactly_60s_ok_61s_stale(london):
    q60 = Quote("X", 1, london(8, 21, 21), "t")
    assert q60.is_fresh(london(8, 22, 21))
    q61 = Quote("X", 1, london(8, 21, 20), "t")
    assert not q61.is_fresh(london(8, 22, 21))
    with pytest.raises(StaleQuoteError):
        get_fresh_quote("X", at=london(8, 22, 21), fetch=lambda s: q61, requote_wait_s=0)


def test_sell_requires_fresh_quote(make_emulator, london):
    now = london(8, 22, 21)
    fresh = Quote("ISPY.L", 3753.51, london(8, 22, 0), "t")
    e = make_emulator(now, {"ISPY.L": [fresh, Quote("ISPY.L", 3700, london(8, 0), "t"),
                                       Quote("ISPY.L", 3700, london(8, 0), "t")]})
    e.execute_buy("ISPY.L", 1000, 37.5, 37.1, 38.4, "t", 0, 0, 0)
    with pytest.raises(StaleQuoteError):
        e.execute_sell("ISPY.L", "manual")
    assert "ISPY.L" in e.positions


def test_usd_instrument_books_in_gbp(make_emulator, london):
    now = london(15, 0, 0)
    e = make_emulator(now, {"XOM": Quote("XOM", 160.0, london(14, 59, 50), "t"),
                            "GBPUSD=X": Quote("GBPUSD=X", 1.25, london(14, 59, 0), "t")})
    t = e.execute_buy("XOM", 1000, 128.0, 126.0, 131.0, "t", 0, 0, 0)
    assert t.price == pytest.approx(128.0) and t.isin == "US30233Q1085"


def test_trade_backward_compatible_load(tmp_path):
    old = [{"id": "T00001", "ticker": "ISPY.L", "action": "BUY", "quantity": 1.0, "price": 37.75,
            "value_gbp": 37.75, "timestamp": "2026-09-25T08:22:21", "reasoning": "old",
            "claude_score": 0, "grok_score": 0, "combined_score": 0, "stop_loss": 37.3,
            "take_profit": 38.7, "status": "open", "some_future_field": 1}]
    (tmp_path / "trades.json").write_text(json.dumps(old))
    e = emulator.PaperTradingEmulator(data_dir=str(tmp_path))
    assert e.trade_history[0].quote_ts == "" and e.trade_history[0].quote_source == ""


def test_real_data_trades_load_with_quote_fields():
    e = emulator.PaperTradingEmulator()
    for t in e.trade_history:
        assert hasattr(t, "quote_ts")


def test_halted_day_blocks_new_buys(make_emulator, london, tmp_path):
    (tmp_path / "live_status.json").write_text(json.dumps({"halted": True, "halted_date": "2026-09-25"}))
    e = make_emulator(london(15, 0), {"ISPY.L": Quote("ISPY.L", 3700, london(14, 59, 30), "t")})
    assert e.execute_buy("ISPY.L", 1000, 37, 36.6, 38, "t", 0, 0, 0) is None
    e2 = make_emulator(london(9, 0, day=28), {"ISPY.L": Quote("ISPY.L", 3700, london(8, 59, 30, day=28), "t")})
    assert e2.execute_buy("ISPY.L", 1000, 37, 36.6, 38, "t", 0, 0, 0) is not None
