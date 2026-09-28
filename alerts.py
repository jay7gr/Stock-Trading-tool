"""Append-only alert log: data/alerts_log.jsonl.

Each line: {event_ts, detected_ts, type, ticker, details, notified, [sent_at, skip_reason]}

Hygiene rule (post 2026-09-28 postmortem): every row ends up either notified=true or
carrying an explicit skip_reason, so nothing lingers past the 5-minute relay SLA.
  * ACTIONABLE types (fill, exit, stop, target, scale, halt, skip, failure) are written
    notified=false and stay that way until the relay sends them and stamps sent_at.
  * STATUS-ONLY types (no_trigger, heartbeat, monitor) are informational: they are written
    already closed out as notified=true, skip_reason="status_only", sent_at=null (nothing
    was sent) so the relay never picks them up.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

from quotes import now_london, to_london

ACTIONABLE_TYPES = {"fill", "exit", "stop", "target", "scale", "halt", "skip", "failure"}
STATUS_ONLY_TYPES = {"no_trigger", "heartbeat", "monitor"}
EVENT_TYPES = ACTIONABLE_TYPES | STATUS_ONLY_TYPES
STATUS_ONLY_SKIP_REASON = "status_only"
BACKFILL_SKIP_REASON = "status_only_backfill"


def is_status_only(type_: str) -> bool:
    return type_ in STATUS_ONLY_TYPES


def notify_fields(type_: str) -> dict:
    """Notification bookkeeping for a new row of this type (see module doc)."""
    if is_status_only(type_):
        return {"notified": True, "sent_at": None, "skip_reason": STATUS_ONLY_SKIP_REASON}
    return {"notified": False}


def append_alert(data_dir: str, type_: str, ticker: str, details: dict,
                 event_ts=None, detected_ts=None) -> dict:
    if type_ not in EVENT_TYPES:
        raise ValueError(f"unknown alert type {type_!r}")
    detected = to_london(detected_ts) if detected_ts else now_london()
    event = to_london(event_ts) if event_ts else detected
    rec = {
        "event_ts": event.isoformat(),
        "detected_ts": detected.isoformat(),
        "type": type_,
        "ticker": ticker,
        "details": details,
        **notify_fields(type_),
    }
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "alerts_log.jsonl"), "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec
