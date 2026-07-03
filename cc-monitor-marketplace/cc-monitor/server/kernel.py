"""cc-monitor upper-layer kernel — a lazy-started, backoff-loop daemon.

Started on demand by check_core.ensure_core(). Single instance is enforced by
check_core's file lock; this process just runs once spawned.

Lifecycle (core_plan #11):
  INIT:  ensure dirs -> load alive_sessions snapshot -> write core_status.json
         {status:1, pid, start_time} (the READY signal check_core waits for).
  LOOP:  backoff 1ms..1s (core_plan "退避的循环"). Each cycle:
           - process_session_ctrl_event(): replay new event-log files.
           - [TODO] drain queue: dispatch request files to kernel functions.
  EXIT:  when alive_conversations empty AND idle_timeout since last conversation
         / queue activity AND queue empty (core_plan #11c). Writes status=0,
         persists alive_sessions snapshot, exits. SIGINT/SIGTERM also trigger a
         clean exit (status=0 + snapshot).

SCOPE OF THIS FILE: lifecycle skeleton only.
  - process_session_ctrl_event() is minimal: marks events seen + logs them. The
    full replay (start/end -> alive_sessions + sessions.json) is the NEXT
    increment.
  - Queue dispatch is a TODO stub.
  - The real kernel functions (query_session, check_alive, evoke, withdraw) and
    user-function RPC come after the lifecycle is verified end-to-end.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time

from paths import (
    CORE_STATUS_FILE, ALIVE_SNAPSHOT_FILE, SERVER_DATA_DIR,
    SESSION_CTRL_DIR, QUEUE_DIR, ensure_runtime_dirs,
)
from proc import proc_start_time

# Idle exit timeout (seconds). Default 10 min per core_plan; override via env
# for testing, e.g. CC_MONITOR_IDLE_TIMEOUT=10.
_IDLE_TIMEOUT = float(os.environ.get("CC_MONITOR_IDLE_TIMEOUT", "600"))

# Backoff parameters.
_BASE_SLEEP = 0.001                  # 1 kHz
_IDLE_CYCLES_BEFORE_BACKOFF = 10000  # consecutive idle cycles before *10
_MAX_SLEEP = 1.0

# In-memory state.
_seen_events: set[str] = set()       # event filenames already processed (memory only)
alive_sessions: dict = {}            # session_id -> {pid, start_time(epoch), cwd, ...}
alive_conversations: dict = {}       # (sid_a, sid_b) -> conv info  [no p2p yet; empty]
_last_activity: float = 0.0          # monotonic time of last conversation/queue activity

_exit_requested = False
log = logging.getLogger("cc-monitor.kernel")


# ---------- atomic JSON helpers ----------

def _atomic_write_json(path: str, obj):
    """Write JSON via temp file + os.replace (atomic rename on same filesystem)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ---------- core_status.json (READY signal / exit marker) ----------

def _write_core_status(status: int):
    _atomic_write_json(CORE_STATUS_FILE, {
        "status": status,
        "pid": os.getpid(),
        "start_time": proc_start_time(os.getpid()),
    })


# ---------- alive_sessions snapshot (persist on exit, load on init) ----------

def _load_snapshot():
    snap = _read_json(ALIVE_SNAPSHOT_FILE)
    if isinstance(snap, dict):
        alive_sessions.update(snap)
        log.info("loaded alive_sessions snapshot: %d sessions", len(snap))


def _persist_snapshot():
    _atomic_write_json(ALIVE_SNAPSHOT_FILE, alive_sessions)
    log.info("persisted alive_sessions snapshot: %d sessions", len(alive_sessions))


# ---------- process_session_ctrl_event (MINIMAL — full replay is next increment) ----------

def process_session_ctrl_event() -> bool:
    """Scan data/session_ctrl/ for event files not yet seen, in lexical
    (chronological) order, and process them.

    Minimal version: mark seen + log. The full version will replay start events
    into alive_sessions + sessions.json and end events into removal, per
    core_plan "内核函数 1".

    Returns True if any new event was processed (resets backoff). Note: session
    events do NOT update _last_activity — only conversation/queue activity does
    (the idle-exit condition is about conversations, not session presence)."""
    try:
        files = sorted(os.listdir(SESSION_CTRL_DIR))
    except FileNotFoundError:
        return False
    new = [f for f in files if f.endswith(".json") and f not in _seen_events]
    for f in new:
        _seen_events.add(f)
        ev = _read_json(os.path.join(SESSION_CTRL_DIR, f))
        if ev:
            log.info("event %s: kind=%s sid=%s", f, ev.get("event"), ev.get("session_id"))
    return bool(new)


# ---------- exit condition (core_plan #11c) ----------

def _queue_has_pending() -> bool:
    try:
        return any(f.endswith(".json") for f in os.listdir(QUEUE_DIR))
    except FileNotFoundError:
        return False


def _should_exit() -> bool:
    if _exit_requested:
        return True
    if alive_conversations:
        return False
    if time.monotonic() - _last_activity < _IDLE_TIMEOUT:
        return False
    if _queue_has_pending():
        return False  # exit-race mitigation: never exit with work queued
    return True


# ---------- setup ----------

def _setup_logging():
    os.makedirs(SERVER_DATA_DIR, exist_ok=True)
    log_path = os.path.join(SERVER_DATA_DIR, "kernel.log")
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _install_signal_handlers():
    def _req(signum, frame):
        global _exit_requested
        _exit_requested = True
        log.info("signal %s received -> requesting exit", signum)
    signal.signal(signal.SIGINT, _req)
    signal.signal(signal.SIGTERM, _req)


# ---------- main ----------

def main():
    global _last_activity
    _setup_logging()
    _install_signal_handlers()
    ensure_runtime_dirs()
    log.info("kernel starting (pid=%d, idle_timeout=%ss)", os.getpid(), _IDLE_TIMEOUT)

    # INIT: load snapshot, THEN signal READY (check_core is polling for this).
    _load_snapshot()
    _write_core_status(1)
    log.info("kernel READY — core_status.json written")
    _last_activity = time.monotonic()

    # LOOP with backoff.
    sleep = _BASE_SLEEP
    idle = 0
    try:
        while True:
            busy = process_session_ctrl_event()
            # TODO: drain queue — dispatch request files to kernel functions.
            #       A processed request updates _last_activity (queue activity).
            if busy:
                sleep = _BASE_SLEEP
                idle = 0
            else:
                idle += 1
                if idle >= _IDLE_CYCLES_BEFORE_BACKOFF:
                    sleep = min(sleep * 10, _MAX_SLEEP)
                    idle = 0
            if _should_exit():
                break
            time.sleep(sleep)
    except Exception:
        log.exception("kernel crashed")
        raise
    finally:
        log.info("kernel exiting — writing status=0, persisting snapshot")
        _write_core_status(0)
        _persist_snapshot()
        log.info("kernel exited")


if __name__ == "__main__":
    main()
