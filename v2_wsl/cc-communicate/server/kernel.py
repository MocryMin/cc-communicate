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
-> LOOP (backoff 1ms..1s; replay events, drain queue) -> EXIT (idle_timeout AND
queue empty - registration is NOT a lease (D10); or SIGINT/SIGTERM; or
kernel_terminate).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time

import cleanup
import kernel_api
import fileutil
import machine_identity
import operation_journal as operation_journal_mod
import validation
from paths import (
    CONVERSATIONS_DIR, CORE_STATUS_FILE, SERVER_DATA_DIR, TERMINATE_FLAG,
    SESSION_CTRL_DIR, QUEUE_DIR, QUEUE_RESPONSES_DIR, SESSIONS_FILE,
    ALIVE_CONVS_FILE, ACK_TIMESTAMPS_FILE, MESSAGE_SEQUENCE_FILE, CURSORS_FILE,
    OPERATION_JOURNAL_FILE, PENDING_SPAWN_DIR, ensure_runtime_dirs,
)
from proc import proc_start_time, parse_start_time

_IDLE_TIMEOUT = float(os.environ.get("CC_MONITOR_IDLE_TIMEOUT", "600"))
_BASE_SLEEP = 0.001
_IDLE_CYCLES_BEFORE_BACKOFF = 10000
# T24/B5: cut from 1.0s to 0.2s so a polling `listen` (one listen_scan rpc per
# cycle) wakes the kernel within ~0.2s instead of ~1s. Trades idle CPU for the
# ~5x lower listen latency the watermark-ACK poll loop needs.
_MAX_SLEEP = 0.2

_seen_events: set[str] = set()
sessions: dict = {}
alive_sessions: dict = {}
alive_conversations: dict = {}
acked_timestamps: dict = {}  # T24: sid -> latest confirmed ACK watermark (persisted)
message_sequence: dict = {}  # HP-01: {"schema_version","store_id","last_allocated"}
cursors: dict = {}  # HP-02: sid -> {store_id: confirmed sequence} (persisted)
operation_journal: dict = {}  # HP-03: operation_id -> completed mutation record
spawn_tokens: dict = {}  # HP-04: spawn_token -> session_id (rebuilt from event replay)
_local_store_id: str = "unknown"
_last_activity: float = 0.0

_exit_requested = False
_last_gc_check: float = 0.0  # HP-08: wall-clock anchor for the daily GC due-check
log = logging.getLogger("cc-communicate.kernel")
_local_machine_type: str = "unknown"


def _atomic_write_json(path: str, obj):
    fileutil.atomic_write_json(path, obj, indent=2)


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


def _load_alive_convs():
    """Reload registered conversations from disk (R2). alive_conversations is
    otherwise in-memory and would be lost on every kernel restart (crash / idle
    exit / terminate), breaking all in-flight send_message calls. Persisted as a
    list of [a, b, info] (tuple keys aren't JSON-serializable); the pair is
    already canonical (sorted) when stored."""
    data = _read_json(ALIVE_CONVS_FILE)
    if not isinstance(data, list):
        return
    for entry in data:
        if isinstance(entry, list) and len(entry) >= 2:
            a, b = entry[0], entry[1]
            info = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
            alive_conversations[(a, b)] = info
    log.info("loaded alive_conversations.json: %d convs", len(alive_conversations))


def _save_alive_convs():
    data = [[a, b, info] for (a, b), info in alive_conversations.items()]
    _atomic_write_json(ALIVE_CONVS_FILE, data)


def _load_ack_timestamps():
    """Reload per-sid ACK watermarks from disk (T24). acked_timestamps is
    otherwise in-memory; listen_scan updates it in memory (frequent, no I/O) and
    upload_ack_timestamp persists immediately (on close). This load catches the
    case where the kernel restarts mid-conversation - the CC can recover its ts
    via query_my_ACK_timestamp. Persisted as a flat {sid: ts} dict."""
    data = _read_json(ACK_TIMESTAMPS_FILE)
    if isinstance(data, dict):
        for sid, ts in data.items():
            if isinstance(ts, (int, float)):
                acked_timestamps[sid] = int(ts)
        log.info("loaded ack_timestamps.json: %d sids", len(acked_timestamps))


