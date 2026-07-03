"""cc-communicate MCP server — thin shell exposing user functions as MCP tools.

CC starts this process per session (see .mcp.json). The server holds NO state;
every tool call forwards to the shared kernel via rpc_client.call() (which
ensure_core()s the kernel, writes a queue request, polls for the response).

Tools currently exposed (one per kernel function):
  query_session, check_alive, query_conversations,
  send_message, register_conversation, unregister_conversation, withdraw.

TODO (next increments): connect, keep_listen, close_connection,
create_collaborator, evoke — these involve orchestration / process spawning
beyond a thin RPC wrapper, and will be added as the p2p layer is completed.
"""
from mcp.server.fastmcp import FastMCP

import rpc_client

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
    """Spawn a new Claude Code session in the given session's working directory
    (Windows). Use to revive a dead peer: the spawned CC loads the plugin and
    waits for messages. The new CC gets a fresh session_id (discovered later via
    its SessionStart hook). Fails if the session is unknown or has no cwd."""
    return rpc_client.call("evoke", {"session_id": session_id})


if __name__ == "__main__":
    mcp.run(transport="stdio")
