"""Execution-time sizing (SOP v2.3): reproduce the 2026-09-28 Risk ticket figures,
ticket qty as a ceiling, refuse at/below stop."""
import pytest

import sizing
from sizing import effective_risk_per_share as rps, executable_qty

FX = 1.325346
TICKETS = {  # sym: (entry, stop, qty, ticket eff risk/share £, ticket leg effective risk £)
    "TSM": (450.61, 429.5215, 1, 28.1107, 28.11),
    "VUSA": (110.4325, 108.1686, 13, 3.6222, 47.09),
    "SHEL": (36.11, 34.7739, 18, 2.3183, 41.73),
    "WMT": (107.98, 103.704, 7, 5.7976, 40.58),
    "MSFT": (516.17, 492.6326, 1, 31.4529, 31.45),
}


@pytest.mark.parametrize("sym", list(TICKETS))
def test_reproduces_ticket_effective_risk_per_share(sym):
    import instruments
    entry, stop, qty, want, leg = TICKETS[sym]
    i = instruments.resolve(sym)
    got = rps(entry, stop, i.currency, FX, purchase_tax=i.purchase_tax_pct)
    assert round(got, 4) == want
    r = executable_qty(sym, qty, stop, entry, FX, ticket_entry=entry)
    assert r.ok and r.qty == qty and round(r.effective_risk_gbp, 2) == leg


@pytest.mark.parametrize("sym", list(TICKETS))
def test_at_ticket_price_all_budget_forms_agree(sym):
    entry, stop, qty, want, leg = TICKETS[sym]
    assert executable_qty(sym, qty, stop, entry, FX, ticket_eff_rps_gbp=want).qty == qty
    assert executable_qty(sym, qty, stop, entry, FX, ticket_entry=entry).qty == qty


def test_live_price_up_shrinks_qty_never_grows():
    r = executable_qty("VUSA", 13, 108.1686, 111.00, ticket_entry=110.4325)
    # live rps = (111.00-108.1686)*1.6 = 4.53024 -> floor(47.0886/4.53024) = 10
    assert r.qty == 10 and r.ok and "risk-capped" in r.reason
    r = executable_qty("VUSA", 13, 108.1686, 109.50, ticket_entry=110.4325)
    assert r.qty == 13                           # price down: still capped at the ticket qty


def test_one_share_legs_go_to_zero_on_an_uptick():
    r = executable_qty("TSM", 1, 429.5215, 451.00, 1.3253, ticket_entry=450.61)
    assert r.qty == 0 and not r.ok and "cancel" in r.reason


def test_usd_needs_fx_and_weaker_pound_raises_gbp_risk():
    assert not executable_qty("WMT", 7, 103.704, 107.98, None, ticket_entry=107.98).ok
    strong = executable_qty("WMT", 7, 103.704, 107.50, 1.36, ticket_entry=107.98)
    weak = executable_qty("WMT", 7, 103.704, 107.50, 1.28, ticket_entry=107.98)
    assert strong.live_eff_rps_gbp < weak.live_eff_rps_gbp and weak.qty <= strong.qty


@pytest.mark.parametrize("live", [34.7739, 34.50])
def test_refuses_at_or_below_stop(live):
    r = executable_qty("SHEL", 18, 34.7739, live, ticket_entry=36.11)
    assert not r.ok and r.qty == 0 and "at or below the stop" in r.reason
    with pytest.raises(sizing.SizingError):
        rps(live, 34.7739)


def test_pence_passed_as_pounds_refused_and_raw_quote_helper():
    r = executable_qty("SHEL", 18, 34.7739, 3598.0, ticket_entry=36.11)
    assert not r.ok and "pence" in r.reason
    import instruments
    assert sizing.from_vendor_quote(instruments.resolve("SHEL"), 3598.0) == pytest.approx(35.98)


def test_cli(capsys):
    rc = sizing.main(["SHEL", "--ticket-qty", "18", "--stop", "34.7739", "--entry", "36.11",
                      "--quote-raw", "3611"])
    out = capsys.readouterr().out
    assert rc == 0 and '"qty": 18' in out
    rc = sizing.main(["TSM", "--ticket-qty", "1", "--stop", "429.5215", "--entry", "450.61",
                      "--price", "429.00", "--gbpusd", "1.3253"])
    assert rc == 2
