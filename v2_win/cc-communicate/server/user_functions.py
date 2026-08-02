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
import message_record
import spawn
import machine_identity
import uuid
from paths import CONVERSATIONS_DIR, PLUGIN_ROOT, MACHINE_INFO_LOG_DIR
from result import Code
from rpc_client import KernelError


def _ok(data=None):
    return {"ok": True, "code": None, "message": None, "data": data, "retryable": False}


def _err(code, message, data=None, retryable=False):
    return {"ok": False, "code": code, "message": message, "data": data, "retryable": retryable}


def _kernel_err(e: KernelError):
    """Local kernel failure -> INTERNAL (entry validation already ran; a kernel
    error here is a bug or a crashed kernel)."""
    return _err(Code.INTERNAL, str(e))


def _remote_err():
    return _err(Code.PEER_UNREACHABLE, "peer machine unreachable")

# D4: the legacy create_collaborator predates the standard default - mark
# its bypass mode in the returned string (suffix; prefixes stay byte-exact).
_LEGACY_BYPASS_SUFFIX = " ; permission_mode=bypass (legacy)"
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


def _send(fromid, toid, message, conv_remote, correlation_id=None, kind=None,
          artifact_refs=None):
    # HP-01/HP-03: one message_id per LOGICAL send, generated here so every
    # funnel (send_message / connect hello / close notice) gets dedup for
    # free. The rpc layer reuses it as the operation_id, so a transport retry
    # replays the journaled result and a domain retry dedups on the filename.
    mid = uuid.uuid4().hex
    args = {"fromid": fromid, "toid": toid, "message": message, "message_id": mid}
    if correlation_id is not None:
        args["correlation_id"] = correlation_id
    if kind is not None:
        args["kind"] = kind
    if artifact_refs is not None:
        args["artifact_refs"] = artifact_refs
    if conv_remote is None:
        return rpc_client.call("send_message", args, operation_id=mid)
    return rpc_client.call_remote(conv_remote, "send_message", args, operation_id=mid)


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


def _get_connection_info(sid_a, sid_b, conv_remote):
    if conv_remote is None:
        return rpc_client.call("get_connection_info", {"sid_a": sid_a, "sid_b": sid_b})
    return rpc_client.call_remote(conv_remote, "get_connection_info",
                                  {"sid_a": sid_a, "sid_b": sid_b})


def _activate_connection(sid_a, sid_b, connection_id, conv_remote):
    if conv_remote is None:
        return rpc_client.call("activate_connection",
                               {"sid_a": sid_a, "sid_b": sid_b,
                                "connection_id": connection_id})
    return rpc_client.call_remote(conv_remote, "activate_connection",
                                  {"sid_a": sid_a, "sid_b": sid_b,
                                   "connection_id": connection_id})


def _deactivate_connection(sid_a, sid_b, conv_remote):
    if conv_remote is None:
        return rpc_client.call("deactivate_connection",
                               {"sid_a": sid_a, "sid_b": sid_b})
    return rpc_client.submit_remote_noblock(conv_remote, "deactivate_connection",
                                            {"sid_a": sid_a, "sid_b": sid_b})


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
        info = conversations.parse_any_pipe_filename(fname)
        if info and info["to_id"] == want_toid:
            out.append((fname, os.path.join(pipe_dir, fname), info))
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


def _claim_reply(pipe_dir, caller, target, conv_remote, hello_ts=0, connection_id=None):
    """Scan pipe_dir once for target's reply (toid==caller, fromid==target).
    HP-05: a record whose correlation_id == connection_id is the reply -
    foreign messages can never be misread. Legacy fallback (D9, one release):
    when no correlation_id matches and EXACTLY ONE candidate exists (from/to +
    newer than hello_ts), accept it - that is unambiguous. Returns the reply
    content (archiving the file), or None."""
    candidates = []
    for fname, path, info in _scan_pipe(pipe_dir, caller):
        if info["from_id"] != target:
            continue
        if info["format"] == "record":
            rec = message_record.read_record(path)
            if not rec:
                continue
            ts = rec.get("created_at_ms", 0)
            content = (rec.get("payload") or {}).get("text")
            if content is None:
                continue
            corr = rec.get("correlation_id")
        else:
            ts = info["ts"]
            corr = None
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue  # C5: skip malformed/undecodable files
        if ts <= hello_ts:
            continue  # C3: stale (not newer than the hello) - skip
        if connection_id is not None and corr == connection_id:
            _archive_reply(conv_remote, caller, fname, path)
            return content
        candidates.append((fname, path, content))
    if connection_id is not None and len(candidates) == 1:
        fname, path, content = candidates[0]
        _archive_reply(conv_remote, caller, fname, path)
        return content
    return None


