"""Net P&L cost model (costs.py): Freetrade FX on non-GBP buy+sell notional, UK stamp on UK
share buys only. Reporting only - gross stays the ranking/sizing/halt number."""
import json
import os
from datetime import date
from types import SimpleNamespace as NS

import pytest

import config
import costs
import pnl
import sizing

D = date(2026, 9, 28)


def T(id, ticker, action, qty, price, ts, pnl_=0.0, day_pnl=None):
    return NS(id=id, ticker=ticker, action=action, quantity=qty, price=price,
              value_gbp=qty * price, timestamp=ts, pnl=pnl_, day_pnl=day_pnl)


def P(ticker, qty, entry, mark, opened="2026-09-28T08:06:00+01:00", prev_close=0.0, pcd=""):
    return NS(ticker=ticker, quantity=qty, avg_entry_price=entry, current_price=mark,
              opened_at=opened, prev_close=prev_close, prev_close_date=pcd, prev_close_source="")


def test_constants_named_and_consistent_with_sizing():
    assert config.FREETRADE_PLAN == "Plus"
    assert config.FREETRADE_FX_FEE_PER_SIDE == pytest.approx(0.0039)
    assert config.FREETRADE_FX_FEE_BY_PLAN == {"Basic": 0.0099, "Standard": 0.0059, "Plus": 0.0039}
    assert config.UK_STAMP_DUTY_PCT == pytest.approx(0.005)
    # sizing keeps its own round-trip constant (sizing unchanged); it must agree with the cost model
    assert sizing.FX_ROUND_TRIP == pytest.approx(2 * config.FREETRADE_FX_FEE_PER_SIDE)


@pytest.mark.parametrize("tkr,fx,stamp", [
    ("VUSA.L", 0.0, 0.0),      # GBP-line UCITS ETF: no FX, no stamp
    ("ISPY.L", 0.0, 0.0),      # GBX-line UCITS ETF: no FX, no stamp
    ("SHEL.L", 0.0, 0.005),    # UK share: stamp on buy
    ("TSM", 0.0039, 0.0),      # US share: FX both sides, no stamp
    ("WMT", 0.0039, 0.0),
    ("CIBR.L", 0.0039, 0.0),   # USD line of a UCITS ETF on LSE: FX, no stamp
])
def test_rates_by_instrument(tkr, fx, stamp):
    assert costs.fx_rate(tkr) == pytest.approx(fx)
    assert costs.stamp_rate(tkr) == pytest.approx(stamp)


def test_buy_and_sell_legs():
    assert costs.buy_costs("SHEL.L", 655.56)["total"] == pytest.approx(3.2778)
    assert costs.sell_costs("SHEL.L", 700.0)["total"] == 0.0           # no stamp on sales
    assert costs.buy_costs("MSFT", 380.18)["fx"] == pytest.approx(1.482702)
    assert costs.sell_costs("MSFT", 383.14)["fx"] == pytest.approx(1.494246)
    assert costs.buy_costs("VUSA.L", 1434.64)["total"] == 0.0


def test_trade_net_pnl_round_trip():
    trades = [T("T7", "MSFT", "BUY", 1, 380.178, "2026-09-28T14:42:33+01:00"),
              T("T8", "MSFT", "SELL", 1, 383.1436, "2026-09-28T14:58:53+01:00", pnl_=2.9656)]
    fees = 0.0039 * (380.178 + 383.1436)
    assert costs.trade_net_pnl(trades, trades[1]) == pytest.approx(2.9656 - fees)
    assert costs.trade_net_pnl(trades, trades[0]) is None


def test_day_net_matches_28_sep_postmortem_fees():
    """Book of 28 Sep: Plus fees £13.37 per the 28 Sep postmortem (stamp 3.28, TSM 2.65, WMT 4.47,
    MSFT 2.98; sell side of open legs priced at the mark)."""
    trades = [T("T3", "VUSA.L", "BUY", 13, 110.35659790039062, "2026-09-28T08:06:00+01:00"),
              T("T4", "SHEL.L", "BUY", 18, 36.42, "2026-09-28T08:06:00+01:00"),
              T("T5", "TSM", "BUY", 1, 337.37743249358874, "2026-09-28T14:42:33+01:00"),
              T("T6", "WMT", "BUY", 7, 81.70161411977672, "2026-09-28T14:42:33+01:00"),
              T("T7", "MSFT", "BUY", 1, 380.178, "2026-09-28T14:42:33+01:00"),
              T("T8", "MSFT", "SELL", 1, 383.14361100640787, "2026-09-28T14:58:53+01:00",
                pnl_=2.965605274019879, day_pnl=2.965605274019879)]
    pos = [P("VUSA.L", 13, 110.35659790039062, 109.7225), P("SHEL.L", 18, 36.42, 36.62),
           P("TSM", 1, 337.37743249358874, 341.31352276917835, "2026-09-28T14:42:33+01:00"),
           P("WMT", 7, 81.70161411977672, 81.93334614847637, "2026-09-28T14:42:33+01:00")]
    r = costs.day_net_pnl(trades, pos, D)
    b = r["breakdown"]
    assert b["stamp"] == pytest.approx(3.2778)
    assert r["costs"] == pytest.approx(13.37, abs=0.01)
    assert r["net"] == pytest.approx(r["gross"] - r["costs"])
    assert r["gross"] == pytest.approx(pnl.day_pnl(trades, pos, D))   # gross untouched


