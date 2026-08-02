"""Kernel functions callable via the queue RPC (core_plan "内核函数").

These operate on kernel state passed explicitly as parameters (no module
globals) so each function's state access is visible at the call site. kernel.py's
_dispatch() routes RPC requests here - both LOCAL requests (rpc_client.call) and
REMOTE requests (rpc_client.call_remote from a peer MCP server).

v2.2 changes (Amd3): arm_poller REMOVED (merged into listen.py). collect_messages
KEPT as a kernel function - it's used by close_connection (drain) and by a peer's
listen.py to archive cross-machine messages (#W7 delegation).

Implemented:
  Read-only:  query_session, check_alive, query_conversations
  Messaging:  send_message, withdraw, register_conversation, unregister_conversation
  Spawning:   evoke, spawn_cc_new, spawn_cc_resume
  Listening:  collect_messages
  Folder:     create_conversation_folder
  Control:    kernel_terminate
  Discovery:  session_by_pid, find_new_session
"""
from __future__ import annotations

import json
import os
import shutil
import time

from paths import CONVERSATIONS_DIR, SERVER_DATA_DIR, PLUGIN_ROOT, ACK_TIMESTAMPS_FILE, MESSAGE_SEQUENCE_FILE, CURSORS_FILE, PENDING_SPAWN_DIR
import cleanup
import proc
import conversations
import fileutil
import message_record
import spawn
import validation


def _atomic_write_json(path: str, obj):
    fileutil.atomic_write_json(path, obj)


def query_session(sessions: dict, session_id: str):
    return sessions.get(session_id)


def check_alive(alive_sessions: dict, session_id: str) -> int:
    """1 if any registration (primary or known_pids fallback) matches the live
    process, else 0. T30: a boot can fire SessionStart twice (startup + restore)
    resolving DIFFERENT claude pids - one transient (already dead), one real.
    Last-write-wins leaves the dead pid primary, so fall back across every pid
    ever recorded for this sid (known_pids, maintained by kernel._handle_start).
    Dead candidates are pruned; a match promotes to primary so the hot path
    stays cheap. start_time tie-break (abs diff <= 1s) rejects pid reuse."""
    info = alive_sessions.get(session_id)
    if not info:
        return 0

    known = info.get("known_pids")
    if known:
        # newest-first: the primary (last write) is checked first - the hot
        # path stays O(1) when it is alive; a dead last-write is pruned BEFORE
        # an older live pid can match and return (T30).
        for pid, recorded in list(known.items())[::-1]:
            m = proc.pid_matches(pid, recorded)
            if m is True:
                info["pid"], info["start_time"] = pid, recorded
                return 1
            if m is False:
                known.pop(pid, None)  # dead - don't re-check next time
    else:
        m = proc.pid_matches(info.get("pid"), info.get("start_time"))
        if m is True:
            return 1
        if m is False:
            alive_sessions.pop(session_id, None)
        return 0
    alive_sessions.pop(session_id, None)
    return 0


# ---------- conversation registration ----------

def register_conversation(alive_conversations: dict, sid_a: str, sid_b: str) -> dict:
    a, b = sorted([sid_a, sid_b])
    alive_conversations[(a, b)] = {"established_at": time.time()}
    return {"ok": True}


def unregister_conversation(alive_conversations: dict, sid_a: str, sid_b: str) -> dict:
    a, b = sorted([sid_a, sid_b])
    alive_conversations.pop((a, b), None)
    return {"ok": True}


# ---------- messaging ----------

def send_message(alive_conversations: dict, message_sequence: dict, store_id: str,
                 fromid: str, toid: str, message: str, message_id: str = None,
                 kind: str = None, correlation_id: str = None) -> dict:
    """HP-01: allocate a per-store sequence, wrap the text in a v1 record,
    atomically publish. HP-03 dedup: a retry carrying the same message_id
    returns the ORIGINAL result without publishing a duplicate. Structured
    dict result (HP-07) - callers branch on 'sent', never on text."""
    a, b = sorted([fromid, toid])
    if (a, b) not in alive_conversations:
        return {"sent": False, "reason": "connection not registered"}
    d = conversations.ensure_conv_dir(fromid, toid)
    if message_id:
        found = _find_message_file(d, message_id)
        if found:
            rec = message_record.read_record(found)
            ts = rec.get("created_at_ms", 0) if rec else 0
            return {"sent": True, "message_id": message_id, "ts": ts,
                    "correlation_id": rec.get("correlation_id") if rec else None}
    seq = int(message_sequence.get("last_allocated", 0)) + 1
    message_sequence["last_allocated"] = seq
    message_sequence["store_id"] = store_id
    fileutil.atomic_write_json(MESSAGE_SEQUENCE_FILE, message_sequence)
    rec = message_record.new_record(store_id, seq, fromid, toid, message,
                                    message_id=message_id, kind=kind or "text",
                                    correlation_id=correlation_id)
    message_record.publish(d, rec)
    return {"sent": True, "message_id": rec["message_id"], "ts": rec["created_at_ms"],
            "correlation_id": correlation_id}


def _find_message_file(conv_d: str, message_id: str):
    """Locate a published message by message_id (filename suffix) in pipe/ or
    log/. Returns the full path, or None. O(files) - fine at this scale."""
    suffix = "__" + message_id + ".json"
    for sub in ("pipe", "log"):
        d = os.path.join(conv_d, sub)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if name.endswith(suffix):
                return os.path.join(d, name)
    return None


def query_conversations(querier_sid: str) -> dict:
    """v2 format (v2.1 §3.4.1): {partner_sid: {...}, ...}. Reads the conversations
    folder directly (includes ended-but-not-withdrawn). info is {} for now
    (future: info.json metadata)."""
    result = {}
    try:
        entries = os.listdir(CONVERSATIONS_DIR)
    except FileNotFoundError:
        return result
    for name in entries:
        parts = name.split(conversations.SEP)
        if len(parts) != 2:
            continue
        if querier_sid in parts:
            partner = parts[1] if parts[0] == querier_sid else parts[0]
            result[partner] = {}
    return result


def withdraw(alive_conversations: dict, fromid: str, toid: str, init_connect: int = 0,
             message_id: str = None) -> dict:
    if init_connect:
        # HP-06 destructive containment: conv_dir already validated both ids;
        # re-verify the resolved target is strictly under CONVERSATIONS_DIR and
        # IS the canonical pair dir before rmtree.
        d = conversations.conv_dir(fromid, toid)
        target = validation.resolve_under(CONVERSATIONS_DIR, os.path.basename(d))
        a, b = sorted([fromid, toid])
        # Structured no-op (HP-07): nothing on disk AND nothing registered ->
        # withdrawn False, so a retry/cleanup can tell "already gone" apart
        # from "just removed".
        existed = os.path.isdir(target) or (a, b) in alive_conversations
        if os.path.isdir(target):
            shutil.rmtree(target)
        unregister_conversation(alive_conversations, fromid, toid)
        if not existed:
            return {"withdrawn": False, "reason": "conversation not found"}
        return {"withdrawn": True, "detail": "conversation withdrawn"}
    if message_id:
        # HP-03: withdraw an EXPLICIT target (retry-safe). The legacy
        # latest-message mode below is non-idempotent by nature and remains
        # for one release only.
        validation.validate_message_id(message_id)
        d = conversations.conv_dir(fromid, toid)
        found = _find_message_file(d, message_id)
        if not found or os.sep + "log" + os.sep in found:
            return {"withdrawn": False,
                    "reason": f"no message {message_id} (already withdrawn or never existed)"}
        try:
            os.remove(found)
        except OSError:
            return {"withdrawn": False,
                    "reason": f"no message {message_id} (already withdrawn or never existed)"}
        return {"withdrawn": True, "detail": f"withdrew message {message_id}",
                "message_id": message_id}
    d = conversations.conv_dir(fromid, toid)
    pipe = os.path.join(d, "pipe")
    try:
        files = os.listdir(pipe)
    except FileNotFoundError:
        return {"withdrawn": False, "reason": "no messages"}
    candidates = []
    for f in files:
        info = conversations.parse_any_pipe_filename(f)
        if not info or info["from_id"] != fromid:
            continue
        # "latest": records order by sequence, legacy by ts; records always
        # postdate legacy, so rank records above any legacy.
        key = (1, info["sequence"]) if info["format"] == "record" else (0, info["ts"])
        candidates.append((key, f))
    if not candidates:
        return {"withdrawn": False, "reason": f"no messages from {fromid}"}
    candidates.sort(key=lambda x: x[0])
    os.remove(os.path.join(pipe, candidates[-1][1]))
    return {"withdrawn": True, "detail": f"withdrew latest message from {fromid}"}


# ---------- process spawning ----------

def evoke(sessions: dict, session_id: str, prompt: str = None) -> dict:
    """Revive a CC session by resuming it (core_plan "内核函数 5"). Uses
    `claude --resume <sid> <prompt>` so the SAME session_id is revived. The
    revived CC fires SessionStart -> process_session_ctrl_event updates
    alive_sessions with the new pid. Returns {'evoked': True, 'session_id'}
    or {'evoked': False, 'reason': 'session unknown'}."""
    if session_id not in sessions:
        return {"evoked": False, "reason": "session unknown"}
    if prompt is None:
        prompt = ("You have been revived for p2p communication by cc-communicate. "
                  "Call my_session_id to learn your id, then call listen "
                  "(your_id, acked_ts, timeout) - it blocks and returns "
                  "{messages, watermark}. Pass 0 as acked_ts the first time, and "
                  "pass the returned watermark as acked_ts on every later listen "
                  "(the kernel archives only what you've confirmed - never drop "
                  "or duplicate it). Reply to any hello with send_message(your_id, "
                  "peer_id, <message>). KEEP LISTENING: after each listen returns, "
                  "process any messages and call listen again (with the latest "
                  "watermark), in a loop, until you call close_connection(your_id, "
                  "peer_id, your_latest_watermark). If you lose your watermark, "
                  "call query_my_ACK_timestamp(your_id). Never invoke listen.py "
                  "directly or write a shell listener - only use the listen tool.")
    # T25: pass the session's original cwd. `claude --resume <sid>` is cwd-scoped
    # (per-project .jsonl lookup); without the right cwd it runs in the kernel's
    # cwd (data/server/) and fails "No conversation found with session ID: <sid>".
    cwd = sessions.get(session_id, {}).get("cwd")
    spawn.spawn_cc_resume(session_id, prompt, cwd)
    return {"evoked": True, "session_id": session_id}


def spawn_cc_new(cwd: str, prompt: str, spawn_token: str = None) -> dict:
    """Kernel function for (cross-machine) create_collaborator /
    spawn_collaborator (v2.1 §3.4.6): a peer MCP server calls this via
    call_remote so THIS kernel spawns a local CC (it knows its own claude path
    / spawn mechanism). HP-04: writes pending_spawn/<token>.json BEFORE
    spawning - the marker makes same-token retries safe (no double spawn) and
    is the plan B claim record; the child gets the token via env (plan A)."""
    if spawn_token:
        validation.validate_spawn_token(spawn_token)
        os.makedirs(PENDING_SPAWN_DIR, exist_ok=True)
        fileutil.atomic_write_json(
            os.path.join(PENDING_SPAWN_DIR, spawn_token + ".json"),
            {"schema_version": 1, "spawn_token": spawn_token, "cwd": cwd,
             "created_at_ms": int(time.time() * 1000)})
    spawn.spawn_cc_new(cwd, prompt, spawn_token)
    return {"spawned": True, "spawn_token": spawn_token}


def spawn_cc_resume(session_id: str, prompt: str, cwd: str = None) -> dict:
    spawn.spawn_cc_resume(session_id, prompt, cwd)
    return {"spawned": True, "session_id": session_id}


# ---------- spawn tokens (HP-04 / D8) ----------
# One map per kernel: spawn_token -> session_id. Populated by plan A (start
# events carrying CC_COMMUNICATE_SPAWN_TOKEN) or plan B (claim_pending_spawn).
# Rebuilt on kernel restart by start-event replay. The pending_spawn/<token>
# marker file distinguishes "never spawned" from "spawned, not yet registered"
# so a same-token retry never double-spawns.

def find_session_by_token(spawn_tokens: dict, token: str):
    return spawn_tokens.get(token)


def has_pending_spawn(token: str) -> bool:
    """HP-08: a marker older than the TTL counts as ABSENT (poisoned-marker
    un-poisoning; the expired file itself is removed by the GC sweep)."""
    path = os.path.join(PENDING_SPAWN_DIR, token + ".json")
    if not os.path.isfile(path):
        return False
    return not cleanup.pending_marker_expired(path)


def claim_pending_spawn(spawn_tokens: dict, token: str, session_id: str) -> dict:
    """Plan B: the spawned worker claims its token on its first tool call.
    Idempotent: an existing binding is kept (worker retries are no-ops).
    HP-08: an expired marker is treated as absent (same result as missing)."""
    validation.validate_spawn_token(token)
    validation.validate_session_id(session_id)
    existing = spawn_tokens.get(token)
    if existing:
        return {"claimed": True, "session_id": existing}
    if not has_pending_spawn(token):
        return {"claimed": False, "reason": "no pending spawn for token"}
    spawn_tokens[token] = session_id
    try:
        os.remove(os.path.join(PENDING_SPAWN_DIR, token + ".json"))
    except OSError:
        pass
    return {"claimed": True, "session_id": session_id}


# ---------- listening (collect only; arm removed in Amd3) ----------

def _read_pipe_message(src: str, info: dict):
    """Build the normalized message dict for one pipe file (dual reader,
    HP-01), or None to skip (malformed/partial/undecodable - C5). Shared by
    listen_scan and collect_messages. The '_sort' key orders legacy .md first
    (they predate the upgrade), then records by SEQUENCE - never by
    created_at_ms (clock backward must not reorder, PB-2)."""
    if info["format"] == "record":
        rec = message_record.read_record(src)
        if not rec:
            return None
        return {"time": rec.get("created_at_ms", 0),
                "from_id": rec.get("from_session"),
                "message": (rec.get("payload") or {}).get("text"),
                "message_id": rec.get("message_id"),
                "sequence": rec.get("sequence"),
                "store_id": rec.get("store_id"),
                "_sort": (1, 0, rec.get("sequence") or 0)}
    try:
        with open(src, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    return {"time": info["ts"], "from_id": info["from_id"],
            "message": content, "message_id": None,
            "sequence": None, "store_id": None,
            "_sort": (0, info["ts"], 0)}


def _archive(src: str, log_dir: str, fname: str):
    """pipe -> log, best-effort (shared by the scans)."""
    os.makedirs(log_dir, exist_ok=True)
    try:
        os.replace(src, os.path.join(log_dir, fname))
    except OSError:
        pass


def collect_messages(session_id: str) -> list:
    """Read all undelivered pipe messages addressed to session_id, move them to
    log/, return sorted (legacy .md first, then records by sequence - see
    _read_pipe_message). Used by close_connection (drain) and the remote
    _archive_reply path. Direction-specific: only messages where
    toid == session_id."""
    result = []
    try:
        entries = os.listdir(CONVERSATIONS_DIR)
    except FileNotFoundError:
        return result
    for name in entries:
        parts = name.split(conversations.SEP)
        if len(parts) != 2 or session_id not in parts:
            continue
        conv_d = os.path.join(CONVERSATIONS_DIR, name)
        pipe = os.path.join(conv_d, "pipe")
        log = os.path.join(conv_d, "log")
        if not os.path.isdir(pipe):
            continue
        for fname in os.listdir(pipe):
            info = conversations.parse_any_pipe_filename(fname)
            if not info or info["to_id"] != session_id:
                continue
            src = os.path.join(pipe, fname)
            msg = _read_pipe_message(src, info)
            if msg is None:
                continue
            result.append(msg)
            _archive(src, log, fname)
    result.sort(key=lambda m: m["_sort"])
    for m in result:
        del m["_sort"]
    return result


# ---------- listening: watermark ACK (T24) ----------
# collect_messages (above) is the OLD archive-on-read scan, kept for connect's
# remote _archive_reply path. The CC-facing `listen` tool no longer uses it -
# it uses listen_scan, which is cancel-safe: it only archives what the CC has
# CONFIRMED (ts <= the watermark the CC passes back), never what it merely read.

def listen_scan(acked_timestamps: dict, sid: str, acked_ts: int) -> dict:
    """LEGACY timestamp-ACK scan (kept for the deprecation window, HP-01 dual
    reader via _read_pipe_message). Archive rule is unchanged:
    (to==sid, time<=acked_ts) moves pipe->log; for records the record's
    created_at_ms stands in for the timestamp until HP-02 cursors take over.
    Record message dicts carry message_id/sequence/store_id; legacy entries
    carry them as None. Cancel-safe: only what a PRIOR call confirmed is
    archived; the just-returned messages stay in the pipe and re-deliver."""
    if acked_ts and acked_ts > acked_timestamps.get(sid, 0):
        acked_timestamps[sid] = acked_ts
    messages = []
    try:
        entries = os.listdir(CONVERSATIONS_DIR)
    except FileNotFoundError:
        return {"messages": [], "watermark": acked_ts}
    for name in entries:
        parts = name.split(conversations.SEP)
        if len(parts) != 2 or sid not in parts:
            continue
        pipe = os.path.join(CONVERSATIONS_DIR, name, "pipe")
        log = os.path.join(CONVERSATIONS_DIR, name, "log")
        if not os.path.isdir(pipe):
            continue
        for fname in os.listdir(pipe):
            info = conversations.parse_any_pipe_filename(fname)
            if not info or info["to_id"] != sid:
                continue
            src = os.path.join(pipe, fname)
            msg = _read_pipe_message(src, info)
            if msg is None:
                continue
            if msg["time"] <= acked_ts:
                # CC confirmed receipt -> archive (pipe->log)
                _archive(src, log, fname)
                continue
            # time > acked_ts: undelivered -> return WITHOUT archiving
            messages.append(msg)
    messages.sort(key=lambda m: m["_sort"])
    for m in messages:
        del m["_sort"]
    watermark = max([m["time"] for m in messages], default=acked_ts)
    return {"messages": messages, "watermark": watermark}


def query_ack_timestamp(acked_timestamps: dict, sid: str) -> int:
    """Return the kernel's stored ACK watermark for sid (0 if unknown). Recovery
    path (T24): a CC that lost its ts (compact / long gap / kernel restart) calls
    query_my_ACK_timestamp to fetch this, then uses it as acked_ts on its next
    listen."""
    return acked_timestamps.get(sid, 0)


def upload_ack_timestamp(acked_timestamps: dict, sid: str, ts: int) -> int:
    """Persist the CC's latest ACK watermark (called by close_connection, T24).
    Updates the in-memory dict AND writes ack_timestamps.json immediately (close
    is infrequent, so the I/O is fine; this survives a later kernel crash). The
    kernel also saves the dict on clean exit as a fallback for in-memory updates
    from listen_scan. Returns the stored value."""
    if ts and ts > acked_timestamps.get(sid, 0):
        acked_timestamps[sid] = ts
    try:
        _atomic_write_json(ACK_TIMESTAMPS_FILE, acked_timestamps)
    except OSError:
        pass
    return acked_timestamps.get(sid, 0)


# ---------- conversation folder ----------

def create_conversation_folder(id1: str, id2: str) -> dict:
    """Create the conversation folder (+ pipe/, log/) for a pair. The MCP server
    decides whether to call this locally or via call_remote (v2.1 §3.5.5)."""
    conversations.ensure_conv_dir(id1, id2)
    return {"ok": True}


# ---------- connection metadata (HP-05 / D9) ----------
# info.json is the single-active-connection authority: written by
# activate_connection (enforced kernel-side - the kernel is the only writer),
# read by get_connection_info, closed by deactivate_connection. A retry with
# the SAME connection_id reuses; a different id while active is a CONFLICT.

def get_connection_info(sid_a: str, sid_b: str):
    """info.json for the pair, or None when absent/malformed."""
    try:
        with open(conversations.info_path(sid_a, sid_b), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def activate_connection(alive_conversations: dict, sid_a: str, sid_b: str,
                        connection_id: str) -> dict:
    """Register the pair + write info.json (status=active). Same connection_id
    retry -> reuse (no-op). Different active connection_id -> conflict."""
    validation.validate_connection_id(connection_id)
    a, b = sorted([sid_a, sid_b])
    existing = get_connection_info(a, b)
    if existing and existing.get("status") == "active":
        if existing.get("connection_id") == connection_id:
            return {"activated": True, "connection_id": connection_id,
                    "reused": True, "established_at_ms": existing.get("established_at_ms")}
        return {"activated": False, "reason": "conflict",
                "current_connection_id": existing.get("connection_id")}
    register_conversation(alive_conversations, a, b)
    info = {
        "schema_version": 1,
        "connection_id": connection_id,
        "status": "active",
        "established_at_ms": int(time.time() * 1000),
        "sid_a": a,
        "sid_b": b,
    }
    os.makedirs(conversations.conv_dir(a, b), exist_ok=True)
    fileutil.atomic_write_json(conversations.info_path(a, b), info)
    return {"activated": True, "connection_id": connection_id, "reused": False,
            "established_at_ms": info["established_at_ms"]}


def deactivate_connection(alive_conversations: dict, sid_a: str, sid_b: str) -> dict:
    """Unregister + mark info.json status=closed (close_connection, HP-05)."""
    a, b = sorted([sid_a, sid_b])
    unregister_conversation(alive_conversations, a, b)
    info = get_connection_info(a, b)
    if info:
        info["status"] = "closed"
        info["closed_at_ms"] = int(time.time() * 1000)
        fileutil.atomic_write_json(conversations.info_path(a, b), info)
    return {"closed": True}


# ---------- control ----------

def kernel_terminate() -> dict:
    """Request the kernel to exit on its next loop iteration (v2.1 §3.5.3).
    Writes a flag file the kernel loop polls. (The kernel runs as __main__, so
    `import kernel; kernel._exit_requested=True` would touch a DIFFERENT module
    object - the flag file sidesteps that.)"""
    from paths import TERMINATE_FLAG, SERVER_DATA_DIR
    try:
        os.makedirs(SERVER_DATA_DIR, exist_ok=True)
        open(TERMINATE_FLAG, "w").close()
        return {"terminated": True}
    except OSError as e:
        return {"terminated": False, "reason": str(e)}


# ---------- session discovery ----------

def session_by_pid(sessions: dict, alive_sessions: dict, pid: int):
    """Primary lookup walks sessions (last-write pid). T35: a boot can fire
    SessionStart twice (T30) and the LAST write clobbers sessions[sid]['pid']
    with a transient, already-dead pid - so on a primary miss, fall back
    across every sid's known_pids (liveness-checked, same rule as
    check_alive)."""
    for sid, info in sessions.items():
        if info and info.get("pid") == pid:
            return sid
    for sid, info in (alive_sessions or {}).items():
        known = info.get("known_pids") or {}
        if pid in known and proc.pid_matches(pid, known[pid]) is True:
            return sid
    return None


def find_new_session(sessions: dict, cwd: str, since_ts):
    target = os.path.normcase(os.path.abspath(cwd))
    best = None
    best_ts = since_ts
    for sid, info in sessions.items():
        if not info:
            continue
        s_cwd = info.get("cwd")
        if not s_cwd:
            continue
        if os.path.normcase(os.path.abspath(s_cwd)) != target:
            continue
        started = info.get("started_at")
        if started is None:
            continue
        if started > best_ts:
            best_ts = started
            best = sid
    return best


# ---------- listening: per-store cursor ACK (HP-02) ----------
# Cursor semantics: a cursor says "the caller has DURABLY received everything
# up to this sequence FROM THIS STORE" - transport-level receipt, NOT upper-
# layer task completion. Archive rule is ONLY:
#     record.store_id == this store AND record.sequence <= cursor[this store]
# Never compare sequences across stores; never use created_at_ms for
# correctness. Legacy .md files are INVISIBLE here (explicit migration point:
# v0.3 in-flight messages drain via the legacy listen during the deprecation
# window; cursor state never converts old timestamps).

def listen_scan_v2(cursors_state: dict, store_id: str, sid: str, acked_seq: int) -> dict:
    """Atomic (kernel-thread) per-store cursor scan. acked_seq is the caller's
    confirmed cursor FOR THIS STORE. Archives confirmed records (pipe->log),
    peeks newer ones (no archive), updates the in-memory cursor map (persisted
    on upload/close/exit). Cancel-safe: only what a PRIOR call confirmed is
    archived."""
    try:
        acked_seq = int(acked_seq or 0)
    except (TypeError, ValueError):
        raise validation.InvalidArgumentError(f"cursor must be an int; got {acked_seq!r}")
    if acked_seq < 0:
        raise validation.InvalidArgumentError(f"cursor must be >= 0; got {acked_seq}")
    per = cursors_state.setdefault(sid, {})
    if acked_seq > per.get(store_id, 0):
        per[store_id] = acked_seq
    messages = []
    try:
        entries = os.listdir(CONVERSATIONS_DIR)
    except FileNotFoundError:
        return {"store_id": store_id, "messages": [], "next_cursor": acked_seq}
    for name in entries:
        parts = name.split(conversations.SEP)
        if len(parts) != 2 or sid not in parts:
            continue
        pipe = os.path.join(CONVERSATIONS_DIR, name, "pipe")
        log = os.path.join(CONVERSATIONS_DIR, name, "log")
        if not os.path.isdir(pipe):
            continue
        for fname in os.listdir(pipe):
            info = conversations.parse_any_pipe_filename(fname)
            if not info or info["format"] != "record" or info["to_id"] != sid:
                continue  # legacy .md invisible to v2 (migration point)
            src = os.path.join(pipe, fname)
            rec = message_record.read_record(src)
            if not rec:
                continue  # C5: skip malformed/partial
            seq = rec.get("sequence")
            if not isinstance(seq, int):
                continue
            if seq <= acked_seq:
                os.makedirs(log, exist_ok=True)
                try:
                    os.replace(src, os.path.join(log, fname))
                except OSError:
                    pass
                continue
            messages.append(rec)
    messages.sort(key=lambda r: r["sequence"])
    next_cursor = max([m["sequence"] for m in messages], default=acked_seq)
    return {"store_id": store_id, "messages": messages, "next_cursor": next_cursor}


def query_cursors(cursors_state: dict, sid: str) -> dict:
    """This kernel's stored cursor map for sid (usually one store entry - each
    kernel persists only cursors for ITS OWN store). user_functions merges
    across machines."""
    return dict(cursors_state.get(sid, {}))


def upload_cursor(cursors_state: dict, store_id: str, sid: str, seq: int) -> dict:
    """Persist the caller's cursor for THIS store (max-merge; never regresses).
    Written through to cursors.json immediately (close is infrequent), so it
    survives a later kernel crash. Returns sid's stored map for this kernel."""
    try:
        seq = int(seq or 0)
    except (TypeError, ValueError):
        raise validation.InvalidArgumentError(f"cursor must be an int; got {seq!r}")
    if seq < 0:
        raise validation.InvalidArgumentError(f"cursor must be >= 0; got {seq}")
    per = cursors_state.setdefault(sid, {})
    if seq > per.get(store_id, 0):
        per[store_id] = seq
    try:
        fileutil.atomic_write_json(
            CURSORS_FILE, {"schema_version": 1, "sessions": cursors_state})
    except OSError:
        pass
    return dict(per)
