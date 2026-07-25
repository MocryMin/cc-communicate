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

import rpc_client
import user_functions
import validation

mcp = FastMCP("cc-communicate")


def _entry_error(*checks):
    """Run MCP-entry validators (HP-06). `checks` are (validator, value) pairs.
    Returns the INVALID_ARGUMENT error string, or None when all pass. Kernel
    dispatch validates again - defense in depth, and remote RPC never passes
    through here."""
    try:
        for validator, value in checks:
            validator(value)
    except validation.InvalidArgumentError as e:
        return str(e)
    return None


@mcp.tool()
def my_session_id() -> str:
    """This CC's own session_id (walks the process tree to the claude binary
    ancestor). Call this first. Returns the sid, or 'failed, ...'."""
    return user_functions.my_session_id()


@mcp.tool()
def query_session(session_id: str) -> dict:
    """Look up a session by id (local kernel first, then registered peer
    machines). Returns session_inf or null if unknown everywhere."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.query_session(session_id)


@mcp.tool()
def check_alive(session_id: str) -> int:
    """1 if the session is truly alive (pid + start_time verified) on this
    machine or any registered peer; 0 otherwise."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.check_alive(session_id)


@mcp.tool()
def query_conversations(session_id: str) -> dict:
    """Conversation partners for session_id, merged across this machine + peers:
    {partner_sid: {...info}, ...}. Includes ended-but-not-withdrawn."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.query_conversations(session_id)


@mcp.tool()
def send_message(fromid: str, toid: str, message: str) -> str:
    """Send a message to a peer's pipe. Routes to the conversation store (host
    for cross-machine, else local). The conversation must be registered
    (normally via connect) first, else returns a failure string."""
    err = _entry_error((validation.validate_session_id, fromid),
                       (validation.validate_session_id, toid),
                       (validation.validate_message_size, message))
    if err:
        return err
    return user_functions.send_message(fromid, toid, message)


@mcp.tool()
def register_conversation(sid_a: str, sid_b: str) -> str:
    """Mark a LOCAL conversation active (low-level; connect handles routing).
    Exposed for bootstrapping/testing."""
    err = _entry_error((validation.validate_session_id, sid_a),
                       (validation.validate_session_id, sid_b))
    if err:
        return err
    return rpc_client.call("register_conversation", {"sid_a": sid_a, "sid_b": sid_b})


@mcp.tool()
def unregister_conversation(sid_a: str, sid_b: str) -> str:
    """Mark a LOCAL conversation inactive (low-level)."""
    err = _entry_error((validation.validate_session_id, sid_a),
                       (validation.validate_session_id, sid_b))
    if err:
        return err
    return rpc_client.call("unregister_conversation", {"sid_a": sid_a, "sid_b": sid_b})


@mcp.tool()
def withdraw(fromid: str, toid: str, init_connect: int = 0,
             message_id: str = None) -> str:
    """Withdraw a message or whole LOCAL conversation (low-level).
    init_connect=1: remove the whole folder + unregister; =0: default legacy
    mode withdraws fromid's latest undelivered message (non-idempotent).
    message_id: withdraw that EXACT message (retry-safe; preferred)."""
    err = _entry_error((validation.validate_session_id, fromid),
                       (validation.validate_session_id, toid))
    if err:
        return err
    if message_id is not None:
        err2 = _entry_error((validation.validate_message_id, message_id))
        if err2:
            return err2
    return rpc_client.call("withdraw", {"fromid": fromid, "toid": toid, "init_connect": init_connect, "message_id": message_id})


@mcp.tool()
def evoke(session_id: str) -> str:
    """Revive a dead CC session on whatever machine it lives on (local or remote
    peer). Returns 'evoke spawned (resumed)' or 'failed, session not exists'."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.evoke(session_id)