def _poll_reply(caller, target, hold_time, conv_remote, hello_ts=0, connection_id=None):
    """Block up to hold_time scanning (in-process) for target's reply (a pipe
    file with toid==caller, fromid==target). Returns the reply content, or None
    on timeout. Reads content BEFORE archiving (Amd2: no false-timeout even if a
    stray listener races us). A final scan after the deadline catches a reply
    that landed in the last poll window. (T15) hello_ts filters stale messages
    (C3); connection_id selects the correlated reply (HP-05)."""
    pipe_dir = _pipe_dir_for(caller, target, conv_remote)
    deadline = time.time() + hold_time
    while time.time() < deadline:
        reply = _claim_reply(pipe_dir, caller, target, conv_remote, hello_ts, connection_id)
        if reply is not None:
            return reply
        time.sleep(0.5)
    # final scan: a reply may have landed in the last 0.5s poll window. (T15)
    return _claim_reply(pipe_dir, caller, target, conv_remote, hello_ts, connection_id)


# ---------- tools ----------

def my_session_id() -> dict:
    """Discover this CC's own session_id. Walks the process tree to the claude
    binary ancestor (resolve_claude, Amd1), then looks up the session by pid.
    Returns ok(sid) or err(...)."""
    from proc import resolve_claude
    pid, _ = resolve_claude(os.getpid())
    if pid is None:
        return _err(Code.INTERNAL, "could not find claude ancestor")
    try:
        sid = rpc_client.call("session_by_pid", {"pid": pid})
    except KernelError as e:
        return _kernel_err(e)
    if not sid:
        return _err(Code.NOT_FOUND, f"no session recorded for claude pid {pid}")
    return _ok(sid)


def query_session(session_id: str) -> dict:
    """Local first, then each registered peer machine (cross-realm fan-out).
    ok(session_inf) or ok(None) when unknown everywhere."""
    try:
        r = rpc_client.call("query_session", {"session_id": session_id})
        if r:
            return _ok(r)
    except KernelError:
        pass
    for m in read_machine_info_log():
        r = rpc_client.call_remote(m, "query_session", {"session_id": session_id})
        if r:
            return _ok(r)
    return _ok(None)


def check_alive(session_id: str) -> dict:
    """1 if the session is truly alive on this machine or any registered peer;
    0 otherwise; err(INTERNAL) if the local kernel itself errors out."""
    try:
        if rpc_client.call("check_alive", {"session_id": session_id}) == 1:
            return _ok(1)
    except KernelError as e:
        return _kernel_err(e)
    for m in read_machine_info_log():
        if rpc_client.call_remote(m, "check_alive", {"session_id": session_id}) == 1:
            return _ok(1)
    return _ok(0)


def query_conversations(session_id: str) -> dict:
    """v2 dict format: {partner_sid: {...info}, ...}. Merges local + peers."""
    out = {}
    try:
        local = rpc_client.call("query_conversations", {"session_id": session_id})
    except KernelError:
        local = None
    if isinstance(local, dict):
        out.update(local)
    for m in read_machine_info_log():
        r = rpc_client.call_remote(m, "query_conversations", {"session_id": session_id})
        if isinstance(r, dict):
            out.update(r)  # sid uniqueness -> drop dups
    return _ok(out)


