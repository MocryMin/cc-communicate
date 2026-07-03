"""Kernel functions callable via the queue RPC (core_plan "内核函数").

These operate on kernel state passed explicitly as parameters (no module
globals) so each function's state access is visible at the call site and the
functions are easy to test in isolation. kernel.py's _dispatch() routes RPC
requests here.

Implemented:
  Read-only:  query_session, check_alive, query_conversations
  Messaging:  send_message, withdraw, register_conversation, unregister_conversation

TODO (later increments): evoke, and the conversation orchestration (connect,
keep_listen, close_connection, create_collaborator) which lives in user-space
MCP tools, not here.
"""
from __future__ import annotations

import os
import shutil
import time

from paths import CONVERSATIONS_DIR
from proc import proc_start_time
import conversations
import spawn


def query_session(sessions: dict, session_id: str):
    """Return the session_inf dict for session_id, or None if unknown.
    (core_plan "内核函数 3".)"""
    return sessions.get(session_id)


def check_alive(alive_sessions: dict, session_id: str) -> int:
    """Is the session truly alive? Returns 1 (alive) or 0 (not alive).

    Four-step check (core_plan "内核函数 4" + technical challenge #3):
      1. session_id in alive_sessions? If not -> 0.
      2. Is its pid still a live OS process? If not, drop the record -> 0.
      3. Does the pid's current start_time match the recorded one? If not
         (PID reuse), drop the record -> 0.
      4. All pass -> 1.

    'Drop the record' mutates alive_sessions in place (passed by reference)."""
    info = alive_sessions.get(session_id)
    if not info:
        return 0
    pid = info.get("pid")
    recorded = info.get("start_time")
    if pid is None or recorded is None:
        return 0
    current = proc_start_time(pid)
    if current is None:
        alive_sessions.pop(session_id, None)  # process gone — stale record
        return 0
    if abs(current - float(recorded)) > 1.0:
        alive_sessions.pop(session_id, None)  # PID reuse — stale record
        return 0
    return 1


# ---------- conversation registration ----------

def register_conversation(alive_conversations: dict, sid_a: str, sid_b: str):
    """Mark a conversation as active. connect() calls this after its handshake
    succeeds. The key is an order-independent (sorted) tuple, so either peer
    can unregister. Also used by the idle-exit condition (kernel stays alive
    while any conversation is active)."""
    a, b = sorted([sid_a, sid_b])
    alive_conversations[(a, b)] = {"established_at": time.time()}


def unregister_conversation(alive_conversations: dict, sid_a: str, sid_b: str):
    a, b = sorted([sid_a, sid_b])
    alive_conversations.pop((a, b), None)


# ---------- messaging ----------

def send_message(alive_conversations: dict, fromid: str, toid: str, message: str) -> str:
    """Write a message to the conversation pipe (core_plan "用户函数 3", here as
    a kernel function). Fails if the conversation is not registered (connect not
    called, or peer closed it). Returns a status string."""
    a, b = sorted([fromid, toid])
    if (a, b) not in alive_conversations:
        return "failed, connection not registered"
    ts = int(time.time() * 1000)
    d = conversations.ensure_conv_dir(fromid, toid)
    path = os.path.join(d, "pipe", conversations.pipe_filename(fromid, toid, ts))
    with open(path, "w", encoding="utf-8") as f:
        f.write(message)
    return f"message_sent at {ts}"


def query_conversations(querier_sid: str) -> list:
    """List conversation partners for querier_sid (core_plan "用户函数 1").
    Returns [{partner: <sid>, ...}, ...]. Reads the conversations folder
    directly (not alive_conversations) — includes ended-but-not-withdrawn
    conversations, which is what compact-recovery needs."""
    result = []
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
            result.append({"partner": partner})
    return result


def withdraw(alive_conversations: dict, fromid: str, toid: str, init_connect: int = 0) -> str:
    """core_plan "内核函数 2".
    init_connect=1: remove the whole conversation folder + unregister.
    init_connect=0: remove fromid's latest undelivered message from the pipe."""
    if init_connect:
        d = conversations.conv_dir(fromid, toid)
        if os.path.isdir(d):
            shutil.rmtree(d)
        unregister_conversation(alive_conversations, fromid, toid)
        return "conversation withdrawn"
    # Remove fromid's latest undelivered pipe message.
    d = conversations.conv_dir(fromid, toid)
    pipe = os.path.join(d, "pipe")
    try:
        files = os.listdir(pipe)
    except FileNotFoundError:
        return "no messages"
    candidates = []  # (ts, filename)
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
    """Spawn a CC process in the session's cwd (core_plan "内核函数 5").

    Reads cwd from sessions[session_id] (populated by process_session_ctrl_event
    from the session's start event — core_plan #4). The spawned CC gets a NEW
    session_id (CC generates a fresh one per start); that new id is discovered
    later when its SessionStart hook fires → process_session_ctrl_event adds it
    to sessions + alive_sessions. So evoke does NOT itself update alive_sessions
    (no mutex needed here — the kernel's single-threaded loop handles the later
    update; see core_plan #9).

    Returns a status string. Fails if the session is unknown or has no cwd
    (e.g. pre-install session — core_plan #6, or an end event with no start)."""
    info = sessions.get(session_id)
    if not info:
        return "failed, session unknown"
    cwd = info.get("cwd")
    if not cwd:
        return "failed, no cwd recorded for session"
    if prompt is None:
        prompt = "You have been spawned for p2p communication by cc-communicate. Wait for incoming messages from peer sessions."
    spawn.spawn_cc(cwd, prompt)
    return "evoke spawned"
