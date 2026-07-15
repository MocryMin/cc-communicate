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

from paths import CONVERSATIONS_DIR, SERVER_DATA_DIR, PLUGIN_ROOT
from proc import proc_start_time
import conversations
import spawn


def _atomic_write_json(path: str, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


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

def send_message(alive_conversations: dict, fromid: str, toid: str, message: str) -> str:
    a, b = sorted([fromid, toid])
    if (a, b) not in alive_conversations:
        return "failed, connection not registered"
    ts = int(time.time() * 1000)
    d = conversations.ensure_conv_dir(fromid, toid)
    path = os.path.join(d, "pipe", conversations.pipe_filename(fromid, toid, ts))
    with open(path, "w", encoding="utf-8") as f:
        f.write(message)
    return f"message_sent at {ts}"


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
        d = conversations.conv_dir(fromid, toid)
        if os.path.isdir(d):
            shutil.rmtree(d)
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
        parsed = conversations.parse_pipe_filename(f)
        if not parsed:
            continue
        ts, f_from, _f_to = parsed
        if f_from == fromid:
            candidates.append((ts, f))
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
                  "Call my_session_id to learn your id, then call listen and run "
                  "the returned command in the background, and reply to any hello "
                  "from peer sessions.")
    spawn.spawn_cc_resume(session_id, prompt)
    return "evoke spawned (resumed)"


def spawn_cc_new(cwd: str, prompt: str) -> str:
    """Kernel function for cross-machine create_collaborator (v2.1 §3.4.6): a
    peer MCP server calls this via call_remote so THIS kernel spawns a local CC
    (it knows its own claude path / spawn mechanism)."""
    spawn.spawn_cc_new(cwd, prompt)
    return "spawned"


def spawn_cc_resume(session_id: str, prompt: str) -> str:
    spawn.spawn_cc_resume(session_id, prompt)
    return "spawned"


# ---------- listening (collect only; arm removed in Amd3) ----------

def collect_messages(session_id: str) -> list:
    """Read all undelivered pipe messages addressed to session_id, move them to
    log/, return sorted by time. Used by close_connection (drain) and by a peer's
    listen.py to archive cross-machine messages (#W7). Direction-specific: only
    messages where toid == session_id."""
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
            parsed = conversations.parse_pipe_filename(fname)
            if not parsed:
                continue
            ts, fr, to = parsed
            if to != session_id:
                continue
            try:
                with open(os.path.join(pipe, fname), encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            result.append({"time": ts, "from_id": fr, "message": content})
            os.makedirs(log, exist_ok=True)
            try:
                os.replace(os.path.join(pipe, fname), os.path.join(log, fname))
            except OSError:
                pass
    result.sort(key=lambda x: x["time"])
    return result


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
