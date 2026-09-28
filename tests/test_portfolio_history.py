"""Benchmark tab P&L (portfolio.calculate_portfolio_history -> compute_period_returns).

Regression 2026-09-28: Emulator.execute_sell stamps the round-trip P&L on the SELL row AND on
the closed BUY leg(s); calculate_portfolio_history summed pnl over every row, so each round
trip was counted twice (gross, and therefore the Net P&L column too). Also the series used to
start on the first trade day with that day's P&L already in it, so period deltas (last - first)
dropped day 1 entirely. A round trip must count exactly once, gross and net."""
import json
from datetime import datetime
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import costs
import emulator
from portfolio import calculate_portfolio_history, compute_period_returns
from quotes import LONDON, Quote

CAP = 20000.0


def L(d, h, m, s=0):
    return datetime(2026, 9, d, h, m, s, tzinfo=LONDON)


def _net(trades, gross):
    """Same formula as the dashboard Benchmark tab's all-time Net P&L column."""
    return gross - sum((costs.buy_costs if t.action == "BUY" else costs.sell_costs)(t.ticker, t.value_gbp)["total"]
                       for t in trades)


def _all_time_pnl(trades):
    df = calculate_portfolio_history(trades, CAP)
    return df, compute_period_returns(df, pd.DataFrame())["all_time"]["portfolio_pnl"]


def test_emulator_round_trip_counts_once_gross_and_net(tmp_path):
    """Real buy + sell through the emulator (which marks the BUY leg closed with the same pnl)."""
    now = L(25, 10, 1, 5)
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": now.isoformat()}))
    q = {"SHEL.L": Quote("SHEL.L", 3605.0, L(25, 10, 0, 59), "t")}
    e = emulator.PaperTradingEmulator(data_dir=str(tmp_path), quote_fn=q.get, now_fn=lambda: now)
    e.requote_wait_s = 0
    buy = e.execute_buy("SHEL", 18 * 36.05, 36.05, 34.7739, 38.7822, "t", 0, 0, 0)
    sell = e.execute_sell("SHEL.L", "manual", fill_price=36.55, fill_ts=L(25, 14, 0))
    gross = 18 * (36.55 - 36.05)                                    # £9.00
    assert sell.pnl == pytest.approx(gross)
    assert buy.status == "closed" and buy.pnl == pytest.approx(gross)  # the duplicate stamp
    assert e.total_realised_pnl() == pytest.approx(gross)

    df, all_time = _all_time_pnl(e.trade_history)
    assert df["cumulative_pnl"].iloc[-1] == pytest.approx(gross)       # was 2 x gross
    assert df["daily_pnl"].sum() == pytest.approx(gross)
    assert all_time == pytest.approx(gross, abs=0.005)                 # was 0 (day 1 dropped)
    fees = costs.buy_costs("SHEL.L", buy.value_gbp)["total"]           # stamp 0.5% on the buy only
    assert fees > 0
    assert _net(e.trade_history, all_time) == pytest.approx(gross - fees, abs=0.005)


def test_round_trip_booked_on_sell_day_not_entry_day():
    trades = [NS(ticker="MSFT", action="BUY", value_gbp=380.18, timestamp="2026-09-24T14:42:33+01:00",
                 pnl=2.97, status="closed"),
              NS(ticker="MSFT", action="SELL", value_gbp=383.14, timestamp="2026-09-25T14:58:53+01:00",
                 pnl=2.97, status="closed")]
    df = calculate_portfolio_history(trades, CAP)
    assert df.loc["2026-09-23", "portfolio_value"] == CAP              # anchor day before first trade
    assert df.loc["2026-09-24", "daily_pnl"] == 0.0
    assert df.loc["2026-09-25", "daily_pnl"] == pytest.approx(2.97)
    assert df["cumulative_pnl"].iloc[-1] == pytest.approx(2.97)


def test_live_book_shape_all_time_is_realised_total():
    """Shape of data/trades.json on 28 Sep: ISPY stop-out on 25 Sep, MSFT round trip on 28 Sep,
    four open BUYs (pnl 0). All-time Benchmark P&L = realised = -187.06 + 2.97."""
    rows = [("ISPY.L", "BUY", 12000.0, "2026-09-25T08:22:21+01:00", -187.06),
            ("ISPY.L", "SELL", 11812.94, "2026-09-25T14:34:00+01:00", -187.06),
            ("VUSA.L", "BUY", 1434.64, "2026-09-28T08:06:00+01:00", 0.0),
            ("MSFT", "BUY", 380.18, "2026-09-28T14:42:33+01:00", 2.97),
            ("MSFT", "SELL", 383.14, "2026-09-28T14:58:53+01:00", 2.97)]
    trades = [NS(ticker=t, action=a, value_gbp=v, timestamp=ts, pnl=p) for t, a, v, ts, p in rows]
    df, all_time = _all_time_pnl(trades)
    assert df["cumulative_pnl"].iloc[-1] == pytest.approx(-184.09)
    assert all_time == pytest.approx(-184.09, abs=0.005)


def test_empty_history_unchanged():
    df = calculate_portfolio_history([], CAP)
    assert len(df) == 1 and df["portfolio_value"].iloc[0] == CAP and df["cumulative_pnl"].iloc[0] == 0
