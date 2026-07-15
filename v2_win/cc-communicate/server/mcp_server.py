"""cc-communicate MCP server - thin shell exposing user functions as MCP tools.

CC starts this process per session (see .mcp.json). The server holds NO state;
each tool either forwards to the shared kernel via rpc_client.call() (local) or
calls user_functions for orchestration / cross-realm routing.

Tools (v2):
  Identity:      my_session_id
  Read-only:     query_session, check_alive, query_conversations   (routed)
  Messaging:     send_message (routed), register_conversation, unregister_conversation, withdraw (local low-level)
  Spawning:      evoke (routed)
  Listening:     listen -> returns the listen.py command (Amd3)
  Orchestration: connect, close_connection, create_collaborator
  Machines:      query_machines
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
def listen(session_id: str, timeout: int = 300) -> dict:
    """Arm a background listener for messages addressed to session_id. Returns
    {command, timeout}. Run `command` via Bash(run_in_background=true); the
    listener prints collected messages as JSON on stdout and exits 0 when one
    arrives (you get a <task-notification>), or exits 2 on timeout. This replaces
    the old arm_poller + collect_messages two-step (Amd3)."""
    return user_functions.listen_command(session_id, timeout)


@mcp.tool()
def connect(caller_sid: str, target_sid: str, hold_time: int = 300) -> str:
    """Establish a p2p connection to target_sid (local or cross-realm). If the
    target is dead, revives it and waits for it to come alive, sends a hello,
    then blocks up to hold_time seconds waiting for the reply. Returns
    'connect succeed; reply: ...' on success, or a 'failed, ...' /
    'connect failed, ...' string on failure. Connect BEFORE calling listen
    (running a listener during connect can duplicate the reply)."""
    return user_functions.connect(caller_sid, target_sid, hold_time)


@mcp.tool()
def close_connection(session_id: str, toid: str) -> dict:
    """Close the connection to toid. Drains pending messages addressed to
    session_id (returns them as delivered_pending), notifies the peer with
    '[CONNECTION CLOSED by <sid>]', and unregisters. Returns
    {closed: True, delivered_pending: [...]}."""
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


if __name__ == "__main__":
    mcp.run(transport="stdio")
