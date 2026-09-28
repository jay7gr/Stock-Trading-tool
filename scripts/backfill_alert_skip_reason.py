#!/usr/bin/env python3
"""One-off backfill for data/alerts_log.jsonl (post 2026-09-28 postmortem, alert hygiene).

Any row with notified != true, no skip_reason, and a STATUS-ONLY type (no_trigger, heartbeat,
monitor) gets skip_reason="status_only_backfill". Actionable rows (fill, exit, stop, target,
scale, halt, skip, failure) that are still notified=false are left untouched and listed.

Safety with a live monitor appending to the same file:
  * a backup copy data/alerts_log.jsonl.bak-YYYYMMDDHHMM is made first;
  * unchanged lines are kept byte-for-byte; the new file is written to a temp file in the same
    directory, fsync'd, and swapped in with os.replace (atomic rename);
  * right before the rename the file is re-read: if its line count / bytes changed (a concurrent
    append), the temp file is discarded and the whole pass retried;
  * an fd on the original inode is held across the rename: bytes appended to the old inode in the
    last instant (writer opened the path before the rename) are copied onto the new file.
  * if nothing needs changing, the file is not rewritten at all.

Usage: python scripts/backfill_alert_skip_reason.py [--path data/alerts_log.jsonl] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from alerts import BACKFILL_SKIP_REASON, STATUS_ONLY_TYPES  # noqa: E402


def _read_all(fd: int) -> bytes:
    chunks = []
    while True:
        b = os.read(fd, 1 << 16)
        if not b:
            return b"".join(chunks)
        chunks.append(b)


def _plan(raw: bytes):
    lines = raw.splitlines(keepends=True)
    if lines and not lines[-1].endswith(b"\n"):
        raise BlockingIOError("partial trailing line (append in progress)")
    out, changed, pending = [], [], []
    for i, ln in enumerate(lines):
        if not ln.strip():
            out.append(ln)
            continue
        rec = json.loads(ln)
        if rec.get("notified") is not True and not rec.get("skip_reason"):
            if rec.get("type") in STATUS_ONLY_TYPES:
                rec["skip_reason"] = BACKFILL_SKIP_REASON
                ln = (json.dumps(rec, default=str) + "\n").encode()
                changed.append({"line": i + 1, "type": rec.get("type"), "ticker": rec.get("ticker"),
                                "event_ts": rec.get("event_ts")})
            else:
                pending.append({"line": i + 1, "type": rec.get("type"), "ticker": rec.get("ticker"),
                                "event_ts": rec.get("event_ts"), "details": rec.get("details")})
        out.append(ln)
    return lines, b"".join(out), changed, pending


def backfill(path: str, *, stamp: str | None = None, dry_run: bool = False, retries: int = 10,
             _before_rename=None) -> dict:
    stamp = stamp or datetime.now().strftime("%Y%m%d%H%M")
    backup = f"{path}.bak-{stamp}"
    res = {"path": path, "backup": None, "rows": 0, "changed": [], "pending_actionable": [],
           "rewritten": False, "attempts": 0, "dry_run": dry_run}
    if not dry_run:
        shutil.copy2(path, backup)
        res["backup"] = backup
    d = os.path.dirname(os.path.abspath(path))
    for attempt in range(1, retries + 1):
        res["attempts"] = attempt
        orig_fd = os.open(path, os.O_RDONLY)
        try:
            raw = _read_all(orig_fd)
            try:
                lines, new, changed, pending = _plan(raw)
            except BlockingIOError:
                continue
            res.update(rows=len(lines), changed=changed, pending_actionable=pending)
            if not changed or dry_run:
                return res
            fd, tmp = tempfile.mkstemp(prefix=".alerts_log.", suffix=".tmp", dir=d)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(new)
                    f.flush()
                    os.fsync(f.fileno())
                shutil.copymode(path, tmp)
                if _before_rename:
                    _before_rename()
                with open(path, "rb") as f:                 # re-check right before the rename
                    now_raw = f.read()
                if now_raw.count(b"\n") != raw.count(b"\n") or now_raw != raw:
                    os.remove(tmp)
                    continue
                os.replace(tmp, path)
            except BaseException:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
            # bytes that landed on the old inode during the swap -> carry over
            size = os.fstat(orig_fd).st_size
            if size > len(raw):
                os.lseek(orig_fd, len(raw), os.SEEK_SET)
                extra = os.read(orig_fd, size - len(raw))
                with open(path, "ab") as f:
                    f.write(extra)
                res["carried_over_bytes"] = len(extra)
            res["rewritten"] = True
            return res
        finally:
            os.close(orig_fd)
    raise RuntimeError(f"alerts log kept changing; gave up after {retries} attempts")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", default=os.path.join(ROOT, "data", "alerts_log.jsonl"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(backfill(a.path, dry_run=a.dry_run), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