@mcp.tool()
def listen(session_id: str, acked_ts: int = 0, timeout: int = 30) -> dict:
    """LEGACY (deprecation window): prefer listen_v2.
    BLOCKING: wait up to `timeout` seconds for undelivered messages addressed
    to session_id, then return `{messages, watermark}` (messages possibly empty
    on timeout). Pass 0 as acked_ts the FIRST time; on every later call pass the
    `watermark` the previous listen returned. CALL THIS IN A LOOP until
    close_connection. Never invoke listen.py directly or write your own shell
    listener."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.listen(session_id, acked_ts, timeout)


@mcp.tool()
def listen_v2(session_id: str, cursors: dict = None, timeout: int = 30) -> dict:
    """BLOCKING listen with PER-STORE cursors (PREFERRED over legacy listen).
    Pass {} (or query_my_cursors) the first time; on every later call pass the
    `next_cursors` the previous listen_v2 returned - unchanged. Returns
    {messages, next_cursors}. Each message is a record: {message_id, store_id,
    sequence, from_session, to_session, kind, correlation_id, created_at_ms,
    payload:{text}}. Dedup on message_id if you see repeats (at-least-once).
    IMPORTANT: persist the messages to your own store BEFORE you pass the
    advanced cursors back - a cursor means "durably received", not "task
    done". NEVER mix cursor values between stores, and NEVER fall back to the
    timestamp `listen` once you use cursors (silent cross-store mis-archiving
    would return)."""
    err = _entry_error((validation.validate_session_id, session_id),
                       (validation.validate_cursors, cursors))
    if err:
        return {"messages": [], "next_cursors": {}, "error": err}
    return user_functions.listen_v2(session_id, cursors, timeout)


@mcp.tool()
def connect(caller_sid: str, target_sid: str, hold_time: int = 300) -> str:
    """Establish a p2p connection to target_sid (local or cross-realm). If the
    target is dead, revives it and waits for it to come alive, sends a hello,
    then blocks up to hold_time seconds waiting for the reply. Returns
    'connect succeed; reply: ...' on success, or a 'failed, ...' /
    'connect failed, ...' string on failure. Connect BEFORE calling listen
    (running a listener during connect can duplicate the reply). Once connect
    succeeds the channel is ESTABLISHED: you MUST then call listen in a loop
    (passing the watermark each call - see the listen tool) and keep it active
    until you call close_connection."""
    err = _entry_error((validation.validate_session_id, caller_sid),
                       (validation.validate_session_id, target_sid))
    if err:
        return err
    return user_functions.connect(caller_sid, target_sid, hold_time)


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
def query_my_ACK_timestamp(session_id: str) -> int:
    """LEGACY: prefer query_my_cursors. Recover your latest ACK watermark from
    the kernel (the one you last passed to listen or close_connection). Call
    this after a compact / long gap / kernel restart if you've lost the
    watermark, then use the returned value as `acked_ts` on your next listen.
    Returns 0 if none is recorded."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.query_my_ACK_timestamp(session_id)


@mcp.tool()
def query_my_cursors(session_id: str) -> dict:
    """Recover your per-store cursors ({store_id: sequence}) from the kernels
    (local + host merged). Call after a compact / long gap / kernel restart,
    then pass the result as `cursors` on your next listen_v2."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return {"error": err}
    return user_functions.query_my_cursors(session_id)


@mcp.tool()
def create_collaborator(caller_sid: str, cwd: str, hold_time: int = 300,
                        machine: dict = None) -> str:
    """Spawn a NEW CC in cwd (on `machine` if given - a query_machines entry -
    else this machine) and connect to it. The new CC loads the plugin and
    listens; this tool waits for it to register, then connects. Returns
    connect's result, or 'failed' if it doesn't register within 30s."""
    err = _entry_error((validation.validate_session_id, caller_sid),
                       (validation.validate_cwd, cwd))
    if err:
        return err
    return user_functions.create_collaborator(caller_sid, cwd, hold_time, machine)


@mcp.tool()
def query_machines() -> dict:
    """Registered peer machines: {id: {type, data_dir, ...}, ...}."""
    return user_functions.query_machines()


@mcp.tool()
def help_connect_machines() -> str:
    """Step-by-step guide for connecting this machine to a peer (Windows host <->
    WSL one-time handshake). Call this when the user wants to link machines -
    e.g. 'help me connect machines', 'connect WSL to host', 'register the other
    machine'. Returns a playbook; follow it, asking the user clarifications
    (is the plugin installed on the other machine? its install path?) and driving
    both sides' handshake scripts yourself via cross-realm exec."""
    return user_functions.help_connect_machines()


if __name__ == "__main__":
    mcp.run(transport="stdio")
