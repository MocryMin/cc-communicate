"""cc-communicate MCP server - thin shell exposing user functions as MCP tools.

CC starts this process per session (see .mcp.json). The server holds NO state;
each tool either forwards to the shared kernel via rpc_client.call() (local) or
calls user_functions for orchestration / cross-realm routing.

Tools (v2):
  Identity:      my_session_id
  Read-only:     query_session, check_alive, query_conversations   (routed)
  Messaging:     send_message (routed), register_conversation, unregister_conversation, withdraw (local low-level)
  Spawning:      evoke (routed)
  Listening:     listen (blocking, timestamp-ACK watermark; poll kernel atomic scan; T24),
                 query_my_ACK_timestamp (recover the watermark after compact/restart; T24)
  Orchestration: connect, close_connection (best-effort non-blocking; uploads watermark; T24), create_collaborator
  Machines:      query_machines, help_connect_machines (handshake guide; C4)
"""
from mcp.server.fastmcp import FastMCP

import user_functions
import validation
from result import Code

mcp = FastMCP("cc-communicate")


def _entry_error(*checks):
    """Run MCP-entry validators (HP-06). `checks` are (validator, value) pairs.
    Returns the INVALID_ARGUMENT envelope, or None when all pass. Kernel
    dispatch validates again - defense in depth, and remote RPC never passes
    through here."""
    try:
        for validator, value in checks:
            validator(value)
    except validation.InvalidArgumentError as e:
        return {"ok": False, "code": Code.INVALID_ARGUMENT,
                "message": str(e), "data": None, "retryable": False}
    return None


@mcp.tool()
def my_session_id() -> dict:
    """This CC's own session_id (walks the process tree to the claude binary
    ancestor). Call this first. Returns the envelope: ok(sid) or err(...)."""
    return user_functions.my_session_id()


@mcp.tool()
def query_session(session_id: str) -> dict:
    """Look up a session by id (local kernel first, then registered peer
    machines). Returns the envelope: ok(session_inf) or ok(null) if unknown
    everywhere; err(INVALID_ARGUMENT) on a bad id."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.query_session(session_id)


@mcp.tool()
def check_alive(session_id: str) -> dict:
    """1 if the session is truly alive (pid + start_time verified) on this
    machine or any registered peer; 0 otherwise. Returns the envelope;
    err(INVALID_ARGUMENT) on a bad id."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.check_alive(session_id)


@mcp.tool()
def query_conversations(session_id: str) -> dict:
    """Conversation partners for session_id, merged across this machine + peers:
    {partner_sid: {...info}, ...}. Includes ended-but-not-withdrawn. Returns the
    envelope; err(INVALID_ARGUMENT) on a bad id."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.query_conversations(session_id)


@mcp.tool()
def send_message(fromid: str, toid: str, message: str,
                 correlation_id: str = None, kind: str = None) -> dict:
    """Send a message to a peer's pipe. Routes to the conversation store (host
    for cross-machine, else local). The conversation must be registered
    (normally via connect) first. Returns the envelope: ok({message_id, ts});
    err(NOT_FOUND) when not registered; err(INVALID_ARGUMENT) on a bad id."""
    checks = [(validation.validate_session_id, fromid),
              (validation.validate_session_id, toid),
              (validation.validate_message_size, message)]
    if correlation_id is not None:
        checks.append((validation.validate_message_id, correlation_id))
    if kind is not None:
        checks.append((validation.validate_message_id, kind))
    err = _entry_error(*checks)
    if err:
        return err
    return user_functions.send_message(fromid, toid, message,
                                       correlation_id=correlation_id, kind=kind)


@mcp.tool()
def register_conversation(sid_a: str, sid_b: str) -> dict:
    """Mark a LOCAL conversation active (low-level; connect handles routing).
    Exposed for bootstrapping/testing. Returns the envelope."""
    err = _entry_error((validation.validate_session_id, sid_a),
                       (validation.validate_session_id, sid_b))
    if err:
        return err
    return user_functions.register_conversation(sid_a, sid_b)


@mcp.tool()
def unregister_conversation(sid_a: str, sid_b: str) -> dict:
    """Mark a LOCAL conversation inactive (low-level). Returns the envelope."""
    err = _entry_error((validation.validate_session_id, sid_a),
                       (validation.validate_session_id, sid_b))
    if err:
        return err
    return user_functions.unregister_conversation(sid_a, sid_b)


@mcp.tool()
def withdraw(fromid: str, toid: str, init_connect: int = 0,
             message_id: str = None) -> dict:
    """Withdraw a message or whole LOCAL conversation (low-level).
    init_connect=1: remove the whole folder + unregister; =0: default legacy
    mode withdraws fromid's latest undelivered message (non-idempotent).
    message_id: withdraw that EXACT message (retry-safe; preferred).
    Returns the envelope: ok(result) or err(NOT_FOUND)."""
    checks = [(validation.validate_session_id, fromid),
              (validation.validate_session_id, toid)]
    if message_id is not None:
        checks.append((validation.validate_message_id, message_id))
    err = _entry_error(*checks)
    if err:
        return err
    return user_functions.withdraw(fromid, toid, init_connect, message_id)


@mcp.tool()
def evoke(session_id: str) -> dict:
    """Revive a dead CC session on whatever machine it lives on (local or remote
    peer). Returns the envelope: ok({evoked: True, session_id}) or
    err(NOT_FOUND) when the session does not exist."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.evoke(session_id)


