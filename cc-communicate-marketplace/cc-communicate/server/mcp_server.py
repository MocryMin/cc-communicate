"""cc-communicate MCP server — thin shell exposing user functions as MCP tools.

CC starts this process per session (see .mcp.json). The server holds NO state;
each tool call either forwards to the shared kernel via rpc_client.call()
(which ensure_core()s the kernel, writes a queue request, polls for the
response), or calls user_functions for orchestration (connect, etc.).

Tools exposed:
  Identity:      my_session_id
  Read-only:     query_session, check_alive, query_conversations
  Messaging:     send_message, withdraw, register_conversation, unregister_conversation
  Spawning:      evoke
  Listening:     arm_poller, collect_messages
  Orchestration: connect, close_connection, create_collaborator
"""
from mcp.server.fastmcp import FastMCP

import rpc_client
import user_functions

mcp = FastMCP("cc-communicate")


@mcp.tool()
def query_session(session_id: str) -> dict:
    """Look up a Claude Code session by id. Returns session_inf
    {pid, cwd, start_time, started_at, ended_at, ...} or null if unknown."""
    return rpc_client.call("query_session", {"session_id": session_id})


@mcp.tool()
def check_alive(session_id: str) -> int:
    """Is the session truly alive? Returns 1 (alive) or 0 (not alive / unknown).
    Verifies pid + start_time against the OS (defends against PID reuse)."""
    return rpc_client.call("check_alive", {"session_id": session_id})


@mcp.tool()
def query_conversations(session_id: str) -> list:
    """List this session's conversation partners (from the conversations
    folder — includes ended-but-not-withdrawn). Returns [{partner: sid}, ...]."""
    return rpc_client.call("query_conversations", {"session_id": session_id})


@mcp.tool()
def send_message(fromid: str, toid: str, message: str) -> str:
    """Send a message to a peer session's pipe. The conversation must be
    registered (normally via connect) first, else returns a failure string."""
    return rpc_client.call("send_message", {"fromid": fromid, "toid": toid, "message": message})


@mcp.tool()
def register_conversation(sid_a: str, sid_b: str) -> str:
    """Mark a conversation as active. Normally called by connect; exposed
    separately for bootstrapping and testing."""
    return rpc_client.call("register_conversation", {"sid_a": sid_a, "sid_b": sid_b})


@mcp.tool()
def unregister_conversation(sid_a: str, sid_b: str) -> str:
    """Mark a conversation inactive (peer closed, etc.)."""
    return rpc_client.call("unregister_conversation", {"sid_a": sid_a, "sid_b": sid_b})


@mcp.tool()
def withdraw(fromid: str, toid: str, init_connect: int = 0) -> str:
    """Withdraw a message or whole conversation.
    init_connect=1: remove the whole conversation folder + unregister.
    init_connect=0: remove fromid's latest undelivered pipe message."""
    return rpc_client.call("withdraw", {"fromid": fromid, "toid": toid, "init_connect": init_connect})


@mcp.tool()
def evoke(session_id: str) -> str:
    """Revive a dead CC session via `claude --resume <session_id>` (Windows).
    The SAME session_id is resumed (not a fresh one), so connect can talk to
    target_sid directly afterward. The revived CC fires SessionStart -> the
    kernel updates alive_sessions with the new pid; poll check_alive until
    alive. Returns 'evoke spawned (resumed)' or 'failed, session unknown'."""
    return rpc_client.call("evoke", {"session_id": session_id})


@mcp.tool()
def arm_poller(session_id: str, timeout: int = 1800) -> dict:
    """Arm a background poller that watches for new messages addressed to
    session_id. Returns {armed, command, timeout, baseline}. Run `command`
    via Bash with run_in_background=true; the poller exits 0 when a message
    arrives (CC gets a <task-notification> and wakes), or 2 on timeout."""
    return rpc_client.call("arm_poller", {"session_id": session_id, "timeout": timeout})


@mcp.tool()
def collect_messages(session_id: str) -> list:
    """Collect all undelivered messages addressed to session_id, moving them to
    the conversation log. Returns [{time, from_id, message}, ...] sorted by
    time. Call this after the poller exits 0, then process messages and
    re-arm (arm_poller) to continue listening."""
    return rpc_client.call("collect_messages", {"session_id": session_id})


@mcp.tool()
def connect(caller_sid: str, target_sid: str, hold_time: int = 60) -> str:
    """Establish a p2p connection to target_sid. If the target is dead, revives
    it (claude --resume) and waits for it to come alive, sends a hello, then
    blocks up to hold_time seconds waiting for the reply. Returns
    'connect succeed; reply: ...' on success, or a 'failed, ...' /
    'connect failed, ...' string on failure (unknown target, could not revive,
    no reply, timeout)."""
    return user_functions.connect(caller_sid, target_sid, hold_time)


@mcp.tool()
def my_session_id() -> str:
    """Discover this CC's own session_id. Walks the process tree up to the
    claude.exe ancestor and looks up the session by pid. Call this first to get
    your session_id for connect/close_connection/create_collaborator."""
    return user_functions.my_session_id()


@mcp.tool()
def close_connection(session_id: str, toid: str) -> dict:
    """Close the connection from session_id to toid. Drains pending messages
    addressed to session_id (returns them as delivered_pending), notifies the
    peer with a '[CONNECTION CLOSED by <session_id>]' message, and
    unregisters. The peer learns of the close via its next collect_messages.
    Returns {closed: True, delivered_pending: [...]}."""
    return user_functions.close_connection(session_id, toid)


@mcp.tool()
def create_collaborator(caller_sid: str, cwd: str, hold_time: int = 60) -> str:
    """Spawn a NEW Claude Code session in cwd and connect to it. The new CC
    loads the plugin and listens; this tool waits for it to register, then
    connects. Returns connect's result, or 'failed' if the new CC doesn't
    register within 30s (plugin not installed for new CCs)."""
    return user_functions.create_collaborator(caller_sid, cwd, hold_time)


if __name__ == "__main__":
    mcp.run(transport="stdio")