def _save_ack_timestamps():
    _atomic_write_json(ACK_TIMESTAMPS_FILE, acked_timestamps)


def _load_message_sequence():
    """Load the persistent per-store sequence counter (HP-01), self-healing to
    max(persisted, max sequence found in any pipe/log file) so a lost/corrupt
    counter NEVER causes sequence reuse."""
    import conversations as _conv
    data = _read_json(MESSAGE_SEQUENCE_FILE)
    state = {"schema_version": 1, "store_id": _local_store_id, "last_allocated": 0}
    if isinstance(data, dict) and isinstance(data.get("last_allocated"), (int, float)):
        state["last_allocated"] = max(0, int(data["last_allocated"]))
        if data.get("store_id"):
            state["store_id"] = data["store_id"]
    found = 0
    try:
        entries = os.listdir(CONVERSATIONS_DIR)
    except FileNotFoundError:
        entries = []
    for name in entries:
        for sub in ("pipe", "log"):
            d = os.path.join(CONVERSATIONS_DIR, name, sub)
            if not os.path.isdir(d):
                continue
            for fname in os.listdir(d):
                info = _conv.parse_any_pipe_filename(fname)
                if info and info["sequence"] is not None:
                    found = max(found, info["sequence"])
    state["last_allocated"] = max(state["last_allocated"], found)
    message_sequence.clear()
    message_sequence.update(state)
    log.info("loaded message_sequence: last_allocated=%d (healed from files=%d)",
             state["last_allocated"], found)


def _save_message_sequence():
    _atomic_write_json(MESSAGE_SEQUENCE_FILE, message_sequence)


def _load_cursors():
    """Reload per-sid per-store cursors (HP-02). Fresh start when absent -
    cursor state is NEVER converted from legacy ack_timestamps (explicit
    migration point)."""
    data = _read_json(CURSORS_FILE)
    if not isinstance(data, dict):
        return
    sessions_data = data.get("sessions")
    if not isinstance(sessions_data, dict):
        return
    for sid, per in sessions_data.items():
        if not isinstance(per, dict):
            continue
        clean = {str(k): int(v) for k, v in per.items()
                 if isinstance(v, int) and not isinstance(v, bool) and v >= 0}
        if clean:
            cursors[sid] = clean
    log.info("loaded cursors.json: %d sids", len(cursors))


def _save_cursors():
    _atomic_write_json(CURSORS_FILE, {"schema_version": 1, "sessions": cursors})


def _load_operation_journal():
    operation_journal.clear()
    operation_journal.update(operation_journal_mod.load(OPERATION_JOURNAL_FILE))
    log.info("loaded operation journal: %d entries", len(operation_journal))


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
    # T30: keep every (pid, start) this session has ever registered - a boot can
    # fire SessionStart twice (startup + restore) and the firings can resolve
    # different claude pids (one transient, already dead). check_alive falls
    # back across these instead of trusting the last event alone. Bounded to
    # the 8 most recent; dead entries are pruned by check_alive on read.
    known = alive_sessions.get(sid, {}).get("known_pids", {})
    alive_sessions[sid] = {
        "pid": ev.get("pid"),
        "start_time": parse_start_time(ev.get("start_time")),
        "cwd": ev.get("cwd"),
        "machine": _local_machine_type,
        "known_pids": known,
    }
    known[ev.get("pid")] = parse_start_time(ev.get("start_time"))
    if len(known) > 8:
        for old_pid in sorted(known, key=known.get)[:-8]:
            known.pop(old_pid, None)
    # HP-04: a start event carrying CC_COMMUNICATE_SPAWN_TOKEN binds the
    # session to its spawn request (plan A). Rebuilt on kernel restart via
    # event replay, so the map needs no separate persistence.
    tok = ev.get("spawn_token")
    if tok:
        spawn_tokens[tok] = sid
        try:
            os.remove(os.path.join(PENDING_SPAWN_DIR, tok + ".json"))
        except OSError:
            pass


def _handle_end(ev: dict, sid: str):
    alive_sessions.pop(sid, None)
    if sid in sessions:
        sessions[sid]["ended_at"] = ev.get("event_ts")
    for tok, s in list(spawn_tokens.items()):
        if s == sid:
            spawn_tokens.pop(tok, None)