def send_message(fromid: str, toid: str, message: str,
                 correlation_id: str = None, kind: str = None,
                 artifact_refs: list = None) -> dict:
    """Route by the conversation store (host for cross-machine, else local).
    ok({message_id, ts}) on success; err(NOT_FOUND) when the conversation is
    not registered; err(INTERNAL/PEER_UNREACHABLE) on transport failure."""
    conv_remote = _conv_store(toid)
    try:
        r = _send(fromid, toid, message, conv_remote,
                  correlation_id=correlation_id, kind=kind,
                  artifact_refs=artifact_refs)
    except KernelError as e:
        return _kernel_err(e)
    if r is None:
        return _remote_err()
    if r.get("sent"):
        return _ok({"message_id": r.get("message_id"), "ts": r.get("ts")})
    if r.get("backlog") is not None:
        # HP-09: peer hasn't acked enough - retry after it drains
        return _err(Code.RESOURCE_EXHAUSTED,
                    r.get("reason", "backlog full"),
                    data=r.get("backlog"), retryable=True)
    return _err(Code.NOT_FOUND, r.get("reason", "send failed"))


def evoke(session_id: str, permission_mode: str = "bypass") -> dict:
    """Revive a dead CC on whatever machine it lives on (local or remote).
    permission_mode (HP-10/D4): "bypass" default - resume of an established
    session is not a new trust decision; pass "standard" to override."""
    is_local, machine = _find_target_machine(session_id)
    if not is_local and machine is None:
        return _err(Code.NOT_FOUND, "session not exists")
    try:
        if is_local:
            r = rpc_client.call("evoke",
                                {"session_id": session_id,
                                 "permission_mode": permission_mode})
        else:
            r = rpc_client.call_remote(machine, "evoke",
                                       {"session_id": session_id,
                                        "permission_mode": permission_mode})
    except KernelError as e:
        return _kernel_err(e)
    if r is None:
        return _remote_err()
    if r.get("evoked"):
        return _ok({"evoked": True, "session_id": r.get("session_id")})
    return _err(Code.NOT_FOUND, r.get("reason", "evoke failed"))


def register_conversation(sid_a: str, sid_b: str) -> dict:
    """Mark a LOCAL conversation active (low-level; connect handles routing).
    Exposed for bootstrapping/testing."""
    try:
        r = rpc_client.call("register_conversation", {"sid_a": sid_a, "sid_b": sid_b})
    except KernelError as e:
        return _kernel_err(e)
    return _ok(r if isinstance(r, dict) else {"ok": True})


def unregister_conversation(sid_a: str, sid_b: str) -> dict:
    """Mark a LOCAL conversation inactive (low-level)."""
    try:
        r = rpc_client.call("unregister_conversation", {"sid_a": sid_a, "sid_b": sid_b})
    except KernelError as e:
        return _kernel_err(e)
    return _ok(r if isinstance(r, dict) else {"ok": True})


def withdraw(fromid: str, toid: str, init_connect: int = 0,
             message_id: str = None) -> dict:
    """Withdraw a message or whole LOCAL conversation (low-level).
    init_connect=1: remove the whole folder + unregister; =0: default legacy
    mode withdraws fromid's latest undelivered message (non-idempotent).
    message_id: withdraw that EXACT message (retry-safe; preferred)."""
    try:
        r = rpc_client.call("withdraw", {"fromid": fromid, "toid": toid,
                                         "init_connect": init_connect,
                                         "message_id": message_id})
    except KernelError as e:
        return _kernel_err(e)
    if r and r.get("withdrawn"):
        return _ok(r)
    return _err(Code.NOT_FOUND, (r or {}).get("reason", "withdraw failed"))


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
            return _ok({"messages": messages, "watermark": watermark})
        time.sleep(_LISTEN_POLL)
    return _ok({"messages": [], "watermark": acked_ts})