@mcp.tool()
def listen(session_id: str, acked_ts: int = 0, timeout: int = 30) -> dict:
    """LEGACY (deprecation window): prefer listen_v2.
    BLOCKING: wait up to `timeout` seconds for undelivered messages addressed
    to session_id, then return the envelope: ok({messages, watermark})
    (messages possibly empty on timeout). Pass 0 as acked_ts the FIRST time; on
    every later call pass the `watermark` the previous listen returned. CALL
    THIS IN A LOOP until close_connection. Never invoke listen.py directly or
    write your own shell listener."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.listen(session_id, acked_ts, timeout)


@mcp.tool()
def listen_v2(session_id: str, cursors: dict = None, timeout: int = 30) -> dict:
    """BLOCKING listen with PER-STORE cursors (PREFERRED over legacy listen).
    Pass {} (or query_my_cursors) the first time; on every later call pass the
    `next_cursors` the previous listen_v2 returned - unchanged. Returns the
    envelope: ok({messages, next_cursors}). Each message is a record:
    {message_id, store_id, sequence, from_session, to_session, kind,
    correlation_id, created_at_ms, payload:{text}}. Dedup on message_id if you
    see repeats (at-least-once). IMPORTANT: persist the messages to your own
    store BEFORE you pass the advanced cursors back - a cursor means "durably
    received", not "task done". NEVER mix cursor values between stores, and
    NEVER fall back to the timestamp `listen` once you use cursors (silent
    cross-store mis-archiving would return)."""
    err = _entry_error((validation.validate_session_id, session_id),
                       (validation.validate_cursors, cursors))
    if err:
        return err
    return user_functions.listen_v2(session_id, cursors, timeout)


@mcp.tool()
def connect(caller_sid: str, target_sid: str, connection_id: str = None,
            hold_time: int = 300) -> dict:
    """Establish a p2p connection to target_sid (local or cross-realm). If the
    target is dead, revives it and waits for it to come alive, sends a hello
    (kind=hello, correlation_id=connection_id), then blocks up to hold_time
    seconds waiting for the correlation-matched reply. connection_id: caller-
    supplied to make retries idempotent; omitted -> server generates one
    (returned in the envelope data). One active connection per pair (D9): a
    retry with the same id returns the current state; a different id while one
    is active returns CONFLICT. Connect BEFORE calling listen (running a
    listener during connect can duplicate the reply). Once connect succeeds the
    channel is ESTABLISHED: you MUST then call listen in a loop (passing the
    watermark each call - see the listen tool) and keep it active until you
    call close_connection."""
    err = _entry_error((validation.validate_session_id, caller_sid),
                       (validation.validate_session_id, target_sid))
    if err:
        return err
    if connection_id is not None:
        err2 = _entry_error((validation.validate_connection_id, connection_id))
        if err2:
            return err2
    return user_functions.connect(caller_sid, target_sid, connection_id, hold_time)


@mcp.tool()
def close_connection(session_id: str, toid: str, acked_ts: int = 0,
                     cursors: dict = None) -> dict:
    """Terminate the connection to toid (the ONLY way to stop your listen loop).
    Pass your latest `watermark` as acked_ts and/or your per-store cursors
    (from listen_v2 / query_my_cursors). Each cursor is uploaded only to the
    kernel that owns that store. Best-effort and non-blocking: returns
    `{closed: True}` immediately. Does NOT clean up the pipe (ts-based/cursor
    ACK: un-acked messages stay). Safe to call even if the peer is
    unreachable. After this returns you may stop listening and exit."""
    err = _entry_error((validation.validate_session_id, session_id),
                       (validation.validate_session_id, toid),
                       (validation.validate_cursors, cursors))
    if err:
        return err
    return user_functions.close_connection(session_id, toid, acked_ts, cursors)


@mcp.tool()
def query_my_ACK_timestamp(session_id: str) -> dict:
    """LEGACY: prefer query_my_cursors. Recover your latest ACK watermark from
    the kernel (the one you last passed to listen or close_connection). Call
    this after a compact / long gap / kernel restart if you've lost the
    watermark, then use the returned value as `acked_ts` on your next listen.
    Returns the envelope: ok(ts) (0 if none is recorded)."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.query_my_ACK_timestamp(session_id)