# HP-03: mutations whose retry must not re-execute side effects. High-frequency
# scans (listen_scan/_v2) are naturally idempotent (same cursor rescan is
# harmless) and are excluded to keep journal churn low. spawn/evoke are
# journaled for the rpc-retry window; their cross-crash-window dedup lands with
# HP-04 spawn_token (Wave 2).
_JOURNALED_FUNCTIONS = frozenset({
    "send_message", "register_conversation", "unregister_conversation",
    "withdraw", "create_conversation_folder", "upload_ack_timestamp",
    "activate_connection", "deactivate_connection",
    "upload_cursor", "collect_messages", "spawn_cc_new", "spawn_cc_resume",
    "evoke", "kernel_terminate",
})


def drain_queue() -> bool:
    try:
        files = sorted(os.listdir(QUEUE_DIR))
    except FileNotFoundError:
        return False
    reqs = [f for f in files if f.endswith(".json")]
    journal_dirty = False
    for fname in reqs:
        path = os.path.join(QUEUE_DIR, fname)
        try:
            req = _read_json(path)
        except OSError:
            continue  # T12: transient read error -> retry next cycle
        try:
            if not req or "function" not in req or "request_id" not in req:
                raise ValueError("malformed request")
            function = req["function"]
            op_id = req.get("operation_id")
            journaled = op_id and function in _JOURNALED_FUNCTIONS
            if journaled:
                hit, replay = operation_journal_mod.completed_result(
                    operation_journal, op_id)
                if hit:
                    # HP-03: retry of a completed operation - replay the
                    # recorded result WITHOUT re-executing the side effect.
                    resp = {"request_id": req["request_id"], "result": replay,
                            "error": None}
                    _write_response_and_consume(resp, path)
                    continue
            result = _dispatch(function, req.get("args") or {})
            resp = {"request_id": req["request_id"], "result": result, "error": None}
            if journaled:
                operation_journal_mod.record_completed(
                    operation_journal, op_id, function, result)
                journal_dirty = True
                # Deferred save: the journal is the fast dedup path at retry
                # time; domain keys (message_id) are the crash-surviving truth.
                # Saving once per drain cycle, not per mutation, keeps the
                # kernel's event loop fast and avoids spawn-race regressions.
        except Exception as e:
            log.exception("error handling request %s", fname)
            resp = {"request_id": req.get("request_id") if req else None,
                    "result": None, "error": f"{type(e).__name__}: {e}"}
        _write_response_and_consume(resp, path)
    if journal_dirty:
        operation_journal_mod.save(OPERATION_JOURNAL_FILE, operation_journal)
    return bool(reqs)


def _write_response_and_consume(resp: dict, req_path: str):
    rid = resp["request_id"]
    if rid is not None:
        os.makedirs(QUEUE_RESPONSES_DIR, exist_ok=True)
        _atomic_write_json(os.path.join(QUEUE_RESPONSES_DIR, rid + ".json"), resp)
    try:
        os.remove(req_path)
    except OSError:
        pass


