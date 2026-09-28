from datetime import date
from types import SimpleNamespace as NS

import pytest

import emulator
import pnl


def test_fmt_gbp():
    assert pnl.fmt_gbp(-187.06) == "-£187.06"
    assert pnl.fmt_gbp(-187.06, signed=True) == "-£187.06"
    assert pnl.fmt_gbp(12, signed=True) == "+£12.00"
    assert pnl.fmt_gbp(19812.94) == "£19,812.94"
    assert pnl.fmt_gbp(-0.001) == "£0.00"


def test_day_pnl_london_day_realised_plus_mtm():
    trades = [NS(action="SELL", pnl=-50.0, timestamp="2026-09-27T23:30:00+00:00"),   # 00:30 BST 28th
              NS(action="SELL", pnl=-10.0, timestamp="2026-09-27T22:30:00+00:00"),   # 23:30 BST 27th
              NS(action="BUY", pnl=-50.0, timestamp="2026-09-28T09:00:00")]
    pos = [NS(quantity=10, current_price=9.0, avg_entry_price=10.0)]
    assert pnl.day_pnl(trades, pos, date(2026, 9, 28)) == pytest.approx(-60.0)
    r = pnl.day_risk(-187.06)
    assert r["headroom_to_halt"] == pytest.approx(12.94) and r["to_target"] == pytest.approx(387.06)
    assert not r["breached"] and pnl.day_risk(-200)["breached"]


def test_real_book_today_is_restated_minus_187_06():
    e = emulator.PaperTradingEmulator()
    assert round(pnl.day_pnl(e.trade_history, e.positions.values(), date(2026, 9, 25)), 2) == -187.06


def test_past_day_excludes_positions_opened_later():
    """A past day evaluated against today's open book must ignore positions opened after it."""
    later = NS(quantity=10, current_price=12.0, avg_entry_price=10.0, opened_at="2026-09-28T08:06:00+01:00")
    same = NS(quantity=10, current_price=9.0, avg_entry_price=10.0, opened_at="2026-09-25T09:00:00+01:00")
    assert pnl.day_pnl([], [later, same], date(2026, 9, 25)) == pytest.approx(-10.0)
    assert pnl.day_pnl([], [later], date(2026, 9, 28)) == pytest.approx(20.0)   # today unchanged
