"""Monday 28 Sep 2026 shortlist: registry (TSM, VUSA, SHEL, WMT, MSFT), GBX pence
handling end-to-end, USD legs in GBP, Monday coverage, Friday dry runs on real
1m bars, native-currency levels and the fill-time level guard."""
import json
from datetime import datetime

import pandas as pd
import pytest

import emulator
import instruments
import market_hours
import monitor
import pnl
from emulator import InvalidLevelsError
from instruments import InstrumentMismatchError, resolve, assert_fill_matches_ticket
from quotes import LONDON, Quote, load_day_bars

FX = 1.3253


def L(d, h, m, s=0):
    return datetime(2026, 9, d, h, m, s, tzinfo=LONDON)


def fixture_bars(sym, since, now):
    b = load_day_bars(sym, "2026-09-25", allow_fetch=False)
    return b[(b.index >= since) & (b.index <= now)]


# ─── registry ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("sym,isin,exch,ccy,unit,yf", [
    ("TSM", "US8740391003", "NYSE", "USD", "USD", "TSM"),
    ("VUSA", "IE00B3XXRP09", "LSE", "GBP", "GBP", "VUSA.L"),
    ("SHEL", "GB00BP6MXD84", "LSE", "GBP", "GBX", "SHEL.L"),
    ("WMT", "US9311421039", "NASDAQ", "USD", "USD", "WMT"),
    ("MSFT", "US5949181045", "NASDAQ", "USD", "USD", "MSFT"),
])
def test_shortlist_registered(sym, isin, exch, ccy, unit, yf):
    i = resolve(sym)
    assert (i.isin, i.exchange, i.currency, i.quote_unit, i.yf_symbol) == (isin, exch, ccy, unit, yf)
    assert i.freetrade_ticker == sym and i.isin_verified and i.freetrade_verified and i.sources
    assert instruments.exchange_of(i).code == exch
    assert resolve(yf) is i


def test_shel_ticket_resolves_to_lse_line_only():
    for s in ("SHEL", "shel", " SHEL ", "SHEL.L"):
        i = resolve(s)
        assert i.yf_symbol == "SHEL.L" and i.listing == "LSE:GBX" and i.isin == "GB00BP6MXD84"
    assert resolve("SHEL").purchase_tax_pct == pytest.approx(0.005)


def test_shel_fill_refuses_yahoo_nyse_adr_quote(make_emulator):
    now = L(28, 10, 0, 30)
    e = make_emulator(now)
    adr = Quote("SHEL", 95.78, L(28, 10, 0, 10), "t")    # Yahoo SHEL = NYSE ADR in USD
    with pytest.raises(InstrumentMismatchError, match="SHEL.L"):
        e.execute_buy("SHEL", 650, 36.11, 34.7739, 38.7822, "t", 0, 0, 0, quote=adr)
    assert e.positions == {}


def test_wmt_is_nasdaq_so_an_nyse_ticket_listing_is_refused():
    assert assert_fill_matches_ticket("WMT", "WMT", "US9311421039", "NASDAQ:USD", "USD").exchange == "NASDAQ"
    with pytest.raises(InstrumentMismatchError, match="listing"):
        assert_fill_matches_ticket("WMT", "WMT", ticket_listing="NYSE:USD")


# ─── Monday coverage (LSE / NYSE / NASDAQ) ────────────────────────────

