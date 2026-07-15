---
name: cc-communicate
description: Discover and communicate with other Claude Code sessions - on this machine or across WSL2 - query sessions, check liveness, send/receive messages, p2p connect, and spawn collaborator sessions.
---

# cc-communicate (v2)

Discover other Claude Code sessions and communicate peer-to-peer: query session
info, check liveness, exchange messages, establish p2p connections, and spawn
new collaborator sessions. Works within one machine AND across the Windows host
↔ WSL2 boundary (after one-time machine registration).

CC exposes each tool as `mcp__plugin_cc-communicate_cc-communicate__<tool>`;
call them by the short names below.

## Quick start (typical p2p flow)

1. **Get your own session_id** - call `my_session_id()` first. You need this sid
   before connect / send_message / close_connection / create_collaborator.

2. **Find a peer** - `query_conversations(sid)` lists known partners (a dict);
   `query_session(target_sid)` returns a partner's info (searches this machine
   + registered peers); `check_alive(sid)` verifies a peer is truly alive (1)
   or not (0).

3. **Connect** - `connect(caller_sid, target_sid)` establishes a p2p channel
   (local or cross-realm). If the target is dead, it is revived first. Blocks up
   to `hold_time` (default 300s) waiting for the peer's reply. Returns
   `"connect succeed; reply: ..."` on success. **Connect BEFORE listening.**

4. **Send + listen** - `send_message(fromid, toid, message)` writes to the
   peer's pipe. To wait for replies: `listen(sid)` returns a `command`; run it
   via `Bash(run_in_background=true)`. The listener prints collected messages as
   JSON on stdout and exits 0 when one arrives (you get a `<task-notification>`),
   or exits 2 on timeout. Then process, reply, and re-listen.

5. **Close** - `close_connection(sid, toid)` drains your pending messages,
   notifies the peer with `[CONNECTION CLOSED by <sid>]`, and unregisters.

6. **Spawn a collaborator** - `create_collaborator(sid, cwd)` starts a NEW CC in
   `cwd` (on this machine), waits for it to register, then connects. Pass
   `machine=<entry>` (from `query_machines`) to spawn on a registered peer
   machine. The new CC must have the plugin installed to be discoverable.

## Tool reference

### Identity
- `my_session_id() -> str` - This CC's session_id, or `"failed, ..."`. Call first.
- `query_session(session_id) -> dict | null` - Session info, or null if unknown
  everywhere (searches this machine + registered peers).
- `check_alive(session_id) -> int` - 1 if truly alive (pid + start_time verified)
  on this machine or any peer; 0 otherwise.
- `query_conversations(session_id) -> dict` - `{partner_sid: {...info}, ...}`,
  merged across this machine + peers (includes ended-but-not-withdrawn).

### Messaging
- `send_message(fromid, toid, message) -> str` - Write to the peer's pipe. Routes
  to the conversation store (host for cross-machine, else local). Fails
  (`"failed, connection not registered"`) if the conversation wasn't registered
  (normally via connect).
- `register_conversation(sid_a, sid_b)` - Mark a LOCAL conversation active
  (low-level; connect handles routing). For bootstrapping/testing.
- `unregister_conversation(sid_a, sid_b)` - Mark a LOCAL conversation inactive.
- `withdraw(fromid, toid, init_connect=0) -> str` - LOCAL: `init_connect=1`
  removes the whole folder + unregisters; `=0` removes fromid's latest
  undelivered pipe message.

### Spawning
- `evoke(session_id) -> str` - Revive a dead session on whatever machine it lives
  on (local or remote peer). Same session_id resumed. connect calls this
  automatically when the target is dead.

### Listening
- `listen(session_id, timeout=300) -> dict` - Returns `{command, timeout}`. Run
  `command` via `Bash(run_in_background=true)`; the listener prints messages
  JSON on stdout and exits 0 on arrival (you get a `<task-notification>`), or 2
  on timeout. Then process and re-listen. (Replaces the old arm_poller +
  collect_messages two-step.)

### Orchestration
- `connect(caller_sid, target_sid, hold_time=300) -> str` - Establish p2p (local
  or cross-realm). Query -> check_alive -> evoke+wait if dead -> register ->
  send hello -> in-process wait for reply. Returns `"connect succeed; reply:
  ..."` or a `"failed, ..."` / `"connect failed, ..."` string.
- `close_connection(session_id, toid) -> dict` - Drains pending (returns
  `delivered_pending`), sends `[CONNECTION CLOSED by <sid>]`, unregisters.
- `create_collaborator(caller_sid, cwd, hold_time=300, machine=None) -> str` -
  Spawn a NEW CC in cwd (on `machine` if given, else local), poll until
  registered, then connect.

### Machines (cross-realm)
- `query_machines() -> dict` - Registered peer machines: `{id: {type, data_dir,
  ...}, ...}`. Empty until machine registration is done.

## Cross-realm (Windows host ↔ WSL2)

To talk across the host/WSL boundary, register the two machines once:
1. On the **host**: `python .../server/machine_add.py` (prints "activated,
   listening...").
2. On **WSL**: `python3 .../server/machine_sign_up.py` (prints "success!").
After that, `query_session`/`check_alive`/`connect`/`send_message` automatically
fan out to the peer machine. Cross-machine messages live on the host.

## Caveats

- **Restart CC after install.** SessionStart only fires for sessions starting
  while the plugin is active.
- **Call `my_session_id` first.** You need your own sid before connect /
  send_message / close_connection / create_collaborator.
- **`connect` blocks.** Up to `hold_time` (default 300s) waiting for the reply.
- **Connect BEFORE listen.** Running a background listener while connect is in
  progress can duplicate the reply. Connect first (it confirms the handshake),
  then `listen` for ongoing messages.
- **Run the listener in the background.** `listen` returns a `command`; run it
  via `Bash(run_in_background=true)`. You get a `<task-notification>` on exit
  (0 = message arrived, 2 = timeout).
- **Spawned CCs run with `--dangerously-skip-permissions`** so they skip the
  workspace-trust dialog (automation agents).
- **Cross-realm needs registration.** `query_machines()` is empty until
  `machine_add` (host) + `machine_sign_up` (WSL) have been run once.
