"""Hard live ATR-floor refusal BEFORE booking (post 2026-09-28 postmortem, SOP v2.3).

MSFT 28 Sep 14:42: fill 504.04 USD, stop 492.6326 USD. ATR14 = 11.772394916731045 USD, the
value the desk actually used that day: Research cache /workspace/research/scans/
_atr_completed_bars.json -> MSFT.use_atr_abs (yf Wilder, the larger of Wilder 11.77 / SMA 10.95
per SOP), identical to Risk's "ATR abs 11.7724" in the 28 Sep MSFT ruling. At 504.04 the ratio is
11.4074 / 11.7724 = 0.969; Risk quoted 0.99 because it measured at a 504.31 fill (Research
fill_usd 504.306). Both are below the 1.0 floor -> refused."""
import json
from datetime import datetime

import pytest

import config
import lse_leg
import sizing
from quotes import LONDON, Quote, load_day_bars

MSFT_ATR = 11.772394916731045      # _atr_completed_bars.json MSFT.use_atr_abs (USD)
TSM_ATR, WMT_ATR = 10.527253429160352, 2.140270557501527
MSFT_FILL, MSFT_STOP = 504.04, 492.6326
FX = MSFT_FILL / 380.178005732388  # GBPUSD implied by the booked T00007 (380.178 GBP)


def T(h, m, s=0):
    return datetime(2026, 9, 28, h, m, s, tzinfo=LONDON)


def _book_dir(tmp_path, now):
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": now.isoformat()}))
    (tmp_path / "portfolio.json").write_text(json.dumps({"cash": 20000, "trade_counter": 4, "positions": {
        "VUSA.L": {"ticker": "VUSA.L", "quantity": 13, "avg_entry_price": 110.3566, "current_price": 110.3,
                   "stop_loss": 108.1686, "take_profit": 114.9603, "opened_at": "2026-09-28T08:06:00+01:00"},
        "SHEL.L": {"ticker": "SHEL.L", "quantity": 18, "avg_entry_price": 36.42, "current_price": 36.6,
                   "stop_loss": 34.7739, "take_profit": 38.7822, "opened_at": "2026-09-28T08:06:00+01:00"}}}))
    (tmp_path / "trades.json").write_text("[]")


def _quotes(msft_usd, at):
    return {"TSM": Quote("TSM", 337.37743249358874 * FX, at, "t"),
            "WMT": Quote("WMT", 81.70161411977672 * FX, at, "t"),
            "MSFT": Quote("MSFT", msft_usd, at, "t"),
            "GBPUSD=X": Quote("GBPUSD=X", FX, at, "t")}


SPECS = [f"TSM:1:429.5215:492.787:{TSM_ATR}", f"WMT:7:103.704:116.532:{WMT_ATR}",
         f"MSFT:1:{MSFT_STOP}:563.2448:{MSFT_ATR}"]


def test_atr_floor_refuses_msft_28sep_1442_fill_before_booking(tmp_path):
    now = T(14, 42, 33)
    _book_dir(tmp_path, now)
    specs = [sizing.parse_leg(x) for x in SPECS]
    r = sizing.book_package(specs, data_dir=str(tmp_path), now_fn=lambda: now,
                            quote_fn=_quotes(MSFT_FILL, T(14, 42, 30)).get)
    legs = {l["symbol"]: l for l in r["package"]["legs"]}
    chk = legs["MSFT"]["atr_check"]
    assert chk["ratio"] == pytest.approx(11.4074 / MSFT_ATR, abs=1e-4) and chk["ratio"] < 1.0
    assert chk["fill"] == pytest.approx(504.04) and chk["stop"] == MSFT_STOP     # USD vs USD
    assert legs["MSFT"]["qty"] == 0 and legs["MSFT"]["status"].startswith("skip: atr_floor")
    # MSFT never booked; TSM/WMT keep their ticket qty (not resized)
    assert [f["symbol"] for f in r["fills"]] == ["TSM", "WMT"] and all(f["ok"] for f in r["fills"])
    assert legs["TSM"]["qty"] == 1 and legs["WMT"]["qty"] == 7
    trades = json.loads((tmp_path / "trades.json").read_text())
    assert "MSFT" not in {t["ticker"] for t in trades}
    assert "MSFT" not in json.loads((tmp_path / "portfolio.json").read_text())["positions"]
    # recorded for PM: result skips + actionable skip alert (stays notified=false for the relay)
    assert r["skips"][0]["symbol"] == "MSFT" and r["skips"][0]["reason"] == "atr_floor"
    alerts = [json.loads(l) for l in (tmp_path / "alerts_log.jsonl").read_text().splitlines()]
    sk = [a for a in alerts if a["type"] == "skip"]
    assert len(sk) == 1 and sk[0]["ticker"] == "MSFT" and sk[0]["notified"] is False
    d = sk[0]["details"]
    assert d["reason"] == "atr_floor" and d["ratio"] < 1.0 and d["fill"] == pytest.approx(504.04)
    assert d["stop"] == MSFT_STOP and d["atr"] == MSFT_ATR and "skip_reason" not in sk[0]
    # Risk's own 504.31 measurement (0.99x) is refused too
    chk2 = sizing.atr_floor_check(504.30612460401267, MSFT_STOP, MSFT_ATR)
    assert not chk2["ok"] and round(chk2["ratio"], 2) == 0.99


