import pytest

import instruments
from instruments import (REGISTRY, InstrumentMismatchError, UnknownInstrumentError,
                         assert_fill_matches_ticket, resolve)
from quotes import Quote

REQUIRED = {"CIBR", "ISPY", "IESU", "VUSA", "XOM"}


def test_registry_has_required_fields():
    assert REQUIRED <= set(REGISTRY)
    for sym in REQUIRED:
        i = REGISTRY[sym]
        assert len(i.isin) == 12 and i.isin[:2] in ("IE", "US")
        assert i.exchange and i.listing and i.currency in ("GBP", "USD")
        assert i.quote_unit in ("GBX", "GBP", "USD")
        assert i.yf_symbol and i.freetrade_ticker
        assert i.isin_verified and i.sources


def test_verified_isins():
    assert resolve("CIBR").isin == "IE00BF16M727"
    assert resolve("ISPY").isin == "IE00BYPLS672"
    assert resolve("IESU").isin == "IE00B42NKQ00"
    assert resolve("VUSA").isin == "IE00B3XXRP09"
    assert resolve("XOM").isin == "US30233Q1085"
    assert "US30231G1022" in resolve("XOM").former_isins


def test_isins_unique_and_no_aliases():
    assert len({i.isin for i in REGISTRY.values()}) == len(REGISTRY)
    for sym, inst in REGISTRY.items():
        assert resolve(inst.yf_symbol) is inst
        assert resolve(sym) is inst


def test_cibr_can_never_resolve_to_ispy():
    for s in ("CIBR", "cibr", "CIBR.L", " CIBR "):
        inst = resolve(s)
        assert inst.symbol == "CIBR"
        assert inst.isin != resolve("ISPY").isin
        assert inst.yf_symbol == "CIBR.L" and inst.currency == "USD" and inst.listing == "LSE:USD"


def test_cibr_ticket_filling_ispy_raises():
    with pytest.raises(InstrumentMismatchError, match="ISIN mismatch"):
        assert_fill_matches_ticket("CIBR", "ISPY.L")
    with pytest.raises(InstrumentMismatchError):
        assert_fill_matches_ticket("CIBR.L", "ISPY")


def test_ticket_identity_overrides_checked():
    assert assert_fill_matches_ticket("CIBR", "CIBR.L", "IE00BF16M727", "LSE:USD", "USD").symbol == "CIBR"
    with pytest.raises(InstrumentMismatchError):
        assert_fill_matches_ticket("CIBR", "CIBR.L", ticket_isin="IE00BYPLS672")
    with pytest.raises(InstrumentMismatchError):
        assert_fill_matches_ticket("CIBR", "CIBR.L", ticket_currency="GBP")


def test_unknown_symbol_raises():
    with pytest.raises(UnknownInstrumentError):
        resolve("FCBR.L")
    with pytest.raises(UnknownInstrumentError):
        assert_fill_matches_ticket("CIBR", "NOPE.L")


def test_quote_units():
    assert resolve("ISPY.L").to_book_gbp(3753.513916) == pytest.approx(37.53513916)
    assert resolve("VUSA.L").to_book_gbp(110.42) == pytest.approx(110.42)  # GBP, not pence
    assert resolve("IESU.L").to_book_gbp(996.5) == pytest.approx(9.965)
    with pytest.raises(ValueError):
        resolve("XOM").to_book_gbp(160.0)
    assert resolve("XOM").to_book_gbp(160.0, gbpusd=1.25) == pytest.approx(128.0)


def test_emulator_rejects_cibr_ticket_filled_as_ispy(make_emulator, london, tmp_path):
    now = london(8, 22, 21)
    e = make_emulator(now, {"ISPY.L": Quote("ISPY.L", 3753.51, london(8, 22, 0), "test")})
    with pytest.raises(InstrumentMismatchError):
        e.execute_buy("ISPY.L", 12000, 37.75, 37.3725, 38.6937, "ticket CIBR", 0.75, 0.75, 0.75,
                      ticket_symbol="CIBR")
    assert e.trade_history == [] and e.positions == {} and e.cash == 20000
    log = (tmp_path / "alerts_log.jsonl").read_text()
    assert '"type": "skip"' in log and "instrument_check_failed" in log


def test_emulator_rejects_quote_for_other_instrument(make_emulator, london):
    now = london(8, 22, 21)
    e = make_emulator(now)
    q = Quote("ISPY.L", 3753.51, london(8, 22, 0), "test")
    with pytest.raises(InstrumentMismatchError):
        e.execute_buy("CIBR.L", 1000, 64.83, 64.0, 66.0, "x", 0, 0, 0, ticket_symbol="CIBR", quote=q)