# HP-06: per-function arg validators applied at the dispatch trust boundary.
# Covers local AND remote RPC (a peer's call_remote lands in this same queue).
# Validators run only on args that are present and non-None; required-ness is
# still enforced by the args["..."] lookups below.
_ARG_VALIDATORS = {
    "query_session": {"session_id": validation.validate_session_id},
    "check_alive": {"session_id": validation.validate_session_id},
    "query_conversations": {"session_id": validation.validate_session_id},
    "send_message": {"fromid": validation.validate_session_id,
                     "toid": validation.validate_session_id,
                     "message": validation.validate_message_size,
                     "message_id": validation.validate_message_id,
                     "correlation_id": validation.validate_message_id,
                     "artifact_refs": validation.validate_artifact_refs},
    "register_conversation": {"sid_a": validation.validate_session_id,
                              "sid_b": validation.validate_session_id},
    "unregister_conversation": {"sid_a": validation.validate_session_id,
                                "sid_b": validation.validate_session_id},
    "withdraw": {"fromid": validation.validate_session_id,
                 "toid": validation.validate_session_id,
                 "message_id": validation.validate_message_id},
    "evoke": {"session_id": validation.validate_session_id},
    "collect_messages": {"session_id": validation.validate_session_id},
    "listen_scan": {"sid": validation.validate_session_id},
    "query_ack_timestamp": {"sid": validation.validate_session_id},
    "upload_ack_timestamp": {"sid": validation.validate_session_id},
    "spawn_cc_new": {"cwd": validation.validate_cwd,
                     "spawn_token": validation.validate_spawn_token},
    "find_session_by_token": {"token": validation.validate_spawn_token},
    "has_pending_spawn": {"token": validation.validate_spawn_token},
    "claim_pending_spawn": {"token": validation.validate_spawn_token,
                            "session_id": validation.validate_session_id},
    "spawn_cc_resume": {"session_id": validation.validate_session_id,
                        "cwd": validation.validate_cwd},
    "create_conversation_folder": {"id1": validation.validate_session_id,
                                   "id2": validation.validate_session_id},
    "activate_connection": {"connection_id": validation.validate_connection_id},
    "deactivate_connection": {},
    "get_connection_info": {},
    "run_gc": {"dry_run": validation.validate_bool},
    "backlog_stats": {"session_id": validation.validate_session_id},
    "listen_scan_v2": {"sid": validation.validate_session_id},
    "query_cursors": {"sid": validation.validate_session_id},
    "upload_cursor": {"sid": validation.validate_session_id},
}


def _validate_args(function: str, args: dict):
    for arg, validator in _ARG_VALIDATORS.get(function, {}).items():
        if arg in args and args[arg] is not None:
            validator(args[arg])


def _dispatch(function: str, args: dict):
    _validate_args(function, args)
    if function == "query_session":
        return kernel_api.query_session(sessions, args["session_id"])
    if function == "check_alive":
        return kernel_api.check_alive(alive_sessions, args["session_id"])
    if function == "query_conversations":
        return kernel_api.query_conversations(args["session_id"])
    if function == "send_message":
        return kernel_api.send_message(
            alive_conversations, message_sequence, _local_store_id,
            args["fromid"], args["toid"], args["message"], args.get("message_id"),
            args.get("kind"), args.get("correlation_id"))
    if function == "register_conversation":
        return kernel_api.register_conversation(alive_conversations, args["sid_a"], args["sid_b"])
    if function == "unregister_conversation":
        return kernel_api.unregister_conversation(alive_conversations, args["sid_a"], args["sid_b"])
    if function == "activate_connection":
        return kernel_api.activate_connection(
            alive_conversations, args["sid_a"], args["sid_b"], args["connection_id"])
    if function == "get_connection_info":
        return kernel_api.get_connection_info(args["sid_a"], args["sid_b"])
    if function == "deactivate_connection":
        return kernel_api.deactivate_connection(alive_conversations, args["sid_a"], args["sid_b"])
    if function == "withdraw":
        return kernel_api.withdraw(alive_conversations, args["fromid"], args["toid"], args.get("init_connect", 0), args.get("message_id"))
    if function == "evoke":
        return kernel_api.evoke(sessions, args["session_id"])
    if function == "collect_messages":
        return kernel_api.collect_messages(args["session_id"])
    if function == "listen_scan":
        return kernel_api.listen_scan(acked_timestamps, args["sid"], args.get("acked_ts", 0))
    if function == "query_ack_timestamp":
        return kernel_api.query_ack_timestamp(acked_timestamps, args["sid"])
    if function == "upload_ack_timestamp":
        return kernel_api.upload_ack_timestamp(acked_timestamps, args["sid"], args.get("ts", 0))
    if function == "listen_scan_v2":
        return kernel_api.listen_scan_v2(cursors, _local_store_id, args["sid"],
                                         args.get("cursor", 0))
    if function == "query_cursors":
        return kernel_api.query_cursors(cursors, args["sid"])
    if function == "upload_cursor":
        return kernel_api.upload_cursor(cursors, _local_store_id, args["sid"],
                                        args.get("seq", 0))
    if function == "session_by_pid":
        return kernel_api.session_by_pid(sessions, alive_sessions, args["pid"])
    if function == "find_new_session":
        return kernel_api.find_new_session(sessions, args["cwd"], args.get("since_ts", 0))
    if function == "spawn_cc_new":
        return kernel_api.spawn_cc_new(args["cwd"], args["prompt"], args.get("spawn_token"))
    if function == "find_session_by_token":
        return kernel_api.find_session_by_token(spawn_tokens, args["token"])
    if function == "has_pending_spawn":
        return kernel_api.has_pending_spawn(args["token"])
    if function == "claim_pending_spawn":
        return kernel_api.claim_pending_spawn(spawn_tokens, args["token"], args["session_id"])
    if function == "spawn_cc_resume":
        return kernel_api.spawn_cc_resume(args["session_id"], args["prompt"], args.get("cwd"))
    if function == "create_conversation_folder":
        return kernel_api.create_conversation_folder(args["id1"], args["id2"])
    if function == "kernel_terminate":
        return kernel_api.kernel_terminate()
    if function == "run_gc":
        return kernel_api.run_gc(args.get("dry_run", False))
    if function == "backlog_stats":
        return kernel_api.backlog_stats(args["session_id"])
    # arm_poller dispatch REMOVED (v2.2 Amd3)
    raise ValueError(f"unknown kernel function: {function}")