def connect(caller_sid: str, target_sid: str, connection_id: str = None,
            hold_time: int = 300) -> dict:
    """Establish a p2p connection to target_sid (Amd2 in-process poll + Phase 2
    routing). HP-05: connection_id (caller-supplied or generated) correlates
    the reply via the hello's correlation_id; info.json enforces ONE active
    connection per pair (D9) - a retry with the same id returns the current
    state, a different id is CONFLICT. Blocks up to hold_time."""
    hold_time = max(hold_time, _MIN_HOLD_TIME)
    conn_id = connection_id or uuid.uuid4().hex
    # 1. locate target
    is_local, target_machine = _find_target_machine(target_sid)
    if not is_local and target_machine is None:
        return _err(Code.NOT_FOUND, "target session not exists")
    # 2. check_alive on target's machine
    try:
        if is_local:
            alive = rpc_client.call("check_alive", {"session_id": target_sid})
        else:
            alive = rpc_client.call_remote(target_machine, "check_alive",
                                           {"session_id": target_sid})
    except KernelError:
        alive = 0
    # 3. revive if dead
    if alive != 1:
        ev = evoke(target_sid)
        if not ev["ok"]:
            return _err(Code.NOT_ALIVE, "evoke: " + str(ev.get("message")))
        deadline = time.time() + _REVIVE_WAIT
        while time.time() < deadline:
            time.sleep(1)
            try:
                if is_local:
                    a = rpc_client.call("check_alive", {"session_id": target_sid})
                else:
                    a = rpc_client.call_remote(target_machine, "check_alive",
                                               {"session_id": target_sid})
            except KernelError:
                a = 0
            if a == 1:
                break
        else:
            return _err(Code.NOT_ALIVE,
                        f"target did not come alive after evoke (waited {_REVIVE_WAIT}s)",
                        retryable=True)
    # 4. conversation store (host for cross-machine, else local) + active check
    conv_remote = _conv_store(target_sid)
    info = _get_connection_info(caller_sid, target_sid, conv_remote)
    if info and info.get("status") == "active":
        if info.get("connection_id") == conn_id:
            return _ok({"connection_id": conn_id, "reply": None,
                        "established_at_ms": info.get("established_at_ms"),
                        "reused": True})
        return _err(Code.CONFLICT, "connection already active",
                    data={"current_connection_id": info.get("connection_id"),
                          "status": "active"})
    init_connect = 0 if _conv_exists(caller_sid, target_sid, conv_remote) else 1
    # 5. register + send hello (kind=hello, correlation_id=connection_id)
    _register(caller_sid, target_sid, conv_remote)
    hello = ("connect hello from " + caller_sid + ". This is a p2p connection "
             "request - reply immediately with send_message(your_session_id, "
             + caller_sid + ", <any message>) to establish the channel.")
    try:
        send_res = _send(caller_sid, target_sid, hello, conv_remote,
                         correlation_id=conn_id, kind="hello")
    except KernelError as e:
        if init_connect:
            _withdraw(caller_sid, target_sid, 1, conv_remote)
        return _kernel_err(e)
    if send_res is None:
        if init_connect:
            _withdraw(caller_sid, target_sid, 1, conv_remote)
        return _remote_err()
    if not send_res.get("sent"):
        if init_connect:
            _withdraw(caller_sid, target_sid, 1, conv_remote)
        return _err(Code.INTERNAL, "send hello: " + str(send_res.get("reason")))
    hello_ts = send_res.get("ts") or 0
    # 6. in-process poll for the correlation-matched reply (HP-05)
    reply = _poll_reply(caller_sid, target_sid, hold_time, conv_remote,
                        hello_ts, conn_id)
    if reply is not None:
        act = _activate_connection(caller_sid, target_sid, conn_id, conv_remote)
        if act and act.get("activated"):
            return _ok({"connection_id": conn_id, "reply": reply,
                        "established_at_ms": act.get("established_at_ms"),
                        "reused": bool(act.get("reused"))})
        # race: another connect activated first - report its state
        info2 = _get_connection_info(caller_sid, target_sid, conv_remote)
        if info2 and info2.get("connection_id") != conn_id:
            return _err(Code.CONFLICT, "connection already active",
                        data={"current_connection_id": info2.get("connection_id"),
                              "status": info2.get("status")})
        return _ok({"connection_id": conn_id, "reply": reply,
                    "established_at_ms": int(time.time() * 1000), "reused": False})
    # 7. timeout -> clean up
    _withdraw(caller_sid, target_sid, init_connect, conv_remote)
    return _err(Code.TIMEOUT, "timeout waiting for reply", retryable=True)


