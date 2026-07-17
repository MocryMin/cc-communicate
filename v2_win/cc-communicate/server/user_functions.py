"""User-function orchestration (core_plan "用户函数") - MCP tools that compose
kernel functions + cross-realm routing. These live in the MCP server process,
NOT the kernel.

v2 changes:
  - connect (Amd2): polls for the reply IN-PROCESS (no listener subprocess);
    cross-realm routing (find target's machine, register/send/poll on the
    conversation store = host for cross-machine, local otherwise).
  - hello + prompts (Amd4): hello explicitly requests an immediate reply;
    evoke/create_collaborator prompts instruct listen + reply.
  - hold_time default 300 (Amd6).
  - Cross-realm routing (Phase 2): query_session/check_alive/query_conversations/
    send_message/evoke/close_connection fan out to registered peer machines via
    rpc_client.call_remote. The kernel stays pure-local (v2.1 #W4/#W12).

Conversation store rule (v2.1 §1.3): same-machine conv -> that machine;
cross-machine conv -> HOST. So a WSL caller reaching a host target registers/
sends/polls on the HOST (remote, via /mnt/c/ read + call_remote archive); a
host caller reaching a WSL target stores on the host (local).
"""
from __future__ import annotations

import json
import os
import sys
import time

import rpc_client
import conversations
import spawn
import machine_identity
from paths import CONVERSATIONS_DIR, PLUGIN_ROOT, MACHINE_INFO_LOG_DIR

_REVIVE_WAIT = 30.0
# Floor for create_collaborator hold_time. A freshly-spawned CC can take
# >120s to boot + start its listener + reply on Windows (observed ~121s,
# T15); a shorter hold_time races _poll_reply's deadline and misses the
# reply by milliseconds. (T15)
_MIN_HOLD_TIME = 300
# T24: listen polls the kernel's atomic listen_scan at this interval. With the
# kernel's _MAX_SLEEP cut to 0.2s (B5), each poll returns within ~0.2s; this
# interval sets the message-pickup granularity.
_LISTEN_POLL = 1.0


# ---------- machine registry helpers ----------

def read_machine_info_log() -> list:
    """All registered peer-machine entries (list of dicts)."""
    entries = []
    try:
        names = os.listdir(MACHINE_INFO_LOG_DIR)
    except (FileNotFoundError, OSError):
        return entries
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(MACHINE_INFO_LOG_DIR, name), encoding="utf-8") as f:
                e = json.load(f)
            if isinstance(e, dict):
                entries.append(e)
        except (OSError, json.JSONDecodeError):
            continue
    return entries


def _local_type() -> str:
    return machine_identity.local_type()


def _host_entry():
    """The registered peer that is the Windows host (None if we are host or no peer)."""
    for m in read_machine_info_log():
        if m.get("type") == "win-host":
            return m
    return None


def _conv_store(toid: str):
    """Where the conv between the local caller and `toid` lives.
    Returns None (local kernel) or a remote machine entry (host, for a WSL
    caller reaching a host target)."""
    toid_local = bool(rpc_client.call("query_session", {"session_id": toid}))
    if toid_local or _local_type() == "win-host":
        return None  # same machine, or we are host (host stores cross-machine convs)
    return _host_entry()  # we are WSL, peer is host -> conv on host


def _find_target_machine(sid: str):
    """Return (is_local, machine_entry). machine_entry is None if local/not found."""
    if rpc_client.call("query_session", {"session_id": sid}):
        return True, None
    for m in read_machine_info_log():
        if rpc_client.call_remote(m, "query_session", {"session_id": sid}):
            return False, m
    return False, None


# ---------- routed store primitives ----------

def _register(caller, target, conv_remote):
    if conv_remote is None:
        conversations.ensure_conv_dir(caller, target)
        rpc_client.call("register_conversation", {"sid_a": caller, "sid_b": target})
    else:
        rpc_client.call_remote(conv_remote, "create_conversation_folder", {"id1": caller, "id2": target})
        rpc_client.call_remote(conv_remote, "register_conversation", {"sid_a": caller, "sid_b": target})


def _send(fromid, toid, message, conv_remote) -> str:
    if conv_remote is None:
        return rpc_client.call("send_message", {"fromid": fromid, "toid": toid, "message": message})
    return rpc_client.call_remote(conv_remote, "send_message",
                                  {"fromid": fromid, "toid": toid, "message": message})


def _withdraw(fromid, toid, init_connect, conv_remote):
    if conv_remote is None:
        return rpc_client.call("withdraw", {"fromid": fromid, "toid": toid, "init_connect": init_connect})
    return rpc_client.call_remote(conv_remote, "withdraw",
                                  {"fromid": fromid, "toid": toid, "init_connect": init_connect})


def _collect(sid, conv_remote):
    if conv_remote is None:
        return rpc_client.call("collect_messages", {"session_id": sid})
    return rpc_client.call_remote(conv_remote, "collect_messages", {"session_id": sid})


def _unregister(sid, toid, conv_remote):
    if conv_remote is None:
        return rpc_client.call("unregister_conversation", {"sid_a": sid, "sid_b": toid})
    return rpc_client.call_remote(conv_remote, "unregister_conversation", {"sid_a": sid, "sid_b": toid})


def _conv_exists(caller, target, conv_remote) -> bool:
    name = os.path.basename(conversations.conv_dir(caller, target))
    if conv_remote is None:
        return os.path.isdir(os.path.join(CONVERSATIONS_DIR, name))
    return os.path.isdir(os.path.join(conv_remote["data_dir"], "conversations", name))


# ---------- in-process reply poll (Amd2) ----------

def _scan_pipe(pipe_dir, want_toid):
    out = []
    try:
        files = os.listdir(pipe_dir)
    except (FileNotFoundError, PermissionError, OSError):
        return out
    for fname in files:
        parsed = conversations.parse_pipe_filename(fname)
        if parsed and parsed[2] == want_toid:
            out.append((fname, os.path.join(pipe_dir, fname)))
    return out


def _pipe_dir_for(caller, target, conv_remote) -> str:
    name = os.path.basename(conversations.conv_dir(caller, target))
    if conv_remote is None:
        return os.path.join(CONVERSATIONS_DIR, name, "pipe")
    return os.path.join(conv_remote["data_dir"], "conversations", name, "pipe")


def _archive_reply(conv_remote, caller, fname, path):
    """Claim the reply file (pipe->log). Local: direct os.replace. Remote
    (we're read-only on host conversations): delegate to host kernel
    collect_messages (archives all undelivered for caller - fine, the reply is
    among them)."""
    if conv_remote is None:
        # log_dir is derived from the pipe path directly (pipe -> log); we do NOT
        # need conv_dir here. An earlier version computed conv_name via
        # conv_dir(caller, None) as dead code - but sorted([str, None]) raises
        # TypeError, crashing connect right when the reply arrived. (T12)
        log_dir = os.path.dirname(path).replace(os.sep + "pipe", os.sep + "log")
        try:
            os.makedirs(log_dir, exist_ok=True)
            os.replace(path, os.path.join(log_dir, fname))
        except OSError:
            pass
    else:
        rpc_client.call_remote(conv_remote, "collect_messages", {"session_id": caller})


def _claim_reply(pipe_dir, caller, target, conv_remote, hello_ts=0):
    """Scan pipe_dir once for target's reply (a pipe file with toid==caller,
    fromid==target). Returns the reply content (archiving the file), or None.
    Stale messages (ts <= hello_ts) are skipped (C3): a prior [CONNECTION CLOSED]
    notice left in the pipe, or the hello itself, must not be mistaken for the
    reply (the hello and any close notice predate the hello we just sent)."""
    for fname, path in _scan_pipe(pipe_dir, caller):
        parsed = conversations.parse_pipe_filename(fname)
        if not parsed or parsed[1] != target:
            continue  # not from target
        if parsed[0] <= hello_ts:
            continue  # C3: stale (not newer than the hello) - skip
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            continue  # C5: skip malformed/undecodable files
        _archive_reply(conv_remote, caller, fname, path)
        return content
    return None


def _poll_reply(caller, target, hold_time, conv_remote, hello_ts=0):
    """Block up to hold_time scanning (in-process) for target's reply (a pipe
    file with toid==caller, fromid==target). Returns the reply content, or None
    on timeout. Reads content BEFORE archiving (Amd2: no false-timeout even if a
    stray listener races us). A final scan after the deadline catches a reply
    that landed in the last poll window. (T15) hello_ts filters stale messages
    (C3)."""
    pipe_dir = _pipe_dir_for(caller, target, conv_remote)
    deadline = time.time() + hold_time
    while time.time() < deadline:
        reply = _claim_reply(pipe_dir, caller, target, conv_remote, hello_ts)
        if reply is not None:
            return reply
        time.sleep(0.5)
    # final scan: a reply may have landed in the last 0.5s poll window. (T15)
    return _claim_reply(pipe_dir, caller, target, conv_remote, hello_ts)


# ---------- tools ----------

def my_session_id() -> str:
    """Discover this CC's own session_id. Walks the process tree to the claude
    binary ancestor (resolve_claude, Amd1), then looks up the session by pid.
    Returns the sid or 'failed, ...'."""
    from proc import resolve_claude
    pid, _ = resolve_claude(os.getpid())
    if pid is None:
        return "failed, could not find claude ancestor"
    sid = rpc_client.call("session_by_pid", {"pid": pid})
    return sid if sid else "failed, no session recorded for claude pid " + str(pid)


def query_session(session_id: str):
    """Local first, then each registered peer machine (cross-realm fan-out)."""
    r = rpc_client.call("query_session", {"session_id": session_id})
    if r:
        return r
    for m in read_machine_info_log():
        r = rpc_client.call_remote(m, "query_session", {"session_id": session_id})
        if r:
            return r
    return None


def check_alive(session_id: str) -> int:
    if rpc_client.call("check_alive", {"session_id": session_id}) == 1:
        return 1
    for m in read_machine_info_log():
        if rpc_client.call_remote(m, "check_alive", {"session_id": session_id}) == 1:
            return 1
    return 0


def query_conversations(session_id: str) -> dict:
    """v2 dict format: {partner_sid: {...info}, ...}. Merges local + peers."""
    out = {}
    local = rpc_client.call("query_conversations", {"session_id": session_id})
    if isinstance(local, dict):
        out.update(local)
    for m in read_machine_info_log():
        r = rpc_client.call_remote(m, "query_conversations", {"session_id": session_id})
        if isinstance(r, dict):
            out.update(r)  # sid uniqueness -> drop dups
    return out


def send_message(fromid: str, toid: str, message: str) -> str:
    """Route by the conversation store (host for cross-machine, else local)."""
    conv_remote = _conv_store(toid)
    return _send(fromid, toid, message, conv_remote)


def evoke(session_id: str) -> str:
    """Revive a dead CC on whatever machine it lives on (local or remote)."""
    is_local, machine = _find_target_machine(session_id)
    if not is_local and machine is None:
        return "failed, session not exists"
    if is_local:
        return rpc_client.call("evoke", {"session_id": session_id})
    return rpc_client.call_remote(machine, "evoke", {"session_id": session_id})


def listen(session_id: str, acked_ts: int = 0, timeout: int = 30) -> dict:
    """BLOCKING listen with timestamp ACK (T24). Polls the kernel's atomic
    listen_scan: archives (to==session_id, ts<=acked_ts) [messages you already
    confirmed] and returns newer messages + a new watermark. CALL THIS IN A
    LOOP: pass the returned `watermark` as `acked_ts` on the next call. Cancel-
    safe - a cancelled listen archived only what you'd already confirmed in a
    prior call; the just-returned messages stay in the pipe and re-deliver next
    time. Cross-realm: a WSL caller also scans the host (where cross-machine
    convs live). Never invoke listen.py directly or write a shell listener."""
    deadline = time.time() + timeout
    host = _host_entry()  # None when we ARE the host -> all our convs are local
    while time.time() < deadline:
        messages = []
        watermark = acked_ts
        # local atomic scan (kernel single-thread -> no concurrent writes)
        try:
            r = rpc_client.call("listen_scan", {"sid": session_id, "acked_ts": acked_ts})
        except Exception:
            r = None  # transient kernel issue -> treat as empty, retry
        if isinstance(r, dict):
            if r.get("messages"):
                messages.extend(r["messages"])
            wm = r.get("watermark", acked_ts)
            if wm > watermark:
                watermark = wm
        # cross-realm: a WSL caller's cross-machine convs are stored on the host
        if host is not None:
            rr = rpc_client.call_remote(host, "listen_scan",
                                        {"sid": session_id, "acked_ts": acked_ts})
            if isinstance(rr, dict):
                if rr.get("messages"):
                    messages.extend(rr["messages"])
                wm = rr.get("watermark", acked_ts)
                if wm > watermark:
                    watermark = wm
        if messages:
            messages.sort(key=lambda x: x.get("time", 0))
            return {"messages": messages, "watermark": watermark}
        time.sleep(_LISTEN_POLL)
    return {"messages": [], "watermark": acked_ts}


def connect(caller_sid: str, target_sid: str, hold_time: int = 300) -> str:
    """Establish a p2p connection to target_sid (Amd2 in-process poll + Phase 2
    routing). Flow: find target's machine -> check_alive (revive if dead) ->
    register + send hello on the conv store -> poll in-process for the reply ->
    succeed / withdraw on fail. Blocks up to hold_time."""
    # 1. locate target
    is_local, target_machine = _find_target_machine(target_sid)
    if not is_local and target_machine is None:
        return "failed, target session not exists"

    # 2. check_alive on target's machine
    if is_local:
        alive = rpc_client.call("check_alive", {"session_id": target_sid})
    else:
        alive = rpc_client.call_remote(target_machine, "check_alive", {"session_id": target_sid})

    # 3. revive if dead
    if alive != 1:
        ev = evoke(target_sid)
        if "failed" in str(ev):
            return "failed, evoke: " + str(ev)
        deadline = time.time() + _REVIVE_WAIT
        while time.time() < deadline:
            time.sleep(1)
            if is_local:
                a = rpc_client.call("check_alive", {"session_id": target_sid})
            else:
                a = rpc_client.call_remote(target_machine, "check_alive", {"session_id": target_sid})
            if a == 1:
                break
        else:
            return "failed, target did not come alive after evoke (waited %ss)" % _REVIVE_WAIT

    # 4. conversation store (host for cross-machine, else local)
    conv_remote = _conv_store(target_sid)
    init_connect = 0 if _conv_exists(caller_sid, target_sid, conv_remote) else 1

    # 5. register + send hello
    _register(caller_sid, target_sid, conv_remote)
    hello = ("connect hello from " + caller_sid + ". This is a p2p connection "
             "request - reply immediately with send_message(your_session_id, "
             + caller_sid + ", <any message>) to establish the channel.")
    send_res = _send(caller_sid, target_sid, hello, conv_remote)
    if "failed" in str(send_res):
        if init_connect:
            _withdraw(caller_sid, target_sid, 1, conv_remote)
        return "failed, send hello: " + str(send_res)
    # Parse the hello's timestamp so _poll_reply can reject stale messages (C3):
    # a prior [CONNECTION CLOSED] notice or the hello itself must not be read as
    # the reply. send_res looks like "message_sent at <ts_ms>".
    try:
        hello_ts = int(str(send_res).rsplit("at ", 1)[1])
    except (ValueError, IndexError):
        hello_ts = 0

    # 6. in-process poll for the reply (Amd2 - no listener subprocess)
    reply = _poll_reply(caller_sid, target_sid, hold_time, conv_remote, hello_ts)
    if reply is not None:
        return "connect succeed; reply: " + reply

    # 7. timeout -> clean up
    _withdraw(caller_sid, target_sid, init_connect, conv_remote)
    return "connect failed, timeout waiting for reply"


def close_connection(session_id: str, toid: str, acked_ts: int = 0) -> dict:
    """Close the connection to toid (T24: best-effort, non-blocking). Uploads
    the caller's latest ACK watermark to the kernel (persisted, so it survives
    compact/restart - the CC can recover it via query_my_ACK_timestamp), sends
    a close notice to the peer (with an instruction to upload its own ts), and
    unregisters. Does NOT clean up the pipe - per the ts-based ACK design,
    un-acked messages stay and are archived lazily via the watermark on the next
    listen (or removed by withdraw). Never raises - a failure here doesn't
    prevent the caller from leaving."""
    conv_remote = _conv_store(toid)
    notice = ("[CONNECTION CLOSED by " + session_id + "] To close your side and "
              "preserve your message state, call close_connection(your_sid, " +
              session_id + ", your_latest_ACK_ts). If you have lost your ts, call "
              "query_my_ACK_timestamp(your_sid) first, then close_connection.")
    # 1. upload the caller's watermark to the home kernel (persisted)
    try:
        rpc_client.call("upload_ack_timestamp", {"sid": session_id, "ts": acked_ts})
    except Exception:
        pass
    # 2. notify the peer + unregister (fire-and-forget if the conv is remote)
    try:
        if conv_remote is None:
            rpc_client.call("send_message",
                            {"fromid": session_id, "toid": toid, "message": notice})
            rpc_client.call("unregister_conversation",
                            {"sid_a": session_id, "sid_b": toid})
        else:
            rpc_client.submit_remote_noblock(
                conv_remote, "send_message",
                {"fromid": session_id, "toid": toid, "message": notice})
            rpc_client.submit_remote_noblock(
                conv_remote, "unregister_conversation",
                {"sid_a": session_id, "sid_b": toid})
    except Exception:
        pass  # best-effort: never block the caller's exit on a notify/unregister failure
    return {"closed": True}