def _queue_has_pending() -> bool:
    try:
        return any(f.endswith(".json") for f in os.listdir(QUEUE_DIR))
    except FileNotFoundError:
        return False


def _should_exit() -> bool:
    """D10: exit looks ONLY at queue/activity/terminate - a registered-but-
    idle conversation is NOT a process lease. All kernel state is persistent
    (alive_conversations.json etc.), so a restart reloads it; the exit path
    saves it (main's finally block)."""
    if _exit_requested or os.path.exists(TERMINATE_FLAG):
        return True
    if _queue_has_pending():                    # queue: in-flight request
        return False
    if time.monotonic() - _last_activity < _IDLE_TIMEOUT:  # activity
        return False
    return True


def _exit_decision() -> bool:
    """True = exit now. Guards the exit-vs-request race (R4): a request that
    landed in the window between _should_exit() and the break restarts the
    cycle (second queue scan - the optimization; client retry + _wake_remote
    is the correctness backstop)."""
    if not _should_exit():
        return False
    return not _queue_has_pending()


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
    global _last_activity, _local_machine_type, _local_store_id
    _setup_logging()
    _install_signal_handlers()
    ensure_runtime_dirs()
    # v2: establish machine identity (creates machine_identity.json on first
    # run, detects type + claude_bin). Stamps local sessions with `machine`.
    ident = machine_identity.load_or_create()
    _local_machine_type = ident.get("type", "unknown")
    _local_store_id = ident.get("id", "unknown")
    log.info("kernel starting (pid=%d, machine=%s, idle_timeout=%ss)",
             os.getpid(), _local_machine_type, _IDLE_TIMEOUT)

    _load_sessions()
    _load_alive_convs()
    _load_ack_timestamps()
    _load_message_sequence()
    _load_cursors()
    _load_operation_journal()
    process_session_ctrl_event()
    # HP-08: start-time GC sweep (before READY - no live traffic yet)
    res = cleanup.maybe_run_gc()
    if res:
        log.info("GC at start: %s", res)
    _last_gc_check = time.time()
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
                _save_alive_convs()  # R2: register/unregister/withdraw may have changed it
            if ev_busy or q_busy:
                sleep = _BASE_SLEEP
                idle = 0
            else:
                idle += 1
                if idle >= _IDLE_CYCLES_BEFORE_BACKOFF:
                    sleep = min(sleep * 10, _MAX_SLEEP)
                    idle = 0
            if _exit_decision():
                break
            # HP-08: daily GC sweep (due-check once per minute of wall time;
            # cleanup.maybe_run_gc is a no-op between due dates)
            if time.time() - _last_gc_check >= 60:
                _last_gc_check = time.time()
                res = cleanup.maybe_run_gc()
                if res:
                    log.info("GC sweep: %s", res)
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
        _save_alive_convs()
        _save_ack_timestamps()  # T24: persist in-memory listen_scan updates
        _save_message_sequence()
        _save_cursors()
        log.info("kernel exited")


if __name__ == "__main__":
    main()