def close_connection(session_id: str, toid: str, acked_ts: int = 0,
                     cursors: dict = None) -> dict:
    """Close the connection to toid (T24: best-effort, non-blocking). Uploads
    the caller's latest ACK watermark to the kernel (persisted, so it survives
    compact/restart), sends a close notice to the peer, and unregisters. Also
    uploads per-store cursors when given (HP-02): each cursor goes ONLY to the
    kernel owning that store; unknown store ids are ignored. Does NOT clean up
    the pipe - per the ts-based ACK design, un-acked messages stay and are
    archived lazily via the watermark on the next listen. N-01: best-effort
    steps that actually failed are reported via `degraded_steps` in data
    (absent when everything succeeded, so the clean shape is unchanged) -
    still non-blocking, never raises, `closed` stays True."""

    conv_remote = _conv_store(toid)
    notice = ("[CONNECTION CLOSED by " + session_id + "] To close your side and "
              "preserve your message state, call close_connection(your_sid, " +
              session_id + ", your_latest_ACK_ts). If you have lost your ts, call "
              "query_my_ACK_timestamp(your_sid) first, then close_connection.")
    degraded_steps = []
    # 1. upload the caller's watermark to the home kernel (persisted)
    try:
        rpc_client.call("upload_ack_timestamp", {"sid": session_id, "ts": acked_ts})
    except Exception:
        degraded_steps.append("upload_ack_timestamp")
    # 1b. upload per-store cursors (HP-02) - each to its owning kernel only
    if cursors:
        local_id, host = _store_ids()
        host_id = host.get("id") if host else None
        for store_id, seq in cursors.items():
            try:
                if store_id == local_id:
                    rpc_client.call("upload_cursor", {"sid": session_id, "seq": seq})
                elif host_id and store_id == host_id:
                    rpc_client.call_remote(host, "upload_cursor",
                                           {"sid": session_id, "seq": seq})
                # unknown store ids are ignored by design
            except Exception:
                degraded_steps.append("upload_cursor:" + str(store_id))
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
        degraded_steps.append("notify_peer")  # best-effort: never block the caller's exit
    # 2b. mark the connection closed (info.json status=closed; HP-05/D9) -
    # routed like unregister (fire-and-forget if the conv is remote)
    try:
        _deactivate_connection(session_id, toid, conv_remote)
    except Exception:
        degraded_steps.append("deactivate_connection")  # best-effort, like the notify
    data = {"closed": True}
    if degraded_steps:
        data["degraded_steps"] = degraded_steps
    return _ok(data)


# ---------- cursor-ACK listening (HP-02; preferred over legacy listen) ----------

def _store_ids():
    """(local_store_id, host_entry). host_entry is None when we ARE the host
    (then all our convs are local). The host's store id is its registry id."""
    local_id = machine_identity.load_or_create().get("id")
    return local_id, _host_entry()


def _degraded_stores(local_id, host, local_ok: bool, remote_ok: bool) -> list:
    """Store ids with zero successful scans during this call (AR-02). host is
    None when we ARE the host (then there is no remote store to report)."""
    degraded = []
    if not local_ok:
        degraded.append(local_id)
    if host is not None and not remote_ok:
        degraded.append(host.get("id"))
    return degraded