def query_my_ACK_timestamp(session_id: str) -> int:
    """Recover the kernel's stored ACK watermark for session_id (T24). Call this
    after a compact / long gap / kernel restart if you've lost your latest ts,
    then use the returned value as `acked_ts` on your next listen."""
    try:
        r = rpc_client.call("query_ack_timestamp", {"sid": session_id})
    except Exception:
        r = 0
    return r if isinstance(r, int) else 0


def create_collaborator(caller_sid: str, cwd: str, hold_time: int = 300,
                        machine=None) -> str:
    """Spawn a NEW CC in cwd (on `machine` if given, else local), wait for it to
    register, then connect. The new CC must have the plugin installed."""
    # Enforce a floor: the spawned CC cold-starts (boot + tool load +
    # listener + reply) and can exceed 120s on Windows; a shorter hold_time
    # races _poll_reply. See T15. (hold_time default 300 == the floor.)
    hold_time = max(hold_time, _MIN_HOLD_TIME)
    prompt = ("You are a new collaborator spawned by cc-communicate. "
              "First call my_session_id to learn your id. Then call listen "
              "(your_id, acked_ts, timeout) - it blocks and returns "
              "{messages, watermark}. Pass 0 as acked_ts the FIRST time; on "
              "every later listen pass the watermark the previous listen "
              "returned (this lets the kernel archive only what you've "
              "confirmed - never drop or duplicate it). When a peer sends you "
              "a hello, reply with send_message(your_id, peer_id, <message>) "
              "- do NOT call connect to reply. KEEP LISTENING: after each "
              "listen returns, process any messages and call listen again "
              "(with the latest watermark), in a loop, until you call "
              "close_connection(your_id, peer_id, your_latest_watermark) to "
              "end the conversation. If you ever lose your watermark (compact / "
              "long gap), call query_my_ACK_timestamp(your_id) to recover it. "
              "Never invoke listen.py directly, never write a shell loop, never "
              "nohup a listener - only use the listen tool.")
    since_ts = int(time.time() * 1000)
    if machine is None:
        spawn.spawn_cc_new(cwd, prompt)
        find = lambda: rpc_client.call("find_new_session", {"cwd": cwd, "since_ts": since_ts})
    else:
        rpc_client.call_remote(machine, "spawn_cc_new", {"cwd": cwd, "prompt": prompt})
        find = lambda: rpc_client.call_remote(machine, "find_new_session",
                                              {"cwd": cwd, "since_ts": since_ts})
    deadline = time.time() + 30
    new_sid = None
    while time.time() < deadline:
        time.sleep(1)
        new_sid = find()
        if new_sid:
            break
    if not new_sid:
        return "failed, new session did not register within 30s (is the plugin installed for new CCs?)"
    return connect(caller_sid, new_sid, hold_time)


def query_machines() -> dict:
    """Registered peer machines: {id: entry, ...}."""
    return {m.get("id"): m for m in read_machine_info_log()}


def help_connect_machines() -> str:
    """Return the cross-machine handshake playbook (C4). The CC calls this when
    the user wants to link this machine to a peer (e.g. 'help me connect
    machines', 'connect WSL to host', 'register the other machine'), then follows
    the steps - asking clarifications and driving both sides' handshake scripts
    itself (cross-realm exec, like _wake_remote)."""
    guide_path = os.path.join(PLUGIN_ROOT, "server", "handshake_guide.md")
    try:
        with open(guide_path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        return "handshake guide not found at %s: %s" % (guide_path, e)
