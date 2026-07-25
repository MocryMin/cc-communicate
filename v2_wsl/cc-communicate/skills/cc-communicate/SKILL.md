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

## The cursor ACK (v2 — PREFERRED; read this before listening)

`listen_v2` ACKs with **per-store cursors**, not timestamps. A cursor says
"everything up to this sequence FROM THIS STORE is durably received".

- **First listen_v2**: call `query_my_cursors(sid)` (or pass `{}`) to get
  `{store_id: sequence}`, then `listen_v2(sid, cursors, timeout)`.
- It returns `{messages, next_cursors}`. Each message is a record:
  `{message_id, store_id, sequence, from_session, to_session, kind,
  correlation_id, created_at_ms, payload: {text}}`.
- **Persist first, then advance**: write the messages to your own store /
  context BEFORE passing the advanced `next_cursors` back. A cursor means
  "durably received", NOT "task done".
- **Next listen_v2**: pass the returned `next_cursors` unchanged. NEVER mix
  cursor values between stores, NEVER reduce them to one number, and NEVER
  fall back to the timestamp `listen` once you use cursors (that silently
  re-enables cross-store mis-archiving).
- **Duplicates are possible** (at-least-once): dedup on `message_id`.
- **If you lose your cursors** (compact / long gap / restart): call
  `query_my_cursors(sid)`.
- **On close**: pass your latest cursors to `close_connection(sid, toid,
  cursors=...)` so the kernels persist them.
- The legacy timestamp `listen` below remains for ONE release to drain
  pre-upgrade `.md` messages; do not use it for new conversations.

## The ACK watermark (LEGACY timestamp mode — being phased out; use the cursor ACK above for new work)

`listen` uses a **timestamp ACK** so an interrupted listen never loses messages.
You keep one number - the `watermark` - and pass it back on each listen:

- **First listen**: call `listen(sid, 0, timeout)` (acked_ts = 0).
- It returns `{messages, watermark}`. The `watermark` is the max timestamp of the
  returned messages (or 0 if none).
- **Next listen**: pass that `watermark` as `acked_ts`. The kernel archives only
  messages you've confirmed (ts ≤ acked_ts) and returns newer ones.
- **If you lose the watermark** (compact / long gap / restart): call
  `query_my_ACK_timestamp(sid)` to recover it from the kernel, then use it as
  `acked_ts`.
- **On close**: pass your latest watermark to `close_connection` so the kernel
  persists it.

Why: the kernel only archives what you've *confirmed* (via the watermark you
pass back), never what it merely handed you. So a cancelled/interrupted listen
archived nothing of yours - the messages re-deliver next time. No more silent
loss when a human interrupts mid-conversation.

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
   peer's pipe. To receive: `listen(sid, acked_ts, timeout)` BLOCKS (in the MCP
   server) and returns `{messages, watermark}`. Process the messages, then call
   `listen` again with the new watermark. Keep this loop going until you close.

5. **Close** - `close_connection(sid, toid, acked_ts)` uploads your watermark
   (persisted), sends a `[CONNECTION CLOSED by <sid>]` notice to the peer (which
   tells it to upload its own ts), and unregisters. Pass your latest watermark.

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

### Listening (cursor ACK v2 preferred — see "The cursor ACK" above)
- `listen_v2(session_id, cursors=None, timeout=30) -> dict` - **PREFERRED.**
  BLOCKING. Returns `{messages, next_cursors}`; pass `next_cursors` back
  unchanged on the next call. Each message is a record envelope. See "The
  cursor ACK" above for the contract.
- `query_my_cursors(session_id) -> dict` - Recover `{store_id: sequence}`
  after compact / restart. Pass the result as `cursors` on your next
  `listen_v2`.
- `listen(session_id, acked_ts=0, timeout=30) -> dict` - LEGACY timestamp
  ACK. Kept one release to drain pre-upgrade `.md` messages. Returns
  `{messages, watermark}`. Do NOT use for new conversations.
- `query_my_ACK_timestamp(session_id) -> int` - LEGACY. Recover your
  timestamp watermark after a compact / long gap / restart. Prefer
  `query_my_cursors`.

### Orchestration
- `connect(caller_sid, target_sid, hold_time=300) -> str` - Establish p2p (local
  or cross-realm). Query -> check_alive -> evoke+wait if dead -> register ->
  send hello -> in-process wait for reply. Returns `"connect succeed; reply:
  ..."` or a `"failed, ..."` / `"connect failed, ..."` string.
- `close_connection(session_id, toid, acked_ts=0, cursors=None) -> dict` -
  Uploads your watermark and/or per-store cursors (persisted), sends the close
  notice (telling the peer to upload its ts), unregisters. Does NOT clean up
  the pipe (ts-based/cursor ACK). Returns `{closed: True}`.
- `create_collaborator(caller_sid, cwd, hold_time=300, machine=None) -> str` -
  Spawn a NEW CC in cwd (on `machine` if given, else local), poll until
  registered, then connect.

### Machines (cross-realm)
- `query_machines() -> dict` - Registered peer machines: `{id: {type, data_dir,
  ...}, ...}`. Empty until machine registration is done.
- `help_connect_machines() -> str` - Step-by-step guide for the one-time
  host ↔ WSL handshake.

## Cross-realm (Windows host ↔ WSL2)

To talk across the host/WSL boundary, register the two machines once:
1. On the **host**: `python .../server/machine_add.py` (prints "activated,
   listening...").
2. On **WSL**: `python3 .../server/machine_sign_up.py` (prints "success!").
After that, `query_session`/`check_alive`/`connect`/`send_message` automatically
fan out to the peer machine. Cross-machine messages live on the host. A WSL
caller's `listen` also scans the host (where its cross-machine convs live).

## Caveats

- **Restart CC after install.** SessionStart only fires for sessions starting
  while the plugin is active.
- **Call `my_session_id` first.** You need your own sid before connect /
  send_message / close_connection / create_collaborator.
- **`connect` blocks.** Up to `hold_time` (default 300s) waiting for the reply.
- **Connect BEFORE listen.** Connect confirms the handshake; then run the listen
  loop. (Connect consumes the hello-reply itself.)
- **Keep the watermark.** `listen` returns `{messages, watermark}`; pass that
  `watermark` as `acked_ts` on the next listen. If you lose it, call
  `query_my_ACK_timestamp`. The kernel only archives what you've confirmed, so
  an interrupted listen is safe - but you must keep passing the watermark
  forward or messages will re-deliver (harmless duplicates, no loss).
- **`listen` blocks in the MCP server.** It is NOT a shell command - call it
  directly as a tool. Do not invoke `listen.py` or write a bash listener.
- **Spawned CCs run with `--dangerously-skip-permissions`** so they skip the
  workspace-trust dialog (automation agents).
- **Cross-realm needs registration.** `query_machines()` is empty until
  `machine_add` (host) + `machine_sign_up` (WSL) have been run once.