def test_atr_floor_ratio_at_or_above_1_books(tmp_path):
    now = T(14, 42, 33)
    _book_dir(tmp_path, now)
    at_floor = MSFT_STOP + MSFT_ATR                      # ratio exactly 1.0 -> books
    assert sizing.atr_floor_check(at_floor, MSFT_STOP, MSFT_ATR)["ok"]
    specs = [sizing.parse_leg(x) for x in SPECS]
    r = sizing.book_package(specs, data_dir=str(tmp_path), now_fn=lambda: now,
                            quote_fn=_quotes(505.50, T(14, 42, 30)).get)   # 12.8674 / 11.7724 = 1.093
    legs = {l["symbol"]: l for l in r["package"]["legs"]}
    assert legs["MSFT"]["atr_check"]["ok"] and legs["MSFT"]["atr_check"]["ratio"] >= 1.0
    assert [f["symbol"] for f in r["fills"]] == ["TSM", "WMT", "MSFT"] and r["skips"] == []
    port = json.loads((tmp_path / "portfolio.json").read_text())["positions"]
    assert port["MSFT"]["quantity"] == 1 and port["MSFT"]["stop_loss"] == MSFT_STOP


def test_book_requires_atr_fail_closed(tmp_path):
    now = T(14, 42, 33)
    _book_dir(tmp_path, now)
    r = sizing.book_package([sizing.parse_leg(f"MSFT:1:{MSFT_STOP}:563.2448")], data_dir=str(tmp_path),
                            now_fn=lambda: now, quote_fn=_quotes(520.0, T(14, 42, 30)).get)
    assert not r["ok"] and r["fills"] == [] and r["skips"][0]["reason"] == "atr_missing"
    assert "atr_missing" in r["reason"]


def test_floor_is_a_named_config_constant(monkeypatch):
    assert config.MIN_STOP_ATR_AT_FILL == 1.0
    assert sizing.atr_floor_check(505.50, MSFT_STOP, MSFT_ATR)["ok"]
    monkeypatch.setattr(config, "MIN_STOP_ATR_AT_FILL", 1.2)     # Risk raises the floor
    c = sizing.atr_floor_check(505.50, MSFT_STOP, MSFT_ATR)
    assert not c["ok"] and c["floor"] == 1.2 and c["reason"] == "atr_floor"


def test_dry_run_msft_1442_refuses_and_writes_nothing(tmp_path):
    now = T(14, 42, 33)
    _book_dir(tmp_path, now)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    r = sizing.book_package([sizing.parse_leg(SPECS[2])], data_dir=str(tmp_path), dry_run=True,
                            now_fn=lambda: now, quote_fn=_quotes(MSFT_FILL, T(14, 42, 30)).get)
    assert not r["ok"] and r["package"]["legs"][0]["qty"] == 0 and r["skips"][0]["reason"] == "atr_floor"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_sizing_cli_with_atr_shows_qty_0(capsys):
    rc = sizing.main(["package", "--gbpusd", str(FX), "--leg", f"MSFT:1:{MSFT_STOP}",
                      "--atr", f"MSFT={MSFT_ATR}", "--price", f"MSFT={MSFT_FILL}"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["legs"][0]["qty"] == 0 and out["skips"][0]["reason"] == "atr_floor"


def _fixture(sym, since, now):
    b = load_day_bars(sym, "2026-09-25", allow_fetch=False)
    return b[(b.index >= since) & (b.index <= now)]


def test_lse_leg_refused_on_atr_floor_before_booking(tmp_path):
    L = lambda h, m: datetime(2026, 9, 25, h, m, tzinfo=LONDON)
    (tmp_path / "monitor_heartbeat.json").write_text(json.dumps({"at": L(8, 36).isoformat(), "pid": 1}))
    # SHEL 08:06 open ~36.045, stop 34.7739 -> distance ~1.27; an ATR of 1.5 makes it ~0.85x
    r = lse_leg.book_lse_leg("SHEL", L(8, 6), 18, 34.7739, 36.11, 38.7822, data_dir=str(tmp_path),
                             bars_fn=_fixture, now_fn=lambda: L(8, 36), atr=1.5)
    assert not r["ok"] and r["atr_check"]["reason"] == "atr_floor" and r["atr_check"]["ratio"] < 1.0
    assert not (tmp_path / "trades.json").exists() or "SHEL" not in (tmp_path / "trades.json").read_text()
    sk = [json.loads(l) for l in (tmp_path / "alerts_log.jsonl").read_text().splitlines()]
    assert sk[-1]["type"] == "skip" and sk[-1]["details"]["reason"] == "atr_floor"
    r = lse_leg.book_lse_leg("SHEL", L(8, 6), 18, 34.7739, 36.11, 38.7822, data_dir=str(tmp_path),
                             bars_fn=_fixture, now_fn=lambda: L(8, 36))         # no ATR -> refused
    assert not r["ok"] and r["atr_check"]["reason"] == "atr_missing"
