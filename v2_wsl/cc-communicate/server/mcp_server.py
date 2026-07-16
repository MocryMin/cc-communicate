"""cc-communicate MCP server - thin shell exposing user functions as MCP tools.

CC starts this process per session (see .mcp.json). The server holds NO state;
each tool either forwards to the shared kernel via rpc_client.call() (local) or
calls user_functions for orchestration / cross-realm routing.

Tools (v2):
  Identity:      my_session_id
  Read-only:     query_session, check_alive, query_conversations   (routed)
  Messaging:     send_message (routed), register_conversation, unregister_conversation, withdraw (local low-level)
  Spawning:      evoke (routed)
  Listening:     listen (blocking - call in a loop until close_connection; C2)
  Orchestration: connect, close_connection (best-effort non-blocking; C1), create_collaborator
  Machines:      query_machines, help_connect_machines (handshake guide; C4)
"""
from mcp.server.fastmcp import FastMCP

import rpc_client
import user_functions

mcp = FastMCP("cc-communicate")


@mcp.tool()
def my_session_id() -> str:
    """This CC's own session_id (walks the process tree to the claude binary
    ancestor). Call this first. Returns the sid, or 'failed, ...'."""
    return user_functions.my_session_id()


@mcp.tool()
def query_session(session_id: str) -> dict:
    """Look up a session by id (local kernel first, then registered peer
    machines). Returns session_inf or null if unknown everywhere."""
    return user_functions.query_session(session_id)


@mcp.tool()
def check_alive(session_id: str) -> int:
    """1 if the session is truly alive (pid + start_time verified) on this
    machine or any registered peer; 0 otherwise."""
    return user_functions.check_alive(session_id)


@mcp.tool()
def query_conversations(session_id: str) -> dict:
    """Conversation partners for session_id, merged across this machine + peers:
    {partner_sid: {...info}, ...}. Includes ended-but-not-withdrawn."""
    return user_functions.query_conversations(session_id)


@mcp.tool()
def send_message(fromid: str, toid: str, message: str) -> str:
    """Send a message to a peer's pipe. Routes to the conversation store (host
    for cross-machine, else local). The conversation must be registered
    (normally via connect) first, else returns a failure string."""
    return user_functions.send_message(fromid, toid, message)


@mcp.tool()
def register_conversation(sid_a: str, sid_b: str) -> str:
    """Mark a LOCAL conversation active (low-level; connect handles routing).
    Exposed for bootstrapping/testing."""
    return rpc_client.call("register_conversation", {"sid_a": sid_a, "sid_b": sid_b})


@mcp.tool()
def unregister_conversation(sid_a: str, sid_b: str) -> str:
    """Mark a LOCAL conversation inactive (low-level)."""
    return rpc_client.call("unregister_conversation", {"sid_a": sid_a, "sid_b": sid_b})


@mcp.tool()
def withdraw(fromid: str, toid: str, init_connect: int = 0) -> str:
    """Withdraw a message or whole LOCAL conversation (low-level).
    init_connect=1: remove the whole folder + unregister; =0: remove fromid's
    latest undelivered pipe message."""
    return rpc_client.call("withdraw", {"fromid": fromid, "toid": toid, "init_connect": init_connect})


@mcp.tool()
def evoke(session_id: str) -> str:
    """Revive a dead CC session on whatever machine it lives on (local or remote
    peer). Returns 'evoke spawned (resumed)' or 'failed, session not exists'."""
    return user_functions.evoke(session_id)


@mcp.tool()
def listen(session_id: str, timeout: int = 30) -> list:
    """BLOCKING: wait up to `timeout` seconds for undelivered messages addressed
    to session_id, then return them as a list (possibly empty on timeout). CALL
    THIS IN A LOOP: after it returns, process any messages, then call listen
    again - keep a listener active at all times while a connection is
    established. You may stop the loop ONLY after calling close_connection.
    Never invoke listen.py directly or write your own shell listener - always
    use this tool."""
    return user_functions.listen(session_id, timeout)


@mcp.tool()
def connect(caller_sid: str, target_sid: str, hold_time: int = 300) -> str:
    """Establish a p2p connection to target_sid (local or cross-realm). If the
    target is dead, revives it and waits for it to come alive, sends a hello,
    then blocks up to hold_time seconds waiting for the reply. Returns
    'connect succeed; reply: ...' on success, or a 'failed, ...' /
    'connect failed, ...' string on failure. Connect BEFORE calling listen
    (running a listener during connect can duplicate the reply). Once connect
    succeeds the channel is ESTABLISHED: you MUST then call listen in a loop
    (see the listen tool) and keep it active until you call close_connection."""
    return user_functions.connect(caller_sid, target_sid, hold_time)


@mcp.tool()
def close_connection(session_id: str, toid: str) -> dict:
    """Terminate the connection to toid (the ONLY way to stop your listen loop).
    Best-effort and non-blocking: sends a '[CONNECTION CLOSED by <sid>]' notice
    and unregisters, then returns {closed: True, ...} immediately - it does not
    wait for the peer to acknowledge. The peer's listener sees the notice and
    frees itself. Safe to call even if the peer is unreachable. After this
    returns you may stop listening and exit."""
    return user_functions.close_connection(session_id, toid)


@mcp.tool()
def create_collaborator(caller_sid: str, cwd: str, hold_time: int = 300,
                        machine: dict = None) -> str:
    """Spawn a NEW CC in cwd (on `machine` if given - a query_machines entry -
    else this machine) and connect to it. The new CC loads the plugin and
    listens; this tool waits for it to register, then connects. Returns
    connect's result, or 'failed' if it doesn't register within 30s."""
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