def listen_v2(session_id: str, cursors: dict = None, timeout: int = 30) -> dict:
    """BLOCKING listen with PER-STORE cursors (HP-02). `cursors` maps
    store_id -> confirmed sequence ({} or None the first time; recover with
    query_my_cursors after compact/restart). Each store is scanned with ONLY
    its own cursor - cursors are never merged or compared across stores.
    Returns {messages, next_cursors}; when a store never answered this call
    the result carries `degraded_stores` ([store_id, ...]). Cancel-safe: the
    kernel archives only what you confirmed via the cursors you passed.
    Persist the messages to YOUR store first, THEN advance cursors (transport
    receipt != task done). NEVER fall back to the timestamp `listen` once you
    use cursors. AR-02: a transport failure is NOT masked as empty success -
    if the LOCAL kernel never answered by the deadline, the call fails
    INTERNAL/retryable (an unscanned local store cannot be trusted); a host
    store that never answered degrades the result instead of losing it."""
    cursors = dict(cursors or {})
    local_id, host = _store_ids()
    deadline = time.time() + timeout
    local_ok = False   # at least one successful LOCAL scan this call (AR-02)
    remote_ok = False  # at least one successful HOST scan this call (AR-02)
    while time.time() < deadline:
        messages = []
        next_cursors = dict(cursors)
        try:
            r = rpc_client.call("listen_scan_v2",
                                {"sid": session_id, "cursor": cursors.get(local_id, 0)})
        except Exception:
            r = None  # transient kernel issue -> retry next poll (still tracked)
        if isinstance(r, dict):
            local_ok = True
            messages.extend(r.get("messages") or [])
            nc = r.get("next_cursor", 0)
            if nc > next_cursors.get(local_id, 0):
                next_cursors[local_id] = nc
        if host is not None:
            hid = host.get("id")
            rr = rpc_client.call_remote(host, "listen_scan_v2",
                                        {"sid": session_id, "cursor": cursors.get(hid, 0)})
            if isinstance(rr, dict):
                remote_ok = True
                messages.extend(rr.get("messages") or [])
                nc = rr.get("next_cursor", 0)
                if nc > next_cursors.get(hid, 0):
                    next_cursors[hid] = nc
        if messages:
            # Display-only sort (created_at_ms is NOT a correctness field);
            # per-store order is by sequence, cross-store order is undefined.
            messages.sort(key=lambda m: (m.get("created_at_ms", 0),
                                         m.get("store_id") or "",
                                         m.get("sequence", 0)))
            data = {"messages": messages, "next_cursors": next_cursors}
            degraded = _degraded_stores(local_id, host, local_ok, remote_ok)
            if degraded:
                data["degraded_stores"] = degraded
            return _ok(data)
        time.sleep(_LISTEN_POLL)
    if not local_ok:
        # Zero successful local scans: the caller cannot distinguish "worker
        # silent" from "transport broken" - that distinction is the point of
        # HP-07. A local kernel down is INTERNAL (it is on this machine).
        return _err(Code.INTERNAL,
                    "local kernel unreachable (listen_scan_v2 never succeeded)",
                    retryable=True)
    data = {"messages": [], "next_cursors": cursors}
    degraded = _degraded_stores(local_id, host, local_ok, remote_ok)
    if degraded:
        data["degraded_stores"] = degraded
    return _ok(data)


def query_my_cursors(session_id: str) -> dict:
    """Recover your per-store cursors, merged across this machine + the host
    (each kernel persists only its own store's cursors). Returns
    data = {cursors: {store_id: sequence}, degraded_stores: [store_id, ...]}
    (degraded_stores = [] when every store answered). RAR-01: degraded
    metadata lives OUTSIDE the cursor map, so `data.cursors` passes
    validate_cursors and composes directly into the next listen_v2 - the map
    itself is never polluted with non-cursor keys. AR-02: a local kernel
    failure is NOT returned as an empty success - it fails
    INTERNAL/retryable; a failed host query degrades to the local result."""
    local_id, host = _store_ids()
    out = {}
    local_ok = False
    remote_ok = False
    try:
        r = rpc_client.call("query_cursors", {"sid": session_id})
    except Exception:
        r = None
    if isinstance(r, dict):
        local_ok = True
        out.update(r)
    if host is not None:
        rr = rpc_client.call_remote(host, "query_cursors", {"sid": session_id})
        if isinstance(rr, dict):
            remote_ok = True
            out.update(rr)
    if not local_ok:
        return _err(Code.INTERNAL,
                    "local kernel unreachable (query_cursors failed)",
                    retryable=True)
    return _ok({"cursors": out,
                "degraded_stores": _degraded_stores(local_id, host,
                                                    local_ok, remote_ok)})