def test_carried_position_counts_each_cost_once():
    """Bought on 25th (entry costs that day). On the 28th only exit FX accrued since the prev close."""
    buy = T("B", "TSM", "BUY", 2, 300.0, "2026-09-25T15:00:00+01:00")
    pos = P("TSM", 2, 300.0, 310.0, opened="2026-09-25T15:00:00+01:00", prev_close=305.0,
            pcd="2026-09-28")
    c28 = costs.day_costs([buy], [pos], D)
    assert c28["fx_buy"] == 0.0 and c28["fx_open_exit"] == pytest.approx(0.0039 * 2 * (310 - 305))
    # sold on the 28th at 312 (day ref = prev close 305)
    sell = T("S", "TSM", "SELL", 2, 312.0, "2026-09-28T15:00:00+01:00", pnl_=24.0, day_pnl=14.0)
    c = costs.day_costs([buy, sell], [], D)
    assert c["fx_sell"] == pytest.approx(0.0039 * 2 * (312 - 305))
    # lifetime = entry FX (25th, 300*2) + accrual to prev close (25th..27th, 305*2) + today's part
    c25 = costs.day_costs([buy], [P("TSM", 2, 300.0, 305.0, opened="2026-09-25T15:00:00+01:00")],
                          date(2026, 9, 25))
    assert c25["total"] + c["total"] == pytest.approx(0.0039 * (600 + 624))


def test_real_book_25_sep_net_equals_gross_and_official_is_minus_187_06():
    """ISPY is a GBX UCITS ETF (no FX, no stamp): 25 Sep net = gross = official -187.06."""
    import emulator
    e = emulator.PaperTradingEmulator()
    r = costs.day_net_pnl(e.trade_history, [], date(2026, 9, 25))
    assert round(r["gross"], 2) == -187.06 and round(r["net"], 2) == -187.06


def test_restatements_official_figure_and_memo_line():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "restatements.json")
    if not os.path.exists(path):
        pytest.skip("no data/restatements.json")
    rec = next(r for r in json.load(open(path)) if str(r.get("T00002", {}).get("after", {}).get("timestamp", "")).startswith("2026-09-25"))
    assert rec["day_pnl_after"] == -187.06                       # original schema field unchanged
    assert round(rec["T00002"]["after"]["pnl"], 2) == -187.06
    if "official" in rec:
        assert rec["official"]["day_pnl_gbp"] == -187.06 and rec["official"]["is_official"] is True
        m = rec["memo_live_exit_alternative"]
        assert m["is_official"] is False
        assert m["pnl_gbp"] == pytest.approx(m["quantity"] * (m["exit_price_gbp"] - m["entry_price_gbp"]), abs=0.005)
        assert m["pnl_gbp"] == pytest.approx(4.76, abs=0.005)


def test_monitor_status_writer_adds_net_fields_next_to_gross(tmp_path):
    from datetime import datetime
    import monitor
    from quotes import LONDON
    trades = [T("T7", "MSFT", "BUY", 1, 380.0, "2026-09-28T14:42:33+01:00"),
              T("T8", "MSFT", "SELL", 1, 383.0, "2026-09-28T14:58:53+01:00", pnl_=3.0, day_pnl=3.0),
              T("T4", "SHEL.L", "BUY", 18, 36.42, "2026-09-28T08:06:00+01:00")]
    pos = P("SHEL.L", 18, 36.42, 36.62)
    pos.stop_loss, pos.take_profit, pos.isin, pos.level_ccy, pos.last_check = 34.77, 38.78, "", "", ""
    emu = NS(trade_history=trades, positions={"SHEL.L": pos}, cash=1000.0,
             level_to_gbp=lambda p, lvl: lvl)
    ls = monitor.write_status(emu, str(tmp_path), datetime(2026, 9, 28, 18, 0, tzinfo=LONDON), [], "t", "t")
    gross = 3.0 + 18 * 0.2
    assert ls["day_pnl"] == ls["day_pnl_gross"] == pytest.approx(round(gross, 2))
    assert ls["day_costs_gbp"] == pytest.approx(round(0.0039 * 763 + 18 * 36.42 * 0.005, 2))
    assert ls["day_pnl_net"] == pytest.approx(round(gross - 0.0039 * 763 - 18 * 36.42 * 0.005, 2))
    assert ls["remaining_day_risk_gbp"] == pytest.approx(round(gross + 200, 2))   # halt maths on gross
    row = ls["open_positions"][0]
    assert row["costs_gbp"] == pytest.approx(3.28) and row["net_unrealised_gbp"] == pytest.approx(0.32)
    snap = json.load(open(tmp_path / "dashboard_snapshot.json"))
    assert snap["day_pnl_net"] == ls["day_pnl_net"]
