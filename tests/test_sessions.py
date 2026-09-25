"""Risk SOP v2.2: exchange sessions, DST, coverage helper, registration rules."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import instruments
import market_hours
from instruments import EXCHANGES, Instrument, NoSessionDataError
from market_hours import MarketNotCoveredError
from quotes import LONDON, Quote

UTC = timezone.utc


def L(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=LONDON)


def test_required_exchanges_defined_with_zones():
    for code, tz in [("LSE", "Europe/London"), ("XETRA", "Europe/Berlin"),
                     ("EURONEXT_PARIS", "Europe/Paris"), ("EURONEXT_AMSTERDAM", "Europe/Amsterdam"),
                     ("NYSE", "America/New_York"), ("NASDAQ", "America/New_York"),
                     ("TSE", "Asia/Tokyo"), ("HKEX", "Asia/Hong_Kong"), ("ASX", "Australia/Sydney")]:
        assert EXCHANGES[code].tz == tz and EXCHANGES[code].segments


def test_lse_open_tracks_uk_dst():
    lse = EXCHANGES["LSE"]
    # Fri 23 Oct 2026 BST: 08:00 London = 07:00 UTC; Mon 26 Oct GMT: 08:00 London = 08:00 UTC
    assert lse.window_on(datetime(2026, 10, 23).date())[0].astimezone(UTC).hour == 7
    assert lse.window_on(datetime(2026, 10, 26).date())[0].astimezone(UTC).hour == 8


def test_nyse_during_uk_us_dst_mismatch_week():
    """UK leaves BST on 25 Oct 2026, US leaves EDT on 1 Nov 2026: that week NYSE
    runs 13:30-20:00 London, not the usual 14:30-21:00."""
    nyse = EXCHANGES["NYSE"]
    assert nyse.state(L(2026, 10, 27, 13, 35))[0] == "open"
    assert nyse.state(L(2026, 10, 27, 20, 5))[0] == "final"
    assert nyse.state(L(2026, 10, 27, 20, 30))[0] == "closed"
    # a normal week: open until 21:00 London, final check 21:00-21:10
    assert nyse.state(L(2026, 9, 25, 20, 59))[0] == "open"
    assert nyse.state(L(2026, 9, 25, 21, 5))[0] == "final"
    assert nyse.state(L(2026, 9, 25, 13, 35))[0] == "closed"


def test_asx_sydney_dst_start_moves_open_into_sunday_utc():
    """Sydney DST starts Sun 4 Oct 2026. Fri 2 Oct ASX opens 10:00 AEST = 01:00 BST;
    Mon 5 Oct it opens 10:00 AEDT = 00:00 BST (23:00 UTC Sunday)."""
    asx = EXCHANGES["ASX"]
    assert asx.state(L(2026, 10, 2, 0, 30))[0] == "closed"
    assert asx.state(L(2026, 10, 2, 1, 0))[0] == "open"
    mon_open = asx.window_on(datetime(2026, 10, 5).date())[0]
    assert mon_open.astimezone(UTC) == datetime(2026, 10, 4, 23, 0, tzinfo=UTC)
    assert asx.state(L(2026, 10, 5, 0, 30))[0] == "open"
    assert asx.state(datetime(2026, 10, 4, 22, 59, tzinfo=UTC))[0] == "closed"


def test_tse_and_hkex_sessions_in_london_time():
    tse, hk = EXCHANGES["TSE"], EXCHANGES["HKEX"]
    assert tse.state(L(2026, 9, 28, 1, 0))[0] == "open"      # 09:00 JST
    assert tse.state(L(2026, 9, 28, 7, 35))[0] == "final"    # 15:35 JST
    assert tse.state(L(2026, 9, 28, 8, 0))[0] == "closed"
    assert hk.state(L(2026, 9, 28, 9, 5))[0] == "open"       # 16:05 HKT closing auction
    assert hk.next_open(L(2026, 9, 26, 12, 0)) == datetime(2026, 9, 28, 9, 30, tzinfo=ZoneInfo("Asia/Hong_Kong"))


def test_register_rejects_instrument_without_session_data():
    bad = Instrument(symbol="ZZZT", name="test", isin="XX0000000001", exchange="MOON",
                     listing="MOON:USD", currency="USD", quote_unit="USD",
                     yf_symbol="ZZZT.MO", freetrade_ticker="ZZZT")
    with pytest.raises(NoSessionDataError):
        instruments.register(bad)
    assert instruments.lookup("ZZZT") is None


def test_register_rejects_duplicate_isin():
    dup = Instrument(symbol="CIBRX", name="alias attempt", isin="IE00BF16M727", exchange="LSE",
                     listing="LSE:GBX", currency="GBP", quote_unit="GBX",
                     yf_symbol="FCBR.L", freetrade_ticker="FCBR")
    with pytest.raises(ValueError):
        instruments.register(dup)


def test_fill_rejected_when_exchange_has_no_session_data(monkeypatch):
    bad = Instrument(symbol="ZZZT", name="test", isin="XX0000000001", exchange="MOON",
                     listing="MOON:USD", currency="USD", quote_unit="USD",
                     yf_symbol="ZZZT.MO", freetrade_ticker="ZZZT")
    monkeypatch.setitem(instruments.REGISTRY, "ZZZT", bad)
    monkeypatch.setitem(instruments._BY_YF, "ZZZT.MO", bad)
    with pytest.raises(NoSessionDataError):
        instruments.assert_fill_matches_ticket("ZZZT", "ZZZT.MO")
    assert market_hours.coverage("ZZZT")["covered"] is False


def test_coverage_helper(tmp_path):
    now = L(2026, 9, 28, 9, 0)
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": now.isoformat()}))
    c = market_hours.coverage("ISPY.L", at=now, data_dir=str(tmp_path))
    assert c["covered"] and c["market_open"] and c["fill_allowed"] and c["exchange"] == "LSE"
    c = market_hours.coverage("XOM", at=now, data_dir=str(tmp_path))
    assert c["covered"] and not c["market_open"] and not c["fill_allowed"]
    assert c["next_open_london"] == "2026-09-28T14:30:00+01:00"
    stale = market_hours.coverage("ISPY.L", at=now + timedelta(minutes=5), data_dir=str(tmp_path))
    assert not stale["covered"] and "heartbeat" in stale["reason"]


def test_buy_rejected_when_market_closed_or_monitor_down(make_emulator, tmp_path):
    e = make_emulator(L(2026, 9, 28, 9, 0), {"XOM": Quote("XOM", 160, L(2026, 9, 28, 8, 59), "t")})
    with pytest.raises(MarketNotCoveredError):
        e.execute_buy("XOM", 1000, 128, 126, 131, "t", 0, 0, 0)     # NYSE closed at 09:00 London
    e2 = make_emulator(L(2026, 9, 28, 9, 0), {"ISPY.L": Quote("ISPY.L", 3700, L(2026, 9, 28, 8, 59), "t")})
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": L(2026, 9, 28, 8, 50).isoformat()}))
    with pytest.raises(MarketNotCoveredError, match="heartbeat"):
        e2.execute_buy("ISPY.L", 1000, 37, 36.6, 38, "t", 0, 0, 0)
    assert e2.trade_history == []
