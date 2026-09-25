"""Append-only alert log: data/alerts_log.jsonl.

Each line: {event_ts, detected_ts, type, ticker, details, notified:false}
type is one of fill|stop|scale|target|exit|skip|no_trigger|halt.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

from quotes import now_london, to_london

EVENT_TYPES = {"fill", "stop", "scale", "target", "exit", "skip", "no_trigger", "halt"}


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
        "notified": False,
    }
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "alerts_log.jsonl"), "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec
