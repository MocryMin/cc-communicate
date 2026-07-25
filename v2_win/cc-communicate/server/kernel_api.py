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

from paths import CONVERSATIONS_DIR, SERVER_DATA_DIR, PLUGIN_ROOT, ACK_TIMESTAMPS_FILE, MESSAGE_SEQUENCE_FILE
from proc import proc_start_time
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
    info = alive_sessions.get(session_id)
    if not info:
        return 0
    pid = info.get("pid")
    recorded = info.get("start_time")
    if pid is None or recorded is None:
        return 0
    current = proc_start_time(pid)
    if current is None:
        alive_sessions.pop(session_id, None)
        return 0
    if abs(current - float(recorded)) > 1.0:
        alive_sessions.pop(session_id, None)
        return 0
    return 1


# ---------- conversation registration ----------

def register_conversation(alive_conversations: dict, sid_a: str, sid_b: str):
    a, b = sorted([sid_a, sid_b])
    alive_conversations[(a, b)] = {"established_at": time.time()}


def unregister_conversation(alive_conversations: dict, sid_a: str, sid_b: str):
    a, b = sorted([sid_a, sid_b])
    alive_conversations.pop((a, b), None)


# ---------- messaging ----------

def send_message(alive_conversations: dict, message_sequence: dict, store_id: str,
                 fromid: str, toid: str, message: str, message_id: str = None) -> str:
    """HP-01: allocate a per-store sequence, wrap the text in a v1 record,
    atomically publish. The sequence counter is persisted BEFORE the message
    (a crash in between leaves a gap - allowed; a sequence is never reused).
    HP-03 dedup: a retry carrying the same message_id returns the ORIGINAL
    result without publishing a duplicate. Return string keeps the legacy
    'message_sent at <created_at_ms>' shape (connect parses it)."""
    a, b = sorted([fromid, toid])
    if (a, b) not in alive_conversations:
        return "failed, connection not registered"
    d = conversations.ensure_conv_dir(fromid, toid)
    if message_id:
        found = _find_message_file(d, message_id)
        if found:
            rec = message_record.read_record(found)
            ts = rec.get("created_at_ms", 0) if rec else 0
            return f"message_sent at {ts}"
    seq = int(message_sequence.get("last_allocated", 0)) + 1
    message_sequence["last_allocated"] = seq
    message_sequence["store_id"] = store_id
    fileutil.atomic_write_json(MESSAGE_SEQUENCE_FILE, message_sequence)
    rec = message_record.new_record(store_id, seq, fromid, toid, message,
                                    message_id=message_id)
    message_record.publish(d, rec)
    return f"message_sent at {rec['created_at_ms']}"


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


def withdraw(alive_conversations: dict, fromid: str, toid: str, init_connect: int = 0) -> str:
    if init_connect:
        # HP-06 destructive containment: conv_dir already validated both ids;
        # re-verify the resolved target is strictly under CONVERSATIONS_DIR and
        # IS the canonical pair dir before rmtree.
        d = conversations.conv_dir(fromid, toid)
        target = validation.resolve_under(CONVERSATIONS_DIR, os.path.basename(d))
        if os.path.isdir(target):
            shutil.rmtree(target)
        unregister_conversation(alive_conversations, fromid, toid)
        return "conversation withdrawn"
    d = conversations.conv_dir(fromid, toid)
    pipe = os.path.join(d, "pipe")
    try:
        files = os.listdir(pipe)
    except FileNotFoundError:
        return "no messages"
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
        return f"no messages from {fromid}"
    candidates.sort(key=lambda x: x[0])
    os.remove(os.path.join(pipe, candidates[-1][1]))
    return f"withdrew latest message from {fromid}"


# ---------- process spawning ----------

def evoke(sessions: dict, session_id: str, prompt: str = None) -> str:
    """Revive a CC session by resuming it (core_plan "内核函数 5"). Uses
    `claude --resume <sid> <prompt>` so the SAME session_id is revived. The
    revived CC fires SessionStart -> process_session_ctrl_event updates
    alive_sessions with the new pid. Returns 'failed, session unknown' if the
    session isn't in sessions."""
    if session_id not in sessions:
        return "failed, session unknown"
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
    return "evoke spawned (resumed)"


def spawn_cc_new(cwd: str, prompt: str) -> str:
    """Kernel function for cross-machine create_collaborator (v2.1 §3.4.6): a
    peer MCP server calls this via call_remote so THIS kernel spawns a local CC
    (it knows its own claude path / spawn mechanism)."""
    spawn.spawn_cc_new(cwd, prompt)
    return "spawned"


def spawn_cc_resume(session_id: str, prompt: str, cwd: str = None) -> str:
    spawn.spawn_cc_resume(session_id, prompt, cwd)
    return "spawned"


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

def create_conversation_folder(id1: str, id2: str) -> str:
    """Create the conversation folder (+ pipe/, log/) for a pair. The MCP server
    decides whether to call this locally or via call_remote (v2.1 §3.5.5)."""
    conversations.ensure_conv_dir(id1, id2)
    return "ok"


# ---------- control ----------

def kernel_terminate() -> str:
    """Request the kernel to exit on its next loop iteration (v2.1 §3.5.3).
    Writes a flag file the kernel loop polls. (The kernel runs as __main__, so
    `import kernel; kernel._exit_requested=True` would touch a DIFFERENT module
    object - the flag file sidesteps that.)"""
    from paths import TERMINATE_FLAG, SERVER_DATA_DIR
    try:
        os.makedirs(SERVER_DATA_DIR, exist_ok=True)
        open(TERMINATE_FLAG, "w").close()
        return "terminate requested"
    except OSError as e:
        return f"failed, {e}"


# ---------- session discovery ----------

def session_by_pid(sessions: dict, pid: int):
    for sid, info in sessions.items():
        if info and info.get("pid") == pid:
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
