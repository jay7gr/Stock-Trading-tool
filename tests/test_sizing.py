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


# ─── package-level check (Risk execution ruling 27 Sep) ───────────────
import json
from datetime import datetime

from quotes import LONDON, Quote
from sizing import PackageLeg, OpenLeg, package_qty, rank_of


def _legs(prices=None, fx=FX):
    prices = prices or {}
    out = []
    for sym, (entry, stop, qty, _, _) in TICKETS.items():
        out.append(PackageLeg(sym, qty, stop, prices.get(sym, entry), rank_of(sym),
                              None if sym in ("VUSA", "SHEL") else fx))
    return out


def test_package_at_ticket_prices_all_fill_188_97():
    r = package_qty(_legs())
    assert [(l["symbol"], l["qty"]) for l in r["legs"]] == [("TSM", 1), ("VUSA", 13), ("SHEL", 18),
                                                            ("WMT", 7), ("MSFT", 1)]
    assert r["new_risk_gbp"] == pytest.approx(188.96, abs=0.01) and r["cuts"] == []


def test_per_leg_budgets_no_longer_bind():
    # VUSA up 0.5%: per-leg sizing would cut 13 -> 10; the package still has room
    r = package_qty(_legs({"VUSA": 111.0}))
    assert r["legs"][1]["qty"] == 13 and r["combined_risk_gbp"] <= 200


def test_cut_order_msft_then_wmt_share_by_share_then_shel():
    q = lambda r: {l["symbol"]: l["qty"] for l in r["legs"]}
    assert q(package_qty(_legs(), realised_pnl_today=-35.0)) == {"TSM": 1, "VUSA": 13, "SHEL": 18, "WMT": 7, "MSFT": 0}
    r = package_qty(_legs(), realised_pnl_today=-45.0)          # headroom 155 -> MSFT out, WMT -1
    assert q(r) == {"TSM": 1, "VUSA": 13, "SHEL": 18, "WMT": 6, "MSFT": 0}
    assert r["cuts"] == ["-1 MSFT (rank 5)", "-1 WMT (rank 4)"]
    r = package_qty(_legs(), realised_pnl_today=-120.0)         # headroom 80: WMT gone, SHEL trimmed
    assert q(r)["WMT"] == 0 and q(r)["MSFT"] == 0 and 0 < q(r)["SHEL"] < 18
    assert q(r)["VUSA"] == 13 and q(r)["TSM"] == 1 and r["combined_risk_gbp"] <= 200 + 1e-9


def test_never_above_ticket_qty_and_gains_do_not_add_headroom():
    r = package_qty(_legs({"VUSA": 108.5}), realised_pnl_today=+500.0)
    assert {l["symbol"]: l["qty"] for l in r["legs"]}["VUSA"] == 13
    assert r["headroom_gbp"] == pytest.approx(200.0)


def test_leg_at_or_below_stop_skipped_others_keep_ticket_qty():
    r = package_qty(_legs({"SHEL": 34.7739, "WMT": 103.0}))
    q = {l["symbol"]: (l["qty"], l["status"]) for l in r["legs"]}
    assert q["SHEL"][0] == 0 and q["SHEL"][1].startswith("skip") and q["WMT"][0] == 0
    assert q["TSM"][0] == 1 and q["VUSA"][0] == 13 and q["MSFT"][0] == 1


def test_open_legs_count_full_risk_from_day_ref_to_stop():
    # VUSA filled at 110.4325 and SHEL at 36.11 this morning: both count full ticket risk,
    # whatever their (possibly delayed) marks say
    open_legs = [OpenLeg("VUSA", 13, 108.1686, 110.4325), OpenLeg("SHEL", 18, 34.7739, 36.11)]
    us = [l for l in _legs() if l.symbol in ("TSM", "WMT", "MSFT")]
    r = package_qty(us, open_legs)
    assert r["open_risk_gbp"] == pytest.approx(13 * 3.62224 + 18 * 2.31831, abs=1e-3)
    assert {l["symbol"]: l["qty"] for l in r["legs"]} == {"TSM": 1, "WMT": 7, "MSFT": 1}
    r = package_qty(us, open_legs, realised_pnl_today=-20.0)
    assert {l["symbol"]: l["qty"] for l in r["legs"]}["MSFT"] == 0


