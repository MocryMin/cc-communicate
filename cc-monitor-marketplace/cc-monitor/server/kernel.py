"""cc-monitor upper-layer kernel — a lazy-started, backoff-loop daemon.

Started on demand by check_core.ensure_core(). Single instance is enforced by
check_core's file lock; this process just runs once spawned.

Lifecycle (core_plan #11):
  INIT:  load sessions.json -> replay session_ctrl event log (rebuilds
         alive_sessions + catches up sessions.json) -> write core_status.json
         {status:1, pid, start_time} (READY signal).
  LOOP:  backoff 1ms..1s. Each cycle:
           - process_session_ctrl_event(): replay new event-log files into
             sessions + alive_sessions.
           - drain_queue(): dispatch RPC requests to kernel_api, write responses.
  EXIT:  when alive_conversations empty AND idle_timeout since last queue
         activity AND queue empty (core_plan #11c). Writes status=0, saves
         sessions.json, exits. SIGINT/SIGTERM also trigger clean exit.

State:
  - sessions.json (persistent): registry of all known sessions. Loaded on init,
    saved after each event batch and on exit. Upsert on start; mark ended on end.
  - alive_sessions (in-memory): session_id -> {pid, start_time(epoch), cwd}.
    Rebuilt from the event log on every init (event log is ground truth). The
    plan's snapshot optimization (persist + incremental replay via watermark)
    is deferred — replay-all is simple and fast for current event volumes.

Kernel functions (dispatched via queue RPC, see kernel_api.py):
  - query_session, check_alive  [implemented]
  - evoke, withdraw, ...        [later increments]
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time

import kernel_api
from paths import (
    CORE_STATUS_FILE, SERVER_DATA_DIR,
    SESSION_CTRL_DIR, QUEUE_DIR, QUEUE_RESPONSES_DIR, SESSIONS_FILE,
    ensure_runtime_dirs,
)
from proc import proc_start_time, parse_start_time

# Idle exit timeout (seconds). Default 10 min per core_plan; override via env.
_IDLE_TIMEOUT = float(os.environ.get("CC_MONITOR_IDLE_TIMEOUT", "600"))

# Backoff parameters (core_plan "退避的循环").
_BASE_SLEEP = 0.001                  # 1 kHz
_IDLE_CYCLES_BEFORE_BACKOFF = 10000  # consecutive idle cycles before *10
_MAX_SLEEP = 1.0

# In-memory state.
_seen_events: set[str] = set()       # event filenames already processed (memory only)
sessions: dict = {}                  # session_id -> session_inf  (mirror of sessions.json)
alive_sessions: dict = {}            # session_id -> {pid, start_time(epoch), cwd}
alive_conversations: dict = {}       # (sid_a, sid_b) -> conv info  [no p2p yet; empty]
_last_activity: float = 0.0          # monotonic time of last queue activity

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


# ---------- sessions.json (persistent registry) ----------

def _load_sessions():
    data = _read_json(SESSIONS_FILE)
    if isinstance(data, dict):
        sessions.update(data)
        log.info("loaded sessions.json: %d sessions", len(sessions))


def _save_sessions():
    _atomic_write_json(SESSIONS_FILE, sessions)


# ---------- process_session_ctrl_event (full replay) ----------

def process_session_ctrl_event() -> bool:
    """Replay new session_ctrl event files into sessions + alive_sessions,
    processed in event_ts order. NOT filename order — 'end_' sorts before
    'start_' alphabetically, so filename sort is not chronological; the lower
    layer's contract (README §1) explicitly requires sorting by event_ts.
    Persists sessions.json after each batch.

    Returns True if any new event was processed (resets backoff). Note: session
    events do NOT update _last_activity — only queue activity delays idle exit
    (the exit condition is about conversations, not session presence)."""
    try:
        files = os.listdir(SESSION_CTRL_DIR)
    except FileNotFoundError:
        return False
    new_names = [f for f in files if f.endswith(".json") and f not in _seen_events]
    if not new_names:
        return False
    # Read each new event to get its event_ts, then sort chronologically.
    pending = []
    for f in new_names:
        ev = _read_json(os.path.join(SESSION_CTRL_DIR, f))
        _seen_events.add(f)
        ts = ev.get("event_ts", 0) if ev else 0
        pending.append((ts, f, ev))
    pending.sort(key=lambda x: x[0])
    for ts, f, ev in pending:
        if not ev:
            continue
        kind = ev.get("event")
        sid = ev.get("session_id")
        if not sid:
            continue
        if kind == "start":
            _handle_start(ev, sid)
        elif kind == "end":
            _handle_end(ev, sid)
        log.info("event %s: kind=%s sid=%s ts=%s", f, kind, sid, ts)
    _save_sessions()
    return True


def _handle_start(ev: dict, sid: str):
    """Upsert the session record (latest info wins; first_seen preserved) and
    mark it alive."""
    existing = sessions.get(sid, {})
    sessions[sid] = {
        "session_id": sid,
        "pid": ev.get("pid"),
        "cwd": ev.get("cwd"),
        "start_time": ev.get("start_time"),                         # ISO (as written by proc.js)
        "start_time_epoch": parse_start_time(ev.get("start_time")),  # epoch (parsed)
        "source": ev.get("source"),
        "started_at": ev.get("event_ts"),
        "ended_at": None,
        "first_seen": existing.get("first_seen", ev.get("event_ts")),  # preserve first discovery
    }
    alive_sessions[sid] = {
        "pid": ev.get("pid"),
        "start_time": parse_start_time(ev.get("start_time")),  # epoch for check_alive comparison
        "cwd": ev.get("cwd"),
    }


def _handle_end(ev: dict, sid: str):
    """Mark the session ended (kept in sessions.json; removed from alive_sessions)."""
    alive_sessions.pop(sid, None)
    if sid in sessions:
        sessions[sid]["ended_at"] = ev.get("event_ts")


# ---------- queue RPC dispatch ----------

def drain_queue() -> bool:
    """Process pending request files: dispatch to kernel_api, write responses,
    remove requests. Returns True if any request was processed."""
    try:
        files = sorted(os.listdir(QUEUE_DIR))
    except FileNotFoundError:
        return False
    reqs = [f for f in files if f.endswith(".json")]
    for fname in reqs:
        path = os.path.join(QUEUE_DIR, fname)
        req = _read_json(path)
        try:
            if not req or "function" not in req or "request_id" not in req:
                raise ValueError("malformed request")
            result = _dispatch(req["function"], req.get("args") or {})
            resp = {"request_id": req["request_id"], "result": result, "error": None}
        except Exception as e:
            log.exception("error handling request %s", fname)
            resp = {"request_id": req.get("request_id") if req else None,
                    "result": None, "error": f"{type(e).__name__}: {e}"}
        rid = resp["request_id"]
        if rid is not None:
            _atomic_write_json(os.path.join(QUEUE_RESPONSES_DIR, rid + ".json"), resp)
        try:
            os.remove(path)
        except OSError:
            pass
    return bool(reqs)


def _dispatch(function: str, args: dict):
    """Route a kernel function name to its implementation in kernel_api."""
    if function == "query_session":
        return kernel_api.query_session(sessions, args["session_id"])
    if function == "check_alive":
        return kernel_api.check_alive(alive_sessions, args["session_id"])
    if function == "query_conversations":
        return kernel_api.query_conversations(args["session_id"])
    if function == "send_message":
        return kernel_api.send_message(alive_conversations, args["fromid"], args["toid"], args["message"])
    if function == "register_conversation":
        kernel_api.register_conversation(alive_conversations, args["sid_a"], args["sid_b"])
        return "ok"
    if function == "unregister_conversation":
        kernel_api.unregister_conversation(alive_conversations, args["sid_a"], args["sid_b"])
        return "ok"
    if function == "withdraw":
        return kernel_api.withdraw(alive_conversations, args["fromid"], args["toid"], args.get("init_connect", 0))
    if function == "evoke":
        return kernel_api.evoke(sessions, args["session_id"])
    if function == "arm_poller":
        return kernel_api.arm_poller(args["session_id"], args.get("timeout", 1800))
    if function == "collect_messages":
        return kernel_api.collect_messages(args["session_id"])
    if function == "session_by_pid":
        return kernel_api.session_by_pid(sessions, args["pid"])
    if function == "find_new_session":
        return kernel_api.find_new_session(sessions, args["cwd"], args.get("since_ts", 0))
    raise ValueError(f"unknown kernel function: {function}")


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

    # INIT: load persistent registry, replay event log (rebuilds alive_sessions,
    # catches up sessions.json), THEN signal READY.
    _load_sessions()
    process_session_ctrl_event()
    _write_core_status(1)
    log.info("kernel READY — %d sessions known, %d alive", len(sessions), len(alive_sessions))
    _last_activity = time.monotonic()

    # LOOP with backoff.
    sleep = _BASE_SLEEP
    idle = 0
    try:
        while True:
            ev_busy = process_session_ctrl_event()
            q_busy = drain_queue()
            if q_busy:
                _last_activity = time.monotonic()  # queue activity delays idle exit
            if ev_busy or q_busy:
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
        log.info("kernel exiting — writing status=0, saving sessions.json")
        _write_core_status(0)
        _save_sessions()
        log.info("kernel exited")


if __name__ == "__main__":
    main()