@pytest.mark.parametrize("sym,before,first,last,after", [
    ("SHEL", (7, 59), (8, 0), (16, 35), (16, 36)),
    ("VUSA", (7, 59), (8, 0), (16, 35), (16, 36)),
    ("TSM", (14, 29), (14, 30), (21, 0), (21, 1)),
    ("WMT", (14, 29), (14, 30), (21, 0), (21, 1)),
    ("MSFT", (14, 29), (14, 30), (21, 0), (21, 1)),
])
def test_monday_coverage(tmp_path, sym, before, first, last, after):
    def cov(h, m):
        at = L(28, h, m)
        (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": at.isoformat(), "pid": 1}))
        return market_hours.coverage(sym, at=at, data_dir=str(tmp_path))
    assert not cov(*before)["fill_allowed"]
    assert cov(*first)["fill_allowed"] and cov(*last)["fill_allowed"]
    assert not cov(*after)["fill_allowed"]
    # stale heartbeat -> refused even when open
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": L(28, first[0] - 1, 0).isoformat()}))
    assert not market_hours.coverage(sym, at=L(28, first[0], first[1] + 10), data_dir=str(tmp_path))["fill_allowed"]


# ─── GBX: SHEL pence end-to-end ───────────────────────────────────────

def test_shel_gbx_fill_stop_pnl_status_all_in_pounds(tmp_path):
    """Friday bars: fill on a 3605p quote -> £36.05; stop £35.85 hit by the 13:47
    bar (Low 3581p) -> exit £35.85; P&L 18 x -0.20 = -£3.60; live_status in £."""
    now = L(25, 10, 1, 5)
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": now.isoformat()}))
    q = {"SHEL.L": Quote("SHEL.L", 3605.0, L(25, 10, 0, 59), "t")}
    e = emulator.PaperTradingEmulator(data_dir=str(tmp_path), quote_fn=q.get, bars_fn=fixture_bars,
                                      now_fn=lambda: now)
    e.requote_wait_s = 0
    t = e.execute_buy("SHEL", 18 * 36.05, 36.05, 35.85, 38.7822, "t", 0, 0, 0)
    assert t.price == pytest.approx(36.05) and t.quantity == pytest.approx(18)
    assert t.value_gbp == pytest.approx(648.90)
    closed = e.check_stops_and_targets(now=L(25, 16, 40))
    assert len(closed) == 1
    c = closed[0]
    assert c.price == pytest.approx(35.85) and c.pnl == pytest.approx(-3.60)
    assert c.timestamp.startswith("2026-09-25T13:47")
    assert pnl.day_pnl(e.trade_history, e.positions.values(), L(25, 12, 0).date()) == pytest.approx(-3.60)


def test_shel_mark_and_dashboard_rows_in_pounds(tmp_path):
    now = L(25, 10, 1, 5)
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": now.isoformat()}))
    q = {"SHEL.L": Quote("SHEL.L", 3605.0, L(25, 10, 0, 59), "t")}
    e = emulator.PaperTradingEmulator(data_dir=str(tmp_path), quote_fn=q.get, bars_fn=fixture_bars,
                                      now_fn=lambda: now)
    e.requote_wait_s = 0
    e.execute_buy("SHEL", 18 * 36.05, 36.05, 34.7739, 38.7822, "t", 0, 0, 0)
    assert e.check_stops_and_targets(now=L(25, 16, 40)) == []
    p = e.positions["SHEL.L"]
    assert p.current_price == pytest.approx(36.14)          # 16:29 close 3614p
    assert p.unrealised_pnl == pytest.approx(18 * 0.09)
    ls = monitor.write_status(e, str(tmp_path), L(25, 16, 40), [], "t", "t")
    row = ls["open_positions"][0]
    assert row["mark_gbp"] == pytest.approx(36.14) and row["stop_gbp"] == pytest.approx(34.7739)
    assert row["value_gbp"] == pytest.approx(650.52) and ls["day_pnl"] == pytest.approx(1.62)
    assert pnl.fmt_gbp(p.current_price) == "£36.14"
    # the mark refresh path (dashboard get_summary) converts pence too
    assert e._book_price("SHEL.L", 3614.0) == pytest.approx(36.14)


def test_pence_levels_on_a_gbx_line_are_refused(make_emulator):
    now = L(28, 10, 0, 30)
    e = make_emulator(now, {"SHEL.L": Quote("SHEL.L", 3611.0, L(28, 10, 0, 10), "t")})
    with pytest.raises(InvalidLevelsError, match="pence"):
        e.execute_buy("SHEL", 650, 36.11, 3477.39, 3878.22, "t", 0, 0, 0)
    assert e.positions == {} and e.trade_history == []


def test_gbx_and_usd_legs_share_one_minus_200_halt(tmp_path):
    """SHEL (GBX) stop realises -£120 and TSM (USD levels) is -£90 MTM -> -£210:
    one halt, TSM flattened; all numbers in GBP."""
    port = {"cash": 10000, "trade_counter": 0, "positions": {
        "SHEL.L": {"ticker": "SHEL.L", "quantity": 400, "avg_entry_price": 36.00, "current_price": 36.00,
                   "stop_loss": 35.70, "take_profit": 37.0, "opened_at": "2026-09-28T09:00:00+01:00"},
        "TSM": {"ticker": "TSM", "quantity": 10, "avg_entry_price": 340.0, "current_price": 340.0,
                "stop_loss": 400.0, "take_profit": 500.0, "opened_at": "2026-09-28T14:31:00+01:00",
                "level_ccy": "USD", "fx_ref": 1.25},
    }}
    (tmp_path / "portfolio.json").write_text(json.dumps(port))
    (tmp_path / "trades.json").write_text("[]")
    (tmp_path / "live_status.json").write_text(json.dumps({"status_date": "2026-09-28", "halted": False,
                                                           "as_of": "2026-09-28T00:01:00+01:00"}))
    idx = lambda t: pd.DatetimeIndex([pd.Timestamp(t, tz=LONDON)])
    table = {"SHEL.L": pd.DataFrame([[3572, 3572, 3565, 3566]], columns=["Open", "High", "Low", "Close"],
                                    index=idx("2026-09-28 15:00")),                 # 3570p stop hit
             "TSM": pd.DataFrame([[414, 414.2, 413.5, 413.75]], columns=["Open", "High", "Low", "Close"],
                                 index=idx("2026-09-28 15:00"))}                    # $413.75/1.25 = £331
    q = {"GBPUSD=X": Quote("GBPUSD=X", 1.25, L(28, 15, 0, 30), "t"),
         "TSM": Quote("TSM", 413.75, L(28, 15, 1, 0), "t")}
    r = monitor.run_once(str(tmp_path), now=L(28, 15, 1, 5),
                         bars_fn=lambda s, a, b: table[s][(table[s].index >= a) & (table[s].index <= b)],
                         quote_fn=q.get)
    assert r["action"] == "halt_flatten_all"
    assert r["day_pnl"] == pytest.approx(400 * (35.70 - 36.00) + 10 * (413.75 / 1.25 - 340.0), abs=0.01)
    ls = json.loads((tmp_path / "live_status.json").read_text())
    assert ls["halted"] and ls["open_positions"] == []


# ─── USD legs ─────────────────────────────────────────────────────────

def test_tsm_usd_levels_friday_bars(tmp_path):
    """Fill 15:17 on 450.207 USD at GBPUSD 1.3253 -> £339.70; stop $449.50 hit by
    the 15:29 bar (Low 449.32) -> exit $449.50 = £339.17; P&L in GBP."""
    now = L(25, 15, 18, 5)
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": now.isoformat()}))
    q = {"TSM": Quote("TSM", 450.207, L(25, 15, 17, 59), "t"),
         "GBPUSD=X": Quote("GBPUSD=X", FX, L(25, 15, 17, 59), "t")}
    e = emulator.PaperTradingEmulator(data_dir=str(tmp_path), quote_fn=q.get, bars_fn=fixture_bars,
                                      now_fn=lambda: now)
    e.requote_wait_s = 0
    t = e.execute_buy("TSM", 450.207 / FX, 450.207 / FX, 449.50, 492.787, "t", 0, 0, 0, levels_ccy="USD")
    assert t.price == pytest.approx(450.207 / FX) and t.quantity == pytest.approx(1.0)
    pos = e.positions["TSM"]
    assert pos.level_ccy == "USD" and pos.stop_loss == 449.50 and pos.fx_ref == FX
    assert e.level_to_gbp(pos, pos.stop_loss) == pytest.approx(449.50 / FX)
    closed = e.check_stops_and_targets(now=L(25, 21, 5))
    assert len(closed) == 1 and closed[0].timestamp.startswith("2026-09-25T15:29")
    assert closed[0].price == pytest.approx(449.50 / FX)
    assert closed[0].pnl == pytest.approx((449.50 - 450.207) / FX)


def test_tsm_ticket_stop_no_trigger_mark_in_gbp(tmp_path):
    now = L(25, 15, 18, 5)
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": now.isoformat()}))
    q = {"TSM": Quote("TSM", 450.207, L(25, 15, 17, 59), "t"),
         "GBPUSD=X": Quote("GBPUSD=X", FX, L(25, 15, 17, 59), "t")}
    e = emulator.PaperTradingEmulator(data_dir=str(tmp_path), quote_fn=q.get, bars_fn=fixture_bars,
                                      now_fn=lambda: now)
    e.requote_wait_s = 0
    e.execute_buy("TSM", 339.7, None, 429.5215, 492.787, "t", 0, 0, 0, levels_ccy="USD")
    assert e.check_stops_and_targets(now=L(25, 21, 5)) == []
    p = e.positions["TSM"]
    assert p.current_price == pytest.approx(450.5799865722656 / FX)   # 20:59 close, GBP


def test_usd_stop_passed_as_gbp_book_level_is_refused(make_emulator):
    """A $429.52 ticket stop given without levels_ccy would be read as £429.52,
    above the £340 fill -> instant stop-out. Refused instead."""
    now = L(28, 15, 0, 30)
    e = make_emulator(now, {"TSM": Quote("TSM", 450.61, L(28, 15, 0, 10), "t"),
                            "GBPUSD=X": Quote("GBPUSD=X", FX, L(28, 15, 0, 10), "t")})
    with pytest.raises(InvalidLevelsError):
        e.execute_buy("TSM", 340, None, 429.5215, 492.787, "t", 0, 0, 0)
    with pytest.raises(InvalidLevelsError):
        e.execute_buy("TSM", 340, None, 429.5215, 492.787, "t", 0, 0, 0, levels_ccy="EUR")
    assert e.positions == {}


def test_vusa_gbp_friday_bars(tmp_path):
    now = L(25, 10, 1, 5)
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": now.isoformat()}))
    q = {"VUSA.L": Quote("VUSA.L", 110.615, L(25, 10, 0, 59), "t")}
    e = emulator.PaperTradingEmulator(data_dir=str(tmp_path), quote_fn=q.get, bars_fn=fixture_bars,
                                      now_fn=lambda: now)
    e.requote_wait_s = 0
    t = e.execute_buy("VUSA", 12 * 110.615, 110.615, 110.05, 114.9603, "t", 0, 0, 0)
    assert t.price == pytest.approx(110.615)                 # GBP line: no /100
    closed = e.check_stops_and_targets(now=L(25, 16, 40))
    assert closed[0].price == pytest.approx(110.05) and closed[0].pnl == pytest.approx(12 * (110.05 - 110.615))


def test_lse_20min_delayed_yahoo_quote_rejected_manual_fresh_quote_accepted(make_emulator):
    """Yahoo .L is ~20 min delayed: a 20-min-old print fails the 60s rule (unchanged);
    a fresh quote read off the order ticket, passed explicitly, is accepted."""
    from quotes import StaleQuoteError
    now = L(28, 9, 30, 0)
    delayed = Quote("VUSA.L", 110.40, L(28, 9, 10, 0), "yfinance_chart_regularMarketTime")
    e = make_emulator(now, {"VUSA.L": [delayed, delayed]})
    with pytest.raises(StaleQuoteError):
        e.execute_buy("VUSA", 1300, 110.4, 108.1686, 114.9603, "t", 0, 0, 0)
    manual = Quote("VUSA.L", 110.45, L(28, 9, 29, 40), "freetrade_order_ticket_manual")
    t = e.execute_buy("VUSA", 13 * 110.45, 110.45, 108.1686, 114.9603, "t", 0, 0, 0, quote=manual)
    assert t.price == pytest.approx(110.45) and t.quote_source == "freetrade_order_ticket_manual"