def test_package_cli(capsys):
    rc = sizing.main(["package", "--gbpusd", "1.325346", "--leg", "TSM:1:429.5215", "--leg", "WMT:7:103.704",
                      "--leg", "MSFT:1:492.6326", "--price", "TSM=450.61", "--price", "WMT=107.98",
                      "--price", "MSFT=516.17", "--realised", "-120", "--open", "VUSA:13:108.1686:110.4325"])
    r = json.loads(capsys.readouterr().out)
    assert rc == 0 and {l["symbol"]: l["qty"] for l in r["legs"]} == {"TSM": 1, "WMT": 0, "MSFT": 0}


def _L(h, m, s=0):
    return datetime(2026, 9, 28, h, m, s, tzinfo=LONDON)


def test_book_package_us_legs_with_open_lse_legs(tmp_path):
    now = _L(14, 35, 5)
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": now.isoformat()}))
    (tmp_path / "portfolio.json").write_text(json.dumps({"cash": 20000, "trade_counter": 2, "positions": {
        "VUSA.L": {"ticker": "VUSA.L", "quantity": 13, "avg_entry_price": 110.4325, "current_price": 110.9,
                   "stop_loss": 108.1686, "take_profit": 114.9603, "opened_at": "2026-09-28T08:06:00+01:00"},
        "SHEL.L": {"ticker": "SHEL.L", "quantity": 18, "avg_entry_price": 36.11, "current_price": 36.2,
                   "stop_loss": 34.7739, "take_profit": 38.7822, "opened_at": "2026-09-28T08:06:00+01:00"}}}))
    (tmp_path / "trades.json").write_text("[]")
    q = {"TSM": Quote("TSM", 450.0, _L(14, 35, 0), "t"), "WMT": Quote("WMT", 107.9, _L(14, 35, 0), "t"),
         "MSFT": Quote("MSFT", 516.0, _L(14, 35, 0), "t"), "GBPUSD=X": Quote("GBPUSD=X", FX, _L(14, 35, 0), "t")}
    specs = [sizing.parse_leg(x) for x in ("TSM:1:429.5215:492.787", "WMT:7:103.704:116.532",
                                           "MSFT:1:492.6326:563.2448")]
    r = sizing.book_package(specs, data_dir=str(tmp_path), now_fn=lambda: now, quote_fn=q.get)
    assert r["ok"] and [f["symbol"] for f in r["fills"]] == ["TSM", "WMT", "MSFT"]
    assert r["package"]["combined_risk_gbp"] <= 200
    port = json.loads((tmp_path / "portfolio.json").read_text())["positions"]
    assert port["TSM"]["level_ccy"] == "USD" and port["TSM"]["stop_loss"] == 429.5215
    assert port["WMT"]["quantity"] == 7 and port["TSM"]["avg_entry_price"] == pytest.approx(450.0 / FX)
    # LSE legs are refused on this real-time path
    r = sizing.book_package([sizing.parse_leg("VUSA:13:108.1686")], data_dir=str(tmp_path),
                            now_fn=lambda: now, quote_fn=q.get)
    assert not r["ok"] and "lse_leg.py" in r["reason"]


def test_book_package_dry_run_writes_nothing(tmp_path):
    now = _L(14, 35, 5)
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": now.isoformat()}))
    q = {"MSFT": Quote("MSFT", 516.0, _L(14, 35, 0), "t"), "GBPUSD=X": Quote("GBPUSD=X", FX, _L(14, 35, 0), "t")}
    r = sizing.book_package([sizing.parse_leg("MSFT:1:492.6326")], data_dir=str(tmp_path), dry_run=True,
                            now_fn=lambda: now, quote_fn=q.get)
    assert r["ok"] and sorted(p.name for p in tmp_path.iterdir()) == ["monitor_heartbeat.json"]
