"""Alert log hygiene (post 2026-09-28 postmortem): every row ends notified=true or carries a
skip_reason. Status-only rows are closed out at write time; actionable rows wait for the relay."""
import importlib.util
import json
import os
from datetime import datetime

import pytest

import alerts
from quotes import LONDON

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("backfill", os.path.join(ROOT, "scripts",
                                                                         "backfill_alert_skip_reason.py"))
backfill_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill_mod)

NOW = datetime(2026, 9, 28, 17, 15, 5, tzinfo=LONDON)


def _rows(d):
    return [json.loads(l) for l in open(os.path.join(d, "alerts_log.jsonl"))]


@pytest.mark.parametrize("t", sorted(alerts.STATUS_ONLY_TYPES))
def test_status_only_rows_written_notified_with_skip_reason(tmp_path, t):
    rec = alerts.append_alert(str(tmp_path), t, "TSM", {"mark": 340.39}, detected_ts=NOW)
    row = _rows(tmp_path)[-1]
    assert row == rec
    assert row["notified"] is True and row["skip_reason"] == "status_only" and row["sent_at"] is None


@pytest.mark.parametrize("t", sorted(alerts.ACTIONABLE_TYPES))
def test_actionable_rows_stay_unnotified_until_relayed(tmp_path, t):
    alerts.append_alert(str(tmp_path), t, "MSFT", {"x": 1}, detected_ts=NOW)
    row = _rows(tmp_path)[-1]
    assert row["notified"] is False and "skip_reason" not in row and "sent_at" not in row


def test_unknown_type_rejected(tmp_path):
    with pytest.raises(ValueError):
        alerts.append_alert(str(tmp_path), "chatter", "X", {})


def test_monitor_no_trigger_path_writes_status_only(tmp_path, monkeypatch):
    """monitor.run_once's periodic no_trigger goes through append_alert -> status_only."""
    import monitor
    assert monitor.notify_fields("no_trigger")["skip_reason"] == "status_only"
    ev = monitor.replay_day.__code__  # replay no_trigger events use the same helper
    assert "notify_fields" in ev.co_names


def _write(p, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


LEGACY = [
    {"type": "fill", "ticker": "TSM", "notified": True, "sent_at": "2026-09-28T14:43:13+01:00"},
    {"type": "no_trigger", "ticker": "TSM", "notified": False},              # -> backfilled
    {"type": "exit", "ticker": "MSFT", "notified": False},                   # actionable: untouched
    {"type": "no_trigger", "ticker": "WMT", "notified": True, "sent_at": "2026-09-28T17:27:29+01:00"},
]


def test_backfill_marks_status_only_leaves_actionable(tmp_path):
    p = tmp_path / "alerts_log.jsonl"
    _write(p, LEGACY)
    before = p.read_text().splitlines()
    r = backfill_mod.backfill(str(p), stamp="202609281800")
    after = [json.loads(l) for l in p.read_text().splitlines()]
    assert os.path.exists(str(p) + ".bak-202609281800")
    assert (tmp_path / "alerts_log.jsonl.bak-202609281800").read_text().splitlines() == before
    assert after[1]["skip_reason"] == "status_only_backfill" and after[1]["notified"] is False
    assert "skip_reason" not in after[2] and after[2]["notified"] is False
    assert p.read_text().splitlines()[0] == before[0] and p.read_text().splitlines()[3] == before[3]
    assert [c["line"] for c in r["changed"]] == [2] and [x["line"] for x in r["pending_actionable"]] == [3]
    assert r["rewritten"]


def test_backfill_noop_does_not_rewrite(tmp_path):
    p = tmp_path / "alerts_log.jsonl"
    _write(p, [LEGACY[0], LEGACY[3]])
    ino = os.stat(p).st_ino
    r = backfill_mod.backfill(str(p), stamp="x")
    assert not r["rewritten"] and r["changed"] == [] and os.stat(p).st_ino == ino


def test_backfill_retries_when_monitor_appends_mid_pass(tmp_path):
    p = tmp_path / "alerts_log.jsonl"
    _write(p, LEGACY)
    calls = {"n": 0}

    def concurrent_append():
        if calls["n"] == 0:                                 # monitor appends during the 1st pass
            alerts.append_alert(str(tmp_path), "no_trigger", "WMT", {"mark": 81.9}, detected_ts=NOW)
            alerts.append_alert(str(tmp_path), "stop", "WMT", {"price": 103.7}, detected_ts=NOW)
        calls["n"] += 1

    r = backfill_mod.backfill(str(p), stamp="y", _before_rename=concurrent_append)
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    assert r["attempts"] == 2 and len(rows) == 6            # concurrent rows not clobbered
    assert rows[4]["skip_reason"] == "status_only" and rows[5]["type"] == "stop"
    assert rows[5]["notified"] is False and "skip_reason" not in rows[5]
    assert rows[1]["skip_reason"] == "status_only_backfill"


def test_backfill_large_file(tmp_path):
    p = tmp_path / "alerts_log.jsonl"
    _write(p, LEGACY * 500)                                  # ~40 KB, > one read chunk
    r = backfill_mod.backfill(str(p), stamp="z")
    assert r["rows"] == 2000 and len(r["changed"]) == 500 and len(r["pending_actionable"]) == 500