def query_my_ACK_timestamp(session_id: str) -> dict:
    """Recover the kernel's stored ACK watermark for session_id (T24). Call this
    after a compact / long gap / kernel restart if you've lost your latest ts,
    then use the returned value as `acked_ts` on your next listen."""
    try:
        r = rpc_client.call("query_ack_timestamp", {"sid": session_id})
    except Exception:
        r = 0
    return _ok(r if isinstance(r, int) else 0)


# ---------- spawn_collaborator (HP-04) ----------

def _spawn_prompt(token: str) -> str:
    return ("You are a new collaborator spawned by cc-communicate. "
            "First call my_session_id to learn your id. Then call "
            f"claim_pending_spawn('{token}', <your_id>) - one call; it is a "
            "no-op if your session was already claimed. Then call listen "
            "(your_id, acked_ts, timeout) - it blocks and returns "
            "{messages, watermark}. Pass 0 as acked_ts the FIRST time; on "
            "every later listen pass the watermark the previous listen "
            "returned (this lets the kernel archive only what you've "
            "confirmed - never drop or duplicate it). When a peer sends you "
            "a hello (kind=hello, carrying a correlation_id), reply with "
            "send_message(your_id, peer_id, <message>, correlation_id=<the "
            "hello's correlation_id>). KEEP LISTENING: after each listen "
            "returns, process any messages and call listen again (with the "
            "latest watermark), in a loop, until you call close_connection("
            "your_id, peer_id, your_latest_watermark) to end the "
            "conversation. If you ever lose your watermark (compact / long "
            "gap), call query_my_ACK_timestamp(your_id) to recover it. "
            "Never invoke listen.py directly, never write a shell loop, "
            "never nohup a listener - only use the listen tool.")


def _find_session_by_token(token: str, machine: dict = None):
    if machine is None:
        return rpc_client.call("find_session_by_token", {"token": token})
    return rpc_client.call_remote(machine, "find_session_by_token", {"token": token})


def _has_pending_spawn(token: str, machine: dict = None):
    if machine is None:
        return rpc_client.call("has_pending_spawn", {"token": token})
    return rpc_client.call_remote(machine, "has_pending_spawn", {"token": token})


def _spawn_new(cwd: str, prompt: str, spawn_token: str, machine: dict = None,
               permission_mode: str = "standard"):
    args = {"cwd": cwd, "prompt": prompt, "spawn_token": spawn_token,
            "permission_mode": permission_mode}
    if machine is None:
        return rpc_client.call("spawn_cc_new", args)
    return rpc_client.call_remote(machine, "spawn_cc_new", args)


def _worker_handle(session_id: str, spawn_token: str, cwd: str,
                   machine: dict = None, permission_mode: str = "standard") -> dict:
    machine_id = (machine or {}).get("id")
    if not machine_id:
        machine_id = machine_identity.load_or_create().get("id")
    return {"session_id": session_id, "machine_id": machine_id, "cwd": cwd,
            "spawn_token": spawn_token, "connection_status": "registered",
            "permission_mode": permission_mode}


def spawn_collaborator(caller_sid: str, cwd: str, spawn_token: str = None,
                       machine: dict = None, hold_time: int = 300,
                       permission_mode: str = "standard") -> dict:
    """Spawn a NEW CC in cwd (on `machine` if given, else local), wait for it
    to register, and return a structured WorkerHandle - NO auto-connect (the
    caller decides when to call connect). spawn_token: caller-supplied (or
    server-generated, returned in the handle); a retry with the SAME token
    returns the original handle instead of spawning again. HP-04.
    permission_mode (HP-10/D4): "standard" default - the spawned CC makes
    normal permission decisions; pass "bypass" for unattended automation
    (skips the trust dialog)."""
    token = spawn_token or uuid.uuid4().hex
    # same-token retry: already registered -> original handle
    try:
        sid = _find_session_by_token(token, machine)
    except KernelError as e:
        return _kernel_err(e)
    if sid:
        return _ok(_worker_handle(sid, token, cwd, machine,
                                  permission_mode=permission_mode))
    # in-flight (pending marker) -> don't re-spawn
    try:
        pending = _has_pending_spawn(token, machine)
    except KernelError as e:
        return _kernel_err(e)
    if pending is None:
        return _remote_err()
    if not pending:
        try:
            r = _spawn_new(cwd, _spawn_prompt(token), token, machine,
                           permission_mode)
        except KernelError as e:
            return _kernel_err(e)
        if r is None:
            return _remote_err()
    # poll for registration (token -> sid; plan A hook event or plan B claim)
    deadline = time.time() + 30
    sid = None
    while time.time() < deadline:
        time.sleep(1)
        try:
            sid = _find_session_by_token(token, machine)
        except KernelError:
            sid = None
        if sid:
            break
    if not sid:
        return _err(Code.TIMEOUT,
                    "new session did not register within 30s (is the plugin "
                    "installed for new CCs?)", retryable=True)
    return _ok(_worker_handle(sid, token, cwd, machine,
                              permission_mode=permission_mode))