@mcp.tool()
def query_my_cursors(session_id: str) -> dict:
    """Recover your per-store cursors ({store_id: sequence}) from the kernels
    (local + host merged). Call after a compact / long gap / kernel restart,
    then pass the result as `cursors` on your next listen_v2. Returns the
    envelope: ok(cursors)."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.query_my_cursors(session_id)


@mcp.tool()
def create_collaborator(caller_sid: str, cwd: str, hold_time: int = 300,
                        machine: dict = None) -> str:
    """Spawn a NEW CC in cwd (on `machine` if given - a query_machines entry -
    else this machine) and connect to it. The new CC loads the plugin and
    listens; this tool waits for it to register, then connects. Returns
    connect's result, or 'failed' if it doesn't register within 30s."""
    err = validation.validate_spawn_entry(caller_sid, cwd, machine)
    if err:
        return err
    return user_functions.create_collaborator(caller_sid, cwd, hold_time, machine)


@mcp.tool()
def spawn_collaborator(caller_sid: str, cwd: str, spawn_token: str = None,
                       permission_mode: str = "bypass", machine: dict = None,
                       hold_time: int = 300) -> dict:
    """Spawn a NEW CC in cwd (on `machine` if given - a query_machines entry -
    else this machine) and wait for it to register. Returns the envelope with
    a structured WorkerHandle in data: {session_id, machine_id, cwd,
    spawn_token, connection_status}. Does NOT auto-connect - call connect when
    you want the channel. spawn_token: caller-supplied to make retries
    idempotent (same token -> same handle, no second spawn); omitted -> server
    generates one (returned in the handle). permission_mode: accepted now
    (default 'bypass' = current behavior); Wave 3 HP-10 flips the default to
    'standard' per D4 - the parameter surface never changes."""
    err = validation.validate_spawn_entry(caller_sid, cwd, machine)
    if err:
        return {"ok": False, "code": Code.INVALID_ARGUMENT,
                "message": err, "data": None, "retryable": False}
    if spawn_token is not None:
        err2 = _entry_error((validation.validate_spawn_token, spawn_token))
        if err2:
            return err2
    return user_functions.spawn_collaborator(caller_sid, cwd, spawn_token,
                                             machine, hold_time)


@mcp.tool()
def claim_pending_spawn(spawn_token: str, session_id: str) -> dict:
    """Claim a pending spawn token (plan B, HP-04): a spawned worker calls
    this on its FIRST tool use so the spawner's registration poll can resolve.
    Idempotent - safe to call more than once. Returns ok({claimed, session_id})
    or err(NOT_FOUND) when no pending spawn matches the token."""
    err = _entry_error((validation.validate_spawn_token, spawn_token),
                       (validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.claim_pending_spawn(spawn_token, session_id)


@mcp.tool()
def query_machines() -> dict:
    """Registered peer machines: {id: {type, data_dir, ...}, ...}. Returns the
    envelope: ok(machines)."""
    return user_functions.query_machines()


@mcp.tool()
def help_connect_machines() -> dict:
    """Step-by-step guide for connecting this machine to a peer (Windows host <->
    WSL one-time handshake). Call this when the user wants to link machines -
    e.g. 'help me connect machines', 'connect WSL to host', 'register the other
    machine'. Returns the envelope: ok(playbook); follow it, asking the user
    clarifications (is the plugin installed on the other machine? its install
    path?) and driving both sides' handshake scripts yourself via cross-realm
    exec."""
    return user_functions.help_connect_machines()


if __name__ == "__main__":
    mcp.run(transport="stdio")
