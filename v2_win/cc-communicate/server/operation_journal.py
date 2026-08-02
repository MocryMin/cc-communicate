"""Operation journal (HP-03): bounded, persistent record of completed
mutations keyed by operation_id.

    request_id   = one transport attempt (unique per queue submission)
    operation_id = stable identity of ONE logical operation across all retries

A retry whose operation_id is journaled as completed REPLAYS the recorded
result WITHOUT re-executing the side effect. The journal is the fast path;
the domain objects (message_id in filenames now, spawn_token registry in
Wave 2) are the crash-surviving source of truth - after a crash between
side-effect and journal write, domain dedup (not the journal) prevents a
duplicate. That residual crash-window is documented in the master plan R-list.

Bounds: TTL 24h + max 1000 entries, pruned on every save. Only "completed"
entries are ever recorded in Wave 1; the prune rules never remove a
non-completed entry (future-proof).

Pure module: the journal file path comes in as a parameter (test isolation).
"""
from __future__ import annotations

import json
import time

import fileutil

SCHEMA_VERSION = 1
TTL_MS = 24 * 3600 * 1000
MAX_ENTRIES = 1000


def load(path: str, data=None) -> dict:
    """-> {operation_id: entry}. Tolerant: any read problem -> empty journal.
    `data` may be passed pre-read (the kernel applies the HP-11 schema guard
    before calling); None -> read the file."""
    if data is None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
    if isinstance(data, dict) and isinstance(data.get("operations"), dict):
        return data["operations"]
    return {}


def save(path: str, operations: dict):
    _prune(operations)
    fileutil.atomic_write_json(
        path, {"schema_version": SCHEMA_VERSION, "operations": operations})


def completed_result(operations: dict, operation_id: str):
    """-> (hit: bool, result). Only completed entries replay."""
    entry = operations.get(operation_id)
    if isinstance(entry, dict) and entry.get("status") == "completed":
        return True, entry.get("result")
    return False, None


def record_completed(operations: dict, operation_id: str, function: str, result):
    operations[operation_id] = {
        "function": function,
        "status": "completed",
        "result": result,
        "completed_at_ms": int(time.time() * 1000),
    }


def _prune(operations: dict):
    now = int(time.time() * 1000)
    stale = [k for k, e in operations.items()
             if isinstance(e, dict) and e.get("status") == "completed"
             and now - int(e.get("completed_at_ms", 0) or 0) > TTL_MS]
    for k in stale:
        del operations[k]
    if len(operations) > MAX_ENTRIES:
        completed_list = sorted(
            (int(e.get("completed_at_ms", 0) or 0), k)
            for k, e in operations.items()
            if isinstance(e, dict) and e.get("status") == "completed")
        for _ts, k in completed_list[:len(operations) - MAX_ENTRIES]:
            del operations[k]
