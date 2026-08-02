"""Safe GC for the cc-communicate data root (HP-08 / D10).

WHITELIST: the ONLY artifacts GC may ever touch. Everything else (pipe/,
log/ - unacked messages and conversation records) is NEVER deleted. The
whitelist is structural: collect_candidates() enumerates exactly these three
roots, and run_gc() re-checks every candidate path for pipe/log components
(violations are skipped + reported).

  session_ctrl/*.json     >= 7 days   (processed start/end events; replay of
                                       a >7d-old event is a no-op - sessions
                                       .json already holds ended_at)
  pending_spawn/*.json    > TTL       (poisoned spawn markers; TTL default
                                       1h, CC_COMMUNICATE_PENDING_SPAWN_TTL_
                                       SECONDS)
  queue/responses/*.json  >= 7 days   (request ids are uuid4, never re-polled
                                       - each retry generates a fresh rid)

Minimum age is the race guard: nothing younger than its threshold is ever
touched, so out-of-process writers (registrar.js writing session_ctrl) can't
lose a file they just wrote. GC runs in the kernel's single thread - no
intra-kernel races. Deletions are best-effort: per-file OSError -> details,
never raised (a GC failure must not take down the kernel).
"""
from __future__ import annotations

import json
import os
import time

import fileutil
from paths import (
    PENDING_SPAWN_DIR, QUEUE_RESPONSES_DIR, SESSION_CTRL_DIR, GC_STATE_FILE,
)

PENDING_SPAWN_TTL_SECONDS = float(
    os.environ.get("CC_COMMUNICATE_PENDING_SPAWN_TTL_SECONDS", "3600"))
SESSION_CTRL_MAX_AGE_SECONDS = 7 * 24 * 3600
RESPONSES_MAX_AGE_SECONDS = 7 * 24 * 3600
GC_INTERVAL_SECONDS = 24 * 3600
_FORBIDDEN_COMPONENTS = ("pipe", "log")


def _age_seconds(path: str) -> float:
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return float("inf")  # missing/unreadable -> beyond any threshold


def pending_marker_expired(path: str) -> bool:
    """True when a pending_spawn marker is older than the TTL. Freshness
    comes from the marker's created_at_ms (authoritative - written at
    spawn); a marker without it (older producers) falls back to file mtime."""
    age = None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        ts = data.get("created_at_ms")
        if isinstance(ts, (int, float)) and ts > 0:
            age = time.time() - ts / 1000.0
    except (OSError, ValueError):
        pass
    if age is None:
        age = _age_seconds(path)
    return age > PENDING_SPAWN_TTL_SECONDS


def _candidates_older(root: str, max_age: float) -> list:
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return []
    return [os.path.join(root, n) for n in names
            if n.endswith(".json")
            and _age_seconds(os.path.join(root, n)) >= max_age]


def collect_candidates() -> dict:
    """Whitelist scan: {kind: [abs paths]} eligible for deletion. Enumerates
    ONLY the three whitelisted roots - pipe/ and log/ are never listed."""
    out = {
        "session_ctrl": _candidates_older(SESSION_CTRL_DIR,
                                          SESSION_CTRL_MAX_AGE_SECONDS),
        "responses": _candidates_older(QUEUE_RESPONSES_DIR,
                                       RESPONSES_MAX_AGE_SECONDS),
    }
    pending = []
    try:
        names = os.listdir(PENDING_SPAWN_DIR)
    except FileNotFoundError:
        names = []
    for n in names:
        if n.endswith(".json") and \
                pending_marker_expired(os.path.join(PENDING_SPAWN_DIR, n)):
            pending.append(os.path.join(PENDING_SPAWN_DIR, n))
    out["pending_spawn"] = pending
    return out


def run_gc(dry_run: bool = False) -> dict:
    """Delete all whitelisted candidates. dry_run: report, delete nothing.
    Returns {"deleted", "dry_run", "violations", "details"} - never raises."""
    violations, deleted, details = [], 0, []
    for kind, paths in collect_candidates().items():
        for path in paths:
            parts = path.replace(os.sep, "/").split("/")
            if any(comp in _FORBIDDEN_COMPONENTS for comp in parts):
                violations.append(path)  # guardrail - should be impossible
                continue
            if dry_run:
                details.append({"kind": kind, "path": path, "dry_run": True})
                continue
            try:
                os.remove(path)
                deleted += 1
                details.append({"kind": kind, "path": path, "deleted": True})
            except OSError as e:
                details.append({"kind": kind, "path": path, "error": str(e)})
    return {"deleted": deleted, "dry_run": bool(dry_run),
            "violations": violations, "details": details}


def load_last_gc_run() -> float | None:
    try:
        with open(GC_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        ts = data.get("last_run_at")
        return float(ts) if isinstance(ts, (int, float)) else None
    except (OSError, ValueError):
        return None


def save_last_gc_run(ts: float):
    try:
        os.makedirs(os.path.dirname(GC_STATE_FILE), exist_ok=True)
        fileutil.atomic_write_json(
            GC_STATE_FILE, {"schema_version": 1, "last_run_at": ts})
    except OSError:
        pass  # best-effort: a failed state write just re-runs GC next time


def gc_due(last_run_at: float | None) -> bool:
    if last_run_at is None:
        return True
    return time.time() - last_run_at >= GC_INTERVAL_SECONDS


def maybe_run_gc() -> dict | None:
    """Run GC when due (never ran, or last run >= GC_INTERVAL_SECONDS ago).
    Returns the run_gc result, or None when skipped. Never raises."""
    try:
        last = load_last_gc_run()
        if not gc_due(last):
            return None
        res = run_gc()
        save_last_gc_run(time.time())
        return res
    except Exception:
        return None