def claim_pending_spawn(spawn_token: str, session_id: str) -> dict:
    """Plan B (D8): a spawned worker claims its token on first tool use so
    spawn_collaborator's registration poll can resolve. Idempotent."""
    try:
        r = rpc_client.call("claim_pending_spawn",
                            {"token": spawn_token, "session_id": session_id})
    except KernelError as e:
        return _kernel_err(e)
    if r and r.get("claimed"):
        return _ok({"claimed": True, "session_id": r.get("session_id")})
    return _err(Code.NOT_FOUND, (r or {}).get("reason", "no pending spawn for token"))


# The legacy spawn prompt below is kept EXACTLY as today (per the brief), so
# workers spawned via the legacy path keep replying WITHOUT correlation_id and
# keep exercising connect's D9 legacy fallback. spawn_collaborator (the new
# path) uses _spawn_prompt above, whose correlation_id instruction makes the
# worker reply selectable by HP-05.
_LEGACY_SPAWN_PROMPT = ("You are a new collaborator spawned by cc-communicate. "
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


def create_collaborator(caller_sid: str, cwd: str, hold_time: int = 300,
                        machine=None) -> str:
    """LEGACY wrapper (one release, HP-07): spawn + connect, returns the
    legacy string shape. New code should use spawn_collaborator (structured
    WorkerHandle) + connect. The spawn prompt stays the OLD text so its
    correlation_id-less replies exercise connect's legacy fallback (D9).
    HP-10 (D4): keeps permission_mode="bypass" EXPLICITLY (pre-dates the
    standard default) and marks it in the returned string + kernel log."""
    hold_time = max(hold_time, _MIN_HOLD_TIME)
    res = spawn_collaborator(caller_sid, cwd, spawn_token=None,
                             machine=machine, hold_time=hold_time,
                             permission_mode="bypass")
    if not res["ok"]:
        return "failed, " + str(res.get("message")) + _LEGACY_BYPASS_SUFFIX
    handle = res["data"]
    cr = connect(caller_sid, handle["session_id"], hold_time=hold_time)
    if cr["ok"]:
        reply = (cr["data"] or {}).get("reply")
        base = ("connect succeed; reply: " + reply) if reply else "connect succeed"
        return base + _LEGACY_BYPASS_SUFFIX
    return "connect failed, " + str(cr.get("message")) + _LEGACY_BYPASS_SUFFIX


def query_machines() -> dict:
    """Registered peer machines: {id: entry, ...}."""
    return _ok({m.get("id"): m for m in read_machine_info_log()})


def help_connect_machines() -> dict:
    """Return the cross-machine handshake playbook (C4). The CC calls this when
    the user wants to link this machine to a peer (e.g. 'help me connect
    machines', 'connect WSL to host', 'register the other machine'), then follows
    the steps - asking clarifications and driving both sides' handshake scripts
    itself (cross-realm exec, like _wake_remote)."""
    guide_path = os.path.join(PLUGIN_ROOT, "server", "handshake_guide.md")
    try:
        with open(guide_path, encoding="utf-8") as f:
            return _ok(f.read())
    except OSError as e:
        return _err(Code.NOT_FOUND, f"handshake guide not found at {guide_path}: {e}")
