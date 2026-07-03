"""Kernel functions callable via the queue RPC (core_plan "内核函数").

These operate on kernel state passed explicitly as parameters (no module
globals) so each function's state access is visible at the call site and the
functions are easy to test in isolation. kernel.py's _dispatch() routes RPC
requests here.

Implemented:
  Read-only:  query_session, check_alive, query_conversations
  Messaging:  send_message, withdraw, register_conversation, unregister_conversation
  Spawning:   evoke
  Listening:  arm_poller, collect_messages  (the poller itself is listen_poller.py)

TODO (later increments): connect, close_connection, create_collaborator —
user-space MCP tools that compose the functions above (connect's handshake,
create_collaborator's spawn+connect). keep_listen as a user-facing concept is
the arm_poller -> listen_poller.py -> collect_messages pattern, already wired.
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
    """Write JSON via temp file + os.replace (atomic on same filesystem)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


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


# ---------- listening (keep_listen: arm_poller + listen_poller.py + collect_messages) ----------

def arm_poller(session_id: str, timeout: int = 1800) -> dict:
    """Write a poller config for session_id and return the command CC should run
    in the background (core_plan "用户函数 4a"). The poller (listen_poller.py)
    exits 0 when a new message addressed to session_id arrives — anywhere,
    including a newly-created conversation folder — or 2 on timeout.

    Baseline = current undelivered count for session_id
    (conversations.count_undelivered), so only messages arriving AFTER arming
    are detected. The poller re-scans all folders each cycle, so a folder
    appearing after arming (a new partner's first message) is still detected."""
    baseline = conversations.count_undelivered(session_id)
    deadline = time.time() + timeout
    config = {"session_id": session_id, "baseline": baseline, "deadline": deadline}
    os.makedirs(SERVER_DATA_DIR, exist_ok=True)
    config_path = os.path.join(SERVER_DATA_DIR, f"poller_{session_id}.json")
    _atomic_write_json(config_path, config)
    cmd = f'python "{PLUGIN_ROOT}/server/listen_poller.py" "{session_id}"'
    return {"armed": True, "command": cmd, "timeout": timeout, "baseline": baseline}


def collect_messages(session_id: str) -> list:
    """Read all undelivered pipe messages addressed to session_id, move them to
    log/, return sorted by time (core_plan "用户函数 4c").

    Returns [{time, from_id, message}, ...]. Only messages where toid ==
    session_id are collected (a conversation's pipe holds both directions; each
    peer collects only its own). Collected files move pipe -> log so the next
    collect doesn't re-return them."""
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
                continue  # addressed to the peer, not us
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
