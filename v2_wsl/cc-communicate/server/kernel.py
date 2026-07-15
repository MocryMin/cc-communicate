"""cc-communicate upper-layer kernel - a lazy-started, backoff-loop daemon.

Started on demand by check_core.ensure_core(). Single instance is enforced by
check_core's file lock; this process just runs once spawned.

v2 changes vs v0.1:
  - On init, load/generate machine_identity (type, id, claude_bin) - used to
    stamp the `machine` field on local sessions and (on WSL) to spawn the right
    claude binary.
  - _handle_start records `machine` = local type on each session_inf /
    alive_sessions entry (v2.1 §3.2.1).
  - dispatch routes the new kernel functions: spawn_cc_new, spawn_cc_resume,
    create_conversation_folder, kernel_terminate. arm_poller dispatch REMOVED.

Lifecycle (core_plan #11): INIT (load sessions, replay event log, signal READY)
-> LOOP (backoff 1ms..1s; replay events, drain queue) -> EXIT (alive_conversations
empty AND idle_timeout AND queue empty; or SIGINT/SIGTERM; or kernel_terminate).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time

import kernel_api
import machine_identity
from paths import (
    CORE_STATUS_FILE, SERVER_DATA_DIR, TERMINATE_FLAG,
    SESSION_CTRL_DIR, QUEUE_DIR, QUEUE_RESPONSES_DIR, SESSIONS_FILE,
    ensure_runtime_dirs,
)
from proc import proc_start_time, parse_start_time

_IDLE_TIMEOUT = float(os.environ.get("CC_MONITOR_IDLE_TIMEOUT", "600"))
_BASE_SLEEP = 0.001
_IDLE_CYCLES_BEFORE_BACKOFF = 10000
_MAX_SLEEP = 1.0

_seen_events: set[str] = set()
sessions: dict = {}
alive_sessions: dict = {}
alive_conversations: dict = {}
_last_activity: float = 0.0

_exit_requested = False
log = logging.getLogger("cc-communicate.kernel")
_local_machine_type: str = "unknown"


def _atomic_write_json(path: str, obj):
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


def _write_core_status(status: int):
    _atomic_write_json(CORE_STATUS_FILE, {
        "status": status,
        "pid": os.getpid(),
        "start_time": proc_start_time(os.getpid()),
    })


def _load_sessions():
    data = _read_json(SESSIONS_FILE)
    if isinstance(data, dict):
        sessions.update(data)
        log.info("loaded sessions.json: %d sessions", len(sessions))


def _save_sessions():
    _atomic_write_json(SESSIONS_FILE, sessions)


def process_session_ctrl_event() -> bool:
    try:
        files = os.listdir(SESSION_CTRL_DIR)
    except FileNotFoundError:
        return False
    new_names = [f for f in files if f.endswith(".json") and f not in _seen_events]
    if not new_names:
        return False
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
    existing = sessions.get(sid, {})
    sessions[sid] = {
        "session_id": sid,
        "pid": ev.get("pid"),
        "cwd": ev.get("cwd"),
        "start_time": ev.get("start_time"),
        "start_time_epoch": parse_start_time(ev.get("start_time")),
        "source": ev.get("source"),
        "started_at": ev.get("event_ts"),
        "ended_at": None,
        "first_seen": existing.get("first_seen", ev.get("event_ts")),
        "machine": _local_machine_type,  # v2: stamp local machine type (§3.2.1)
    }
    alive_sessions[sid] = {
        "pid": ev.get("pid"),
        "start_time": parse_start_time(ev.get("start_time")),
        "cwd": ev.get("cwd"),
        "machine": _local_machine_type,
    }


def _handle_end(ev: dict, sid: str):
    alive_sessions.pop(sid, None)
    if sid in sessions:
        sessions[sid]["ended_at"] = ev.get("event_ts")


def drain_queue() -> bool:
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
            os.makedirs(QUEUE_RESPONSES_DIR, exist_ok=True)
            _atomic_write_json(os.path.join(QUEUE_RESPONSES_DIR, rid + ".json"), resp)
        try:
            os.remove(path)
        except OSError:
            pass
    return bool(reqs)


def _dispatch(function: str, args: dict):
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
    if function == "collect_messages":
        return kernel_api.collect_messages(args["session_id"])
    if function == "session_by_pid":
        return kernel_api.session_by_pid(sessions, args["pid"])
    if function == "find_new_session":
        return kernel_api.find_new_session(sessions, args["cwd"], args.get("since_ts", 0))
    if function == "spawn_cc_new":
        return kernel_api.spawn_cc_new(args["cwd"], args["prompt"])
    if function == "spawn_cc_resume":
        return kernel_api.spawn_cc_resume(args["session_id"], args["prompt"])
    if function == "create_conversation_folder":
        return kernel_api.create_conversation_folder(args["id1"], args["id2"])
    if function == "kernel_terminate":
        return kernel_api.kernel_terminate()
    # arm_poller dispatch REMOVED (v2.2 Amd3)
    raise ValueError(f"unknown kernel function: {function}")


def _queue_has_pending() -> bool:
    try:
        return any(f.endswith(".json") for f in os.listdir(QUEUE_DIR))
    except FileNotFoundError:
        return False


def _should_exit() -> bool:
    if _exit_requested:
        return True
    if os.path.exists(TERMINATE_FLAG):
        return True
    if alive_conversations:
        return False
    if time.monotonic() - _last_activity < _IDLE_TIMEOUT:
        return False
    if _queue_has_pending():
        return False
    return True


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


def main():
    global _last_activity, _local_machine_type
    _setup_logging()
    _install_signal_handlers()
    ensure_runtime_dirs()
    # v2: establish machine identity (creates machine_identity.json on first
    # run, detects type + claude_bin). Stamps local sessions with `machine`.
    _local_machine_type = machine_identity.load_or_create().get("type", "unknown")
    log.info("kernel starting (pid=%d, machine=%s, idle_timeout=%ss)",
             os.getpid(), _local_machine_type, _IDLE_TIMEOUT)

    _load_sessions()
    process_session_ctrl_event()
    _write_core_status(1)
    log.info("kernel READY - %d sessions known, %d alive", len(sessions), len(alive_sessions))
    _last_activity = time.monotonic()

    sleep = _BASE_SLEEP
    idle = 0
    try:
        while True:
            ev_busy = process_session_ctrl_event()
            q_busy = drain_queue()
            if q_busy:
                _last_activity = time.monotonic()
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
        try:
            os.remove(TERMINATE_FLAG)
        except OSError:
            pass
        log.info("kernel exiting - writing status=0, saving sessions.json")
        _write_core_status(0)
        _save_sessions()
        log.info("kernel exited")


if __name__ == "__main__":
    main()
