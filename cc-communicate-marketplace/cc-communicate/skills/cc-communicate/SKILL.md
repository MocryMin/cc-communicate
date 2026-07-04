---
name: cc-communicate
description: Discover and communicate with other Claude Code sessions on this machine — query sessions, check liveness, send/receive messages, p2p connect, and spawn collaborator sessions.
---

# cc-communicate

Discover other Claude Code sessions on this machine and communicate with them
peer-to-peer: query session info, check liveness, exchange messages, establish
p2p connections, and spawn new collaborator sessions.

CC exposes each tool as `mcp__plugin_cc-communicate_cc-communicate__<tool>`;
call them by the short names below.

## Quick start (typical p2p flow)

1. **Get your own session_id** — call `my_session_id()` first. You need this
   sid before calling connect / send_message / close_connection /
   create_collaborator.

2. **Find a peer** — `query_conversations(sid)` lists known partners;
   `query_session(target_sid)` returns a partner's info; `check_alive(sid)`
   verifies a peer is truly alive (1) or not (0).

3. **Connect** — `connect(caller_sid, target_sid)` establishes a p2p channel.
   If the target is dead, it is revived via `claude --resume` first. Blocks up
   to `hold_time` (default 60s) waiting for the peer's reply. Returns
   `"connect succeed; reply: ..."` on success.

4. **Send + listen** — `send_message(fromid, toid, message)` writes to the
   peer's pipe. To wait for replies: `arm_poller(sid)` returns a `command`;
   run it via `Bash(run_in_background=true)`. The poller exits 0 when a new
   message arrives (you get a `<task-notification>`), then call
   `collect_messages(sid)` to read and archive them. Reply, then re-arm.

5. **Close** — `close_connection(sid, toid)` drains your pending messages,
   notifies the peer with `[CONNECTION CLOSED by <sid>]`, and unregisters.
   The peer sees the close via its next `collect_messages`.

6. **Spawn a collaborator** (separate scenario) — `create_collaborator(sid,
   cwd)` starts a NEW CC in `cwd`, waits for it to register, then connects.
   The new CC must have the plugin installed (user-level install) to be
   discoverable.

## Tool reference (14 tools, grouped)

### Identity
- `my_session_id() -> str` — This CC's session_id, or `"failed, ..."`. Call
  first.
- `query_session(session_id) -> dict | null` — Session info `{pid, cwd,
  start_time, started_at, ended_at, ...}` or null if unknown.
- `check_alive(session_id) -> int` — 1 if truly alive (pid + start_time
  verified), 0 otherwise. Drops stale records in place.
- `query_conversations(session_id) -> list` — `[{partner: sid}, ...]` from
  the conversations folder (includes ended-but-not-withdrawn).

### Messaging
- `send_message(fromid, toid, message) -> str` — Write to the peer's pipe.
  Fails (`"failed, connection not registered"`) if the conversation wasn't
  registered (normally via connect).
- `register_conversation(sid_a, sid_b)` — Mark a conversation active. connect
  does this; exposed for bootstrapping/testing. Returns nothing useful.
- `unregister_conversation(sid_a, sid_b)` — Mark inactive (peer closed, etc.).
  Returns nothing useful.
- `withdraw(fromid, toid, init_connect=0) -> str` — `init_connect=1`: remove
  the whole conversation folder + unregister. `=0`: remove fromid's latest
  undelivered pipe message only.

### Spawning
- `evoke(session_id) -> str` — Revive a dead session via `claude --resume`
  (SAME session_id). Returns `"evoke spawned (resumed)"` or `"failed, session
  unknown"`. connect calls this automatically when the target is dead.

### Listening
- `arm_poller(session_id, timeout=1800) -> dict` — Returns
  `{armed, command, timeout, baseline}`. Run `command` via
  `Bash(run_in_background=true)`; poller exits 0 on new message, 2 on timeout.
- `collect_messages(session_id) -> list` — `[{time, from_id, message}, ...]`
  sorted by time. Moves collected messages pipe -> log. Call after the poller
  exits 0, then re-arm.

### Orchestration
- `connect(caller_sid, target_sid, hold_time=60) -> str` — Establish p2p.
  Query -> check_alive -> evoke+wait if dead -> register -> send hello ->
  arm+blocking poller -> collect reply. Returns `"connect succeed; reply:
  ..."` or a `"failed, ..."` / `"connect failed, ..."` string.
- `close_connection(session_id, toid) -> dict` — Drains pending (returns
  `delivered_pending`), sends `[CONNECTION CLOSED by <sid>]`, unregisters.
  Returns `{closed: True, delivered_pending: [...]}`.
- `create_collaborator(caller_sid, cwd, hold_time=60) -> str` — Spawn a NEW
  CC in `cwd`, poll until registered, then connect. Returns connect's result,
  or `"failed, new session did not register within 30s (...)"`.

## Caveats

- **Restart CC after install.** SessionStart only fires for sessions starting
  while the plugin is active. A CC opened before install is not tracked.
- **Windows only.** Linux is a stub (not implemented).
- **Call `my_session_id` first.** You need your own sid before connect /
  send_message / close_connection / create_collaborator.
- **`connect` blocks.** Up to `hold_time` (default 60s) waiting for the peer's
  reply.
- **Run the poller in the background.** `arm_poller` returns a `command`; run
  it via `Bash(run_in_background=true)`. You get a `<task-notification>` when
  it exits (0 = message arrived, 2 = timeout), then `collect_messages`.
