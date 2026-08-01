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

## The envelope (READ FIRST)

Every tool returns the SAME 5-field envelope:

```
{ok, code, message, data, retryable}
```

- `ok` - True on success. When ok, `code` and `message` are None.
- `code` - one of:
  `INVALID_ARGUMENT` (bad session/message/connection/spawn-token id, or a
  message over the inline cap), `NOT_FOUND` (session / conversation /
  message / pending-spawn token unknown), `PEER_UNREACHABLE` (a peer
  machine is down), `TIMEOUT` (a wait expired), `CONFLICT` (the pair's
  connection is already active under a DIFFERENT connection_id),
  `NOT_ALIVE` (the target could not be revived), `RESOURCE_EXHAUSTED`
  (reserved for the Wave 3 resource policy; no tool returns it yet),
  `INTERNAL` (kernel / transport failure).
- `message` - human-readable detail only. It is NOT part of the contract:
  NEVER branch on it, NEVER log it as the outcome.
- `data` - the structured payload (per-tool shape below), or None.
- `retryable` - True ONLY for transient failures where retrying the SAME
  operation is safe and expected (a TIMEOUT after a connect or spawn wait).
  When False, do not blindly retry - fix the input or the state first.

ALWAYS branch on `ok` / `code` / `retryable` and read `data`. On a
`retryable: true` error, retry the identical call - the operations are
idempotent by design (same message_id, same connection_id, same
spawn_token). Never parse `message` text.

## The cursor ACK (v2 - PREFERRED; read this before listening)

`listen_v2` ACKs with **per-store cursors**, not timestamps. A cursor says
"everything up to this sequence FROM THIS STORE is durably received".

- **First listen_v2**: call `query_my_cursors(sid)` (or pass `{}`) to get
  `{store_id: sequence}`, then `listen_v2(sid, cursors, timeout)`.
- It returns the envelope with `data = {messages, next_cursors}`. Each
  message is a record: `{message_id, store_id, sequence, from_session,
  to_session, kind, correlation_id, created_at_ms, payload: {text}}`.
- **Persist first, then advance**: write the messages to your own store /
  context BEFORE passing the advanced `next_cursors` back. A cursor means
  "durably received", NOT "task done".
- **Next listen_v2**: pass `data.next_cursors` unchanged. NEVER mix cursor
  values between stores, NEVER reduce them to one number, and NEVER fall
  back to the timestamp `listen` once you use cursors (that silently
  re-enables cross-store mis-archiving).
- **Duplicates are possible** (at-least-once): dedup on `message_id`.
- **If you lose your cursors** (compact / long gap / restart): call
  `query_my_cursors(sid)`.
- **On close**: pass your latest cursors to `close_connection(sid, toid,
  cursors=...)` so the kernels persist them.
- The legacy timestamp `listen` below remains for ONE release to drain
  pre-upgrade `.md` messages; do not use it for new conversations.

## The ACK watermark (LEGACY timestamp mode - being phased out; use the cursor ACK above for new work)

`listen` uses a **timestamp ACK** so an interrupted listen never loses
messages. You keep one number - the `watermark` - and pass it back on each
listen:

- **First listen**: call `listen(sid, 0, timeout)` (acked_ts = 0).
- It returns the envelope with `data = {messages, watermark}`. The
  `watermark` is the max timestamp of the returned messages (or 0 if none).
- **Next listen**: pass that `watermark` as `acked_ts`. The kernel archives
  only messages you've confirmed (ts <= acked_ts) and returns newer ones.
- **If you lose the watermark** (compact / long gap / restart): call
  `query_my_ACK_timestamp(sid)` to recover it from the kernel, then use it
  as `acked_ts`.
- **On close**: pass your latest watermark to `close_connection` so the
  kernel persists it.

Why: the kernel only archives what you've *confirmed* (via the watermark you
pass back), never what it merely handed you. So a cancelled/interrupted
listen archived nothing of yours - the messages re-deliver next time. No
more silent loss when a human interrupts mid-conversation.

## Quick start (typical p2p flow)

1. **Get your own session_id** - call `my_session_id()` first (envelope,
   `data` = your sid). You need this sid before connect / send_message /
   close_connection / spawn_collaborator.

2. **Find a peer** - `query_conversations(sid)` lists known partners
   (`data` = a dict `{partner_sid: {...info}, ...}`); `query_session(sid)`
   returns a partner's info (`data` = info or None - searches this machine +
   registered peers); `check_alive(sid)` verifies a peer is truly alive
   (`data` = 1 or 0).

3. **Connect** - `connect(caller_sid, target_sid, connection_id=None)`:
   establishes a p2p channel (local or cross-realm). If the target is dead,
   it is revived first. Blocks up to `hold_time` (default 300s) waiting for
   the peer's reply. On success `data = {connection_id, reply,
   established_at_ms, reused}`. **Connect BEFORE listening.**

4. **Send + listen** - `send_message(fromid, toid, message)` writes to the
   peer's pipe (`data` = `{message_id, ts}`). To receive: `listen_v2(sid,
   cursors, timeout)` (or legacy `listen(sid, acked_ts, timeout)`) BLOCKS
   (in the MCP server) and returns the envelope with
   `data = {messages, next_cursors}` / `{messages, watermark}`. Process the
   messages, then call again with the advanced cursors / watermark. Keep
   this loop going until you close.

5. **Close** - `close_connection(sid, toid, acked_ts=0, cursors=None)`
   uploads your watermark and/or per-store cursors (persisted), sends a
   `[CONNECTION CLOSED by <sid>]` notice to the peer (which tells it to
   upload its own ts), unregisters, and marks the connection closed. Pass
   your latest watermark / cursors.

6. **Spawn a collaborator** - `spawn_collaborator(sid, cwd)` starts a NEW CC
   in `cwd` and returns a structured WorkerHandle (it does NOT auto-connect -
   call `connect` when you want the channel). Pass `machine=<entry>` (from
   `query_machines`) to spawn on a registered peer machine. The new CC must
   have the plugin installed to be discoverable.

## Spawning a collaborator (the worker playbook)

`spawn_collaborator(caller_sid, cwd, spawn_token=None, permission_mode="bypass",
machine=None, hold_time=300)` starts a new CC in `cwd` and waits for it to
register (up to 30s). Returns the envelope with `data` = the WorkerHandle:

```
{session_id, machine_id, cwd, spawn_token, connection_status}
```

- `connection_status` is `"registered"` once the worker is discoverable.
- **Same-token retry is idempotent**: pass your own `spawn_token` and a
  retry with the SAME token returns the original handle instead of spawning
  a second CC (a pending marker prevents double-spawn even while the first
  is still booting). Omit it and the server generates one (returned in the
  handle).
- `permission_mode` is accepted now (default `"bypass"` = current behavior;
  Wave 3 HP-10 flips the default to `"standard"` - the parameter surface
  never changes).
- Failure to register within 30s -> `err(TIMEOUT, retryable: true)`.

The spawned worker is prompted to (and should) do this on its side:

1. Call `my_session_id()` first.
2. Call `claim_pending_spawn('<spawn_token>', <your_id>)` once, on its FIRST
   tool use - it lets the spawner's registration poll resolve if the start
   event was missed (plan B fallback; plan A binds the session to the token
   via the spawn env var + SessionStart hook). Idempotent - a no-op if your
   session was already claimed.
3. Run the listen loop: `listen(your_id, acked_ts, timeout)` - pass 0 the
   first time, then the returned `watermark` on every later call. Keep
   listening in a loop until you call `close_connection`.
4. **On a hello (kind=hello carrying a `correlation_id`): reply with
   `send_message(your_id, peer_id, <message>, correlation_id=<the hello's
   correlation_id>)`** - do NOT call connect to reply. The correlation_id
   makes your reply selectable by the caller's `connect` (its reply poll
   matches on it). A plain reply without the correlation_id only works
   while the caller's legacy fallback is alive (one release).
5. If you lose your watermark (compact / long gap), recover it with
   `query_my_ACK_timestamp(your_id)`.

Never invoke listen.py directly, never write a shell loop, never nohup a
listener - only use the listen/listen_v2 tool.

## Tool reference

### Identity
- `my_session_id() -> dict` - This CC's session_id. `data` = sid. Call first.
- `query_session(session_id) -> dict` - Session info or None if unknown
  everywhere (searches this machine + registered peers).
- `check_alive(session_id) -> dict` - `data` = 1 if truly alive (pid +
  start_time verified) on this machine or any peer; 0 otherwise.
- `query_conversations(session_id) -> dict` - `data` = `{partner_sid:
  {...info}, ...}`, merged across this machine + peers (includes
  ended-but-not-withdrawn).

### Messaging
- `send_message(fromid, toid, message, correlation_id=None, kind=None) ->
  dict` - Write to the peer's pipe. `data` = `{message_id, ts}`. Routes to
  the conversation store (host for cross-machine, else local). Fails with
  `err(NOT_FOUND)` if the conversation wasn't registered (normally via
  connect). `correlation_id` (a 1-128 char id-token): correlates this
  message with a reply - connect's hello uses it, and a worker replying to a
  hello MUST echo the hello's correlation_id (see the worker playbook).
  `kind`: free-form tag, defaults to `"text"` (`"hello"` for connect's
  handshake). Messages are capped at 1 MiB inline
  (`CC_COMMUNICATE_MAX_INLINE_BYTES`); over the cap ->
  `err(INVALID_ARGUMENT)`.
- `register_conversation(sid_a, sid_b) -> dict` - Mark a LOCAL conversation
  active (low-level; connect handles routing). For bootstrapping/testing.
- `unregister_conversation(sid_a, sid_b) -> dict` - Mark a LOCAL
  conversation inactive (low-level).
- `withdraw(fromid, toid, init_connect=0, message_id=None) -> dict` -
  LOCAL. `init_connect=1`: remove the whole folder + unregister; `=0`
  default: withdraw fromid's latest undelivered pipe message (non-
  idempotent). `message_id`: withdraw that EXACT message (retry-safe;
  preferred). `err(NOT_FOUND)` when there is nothing to withdraw.

### Spawning
- `evoke(session_id) -> dict` - Revive a dead session on whatever machine it
  lives on (local or remote peer). Same session_id resumed. `data` =
  `{evoked: True, session_id}`; `err(NOT_FOUND)` when the session does not
  exist. connect calls this automatically when the target is dead.
- `spawn_collaborator(caller_sid, cwd, spawn_token=None,
  permission_mode="bypass", machine=None, hold_time=300) -> dict` - Spawn a
  NEW CC in cwd (on `machine` if given - a `query_machines` entry - else
  this machine) and wait for it to register. `data` = WorkerHandle
  `{session_id, machine_id, cwd, spawn_token, connection_status}`. Does NOT
  auto-connect - call `connect` when you want the channel. Same-token
  retries return the original handle (no second spawn). See "Spawning a
  collaborator" above.
- `claim_pending_spawn(spawn_token, session_id) -> dict` - Claim a pending
  spawn token (plan B, D8): a spawned worker calls this on its FIRST tool
  use so the spawner's registration poll can resolve. Idempotent - safe to
  call more than once. `data` = `{claimed: True, session_id}` or
  `err(NOT_FOUND)` when no pending spawn matches the token.

### Listening (cursor ACK v2 preferred - see "The cursor ACK" above)
- `listen_v2(session_id, cursors=None, timeout=30) -> dict` - **PREFERRED.**
  BLOCKING. `data` = `{messages, next_cursors}`; pass `data.next_cursors`
  back unchanged on the next call. Each message is a record envelope. See
  "The cursor ACK" above for the contract.
- `query_my_cursors(session_id) -> dict` - Recover `data` =
  `{store_id: sequence}` after compact / restart. Pass the result as
  `cursors` on your next `listen_v2`.
- `listen(session_id, acked_ts=0, timeout=30) -> dict` - LEGACY timestamp
  ACK. Kept one release to drain pre-upgrade `.md` messages. `data` =
  `{messages, watermark}`. Do NOT use for new conversations.
- `query_my_ACK_timestamp(session_id) -> dict` - LEGACY. Recover your
  timestamp watermark (`data` = ts, 0 if none) after a compact / long gap /
  restart. Prefer `query_my_cursors`.

### Orchestration
- `connect(caller_sid, target_sid, connection_id=None, hold_time=300) ->
  dict` - Establish p2p (local or cross-realm). Query -> check_alive ->
  evoke + wait if dead -> register -> send hello (kind=hello,
  correlation_id=connection_id) -> in-process wait for the
  correlation-matched reply (blocks up to hold_time; running a listener
  during connect can duplicate the reply). Success: `data` =
  `{connection_id, reply, established_at_ms, reused}`. `connection_id`
  (caller-supplied, or server-generated and returned): retries with the SAME
  id are idempotent (an active connection with the same id returns the
  current state with `reused: True`); ONE active connection per pair - a
  DIFFERENT id while one is active is `err(CONFLICT, data:
  {current_connection_id, status})`. Other errors: `NOT_FOUND` (target
  unknown), `NOT_ALIVE` (evoke failed / target never revived - retryable),
  `TIMEOUT` (no reply - retryable), `PEER_UNREACHABLE` / `INTERNAL`. Once
  connect succeeds the channel is ESTABLISHED: you MUST then call listen in
  a loop and keep it active until you call `close_connection`.
- `close_connection(session_id, toid, acked_ts=0, cursors=None) -> dict` -
  Terminate the connection to toid (the ONLY way to stop your listen loop).
  Uploads your watermark and/or per-store cursors (each cursor only to the
  kernel owning that store; unknown store ids ignored), sends the close
  notice (telling the peer to upload its ts), unregisters, marks
  info.json closed. Best-effort and non-blocking: returns `data =
  {closed: True}` immediately. Does NOT clean up the pipe (ts/cursor ACK:
  un-acked messages stay). Safe to call even if the peer is unreachable.
- `create_collaborator(caller_sid, cwd, hold_time=300, machine=None) ->
  str` - **LEGACY wrapper (one release)**: spawn + connect in one call,
  returning the OLD string shape (`"connect succeed; reply: ..."` /
  `"failed, ..."` / `"connect failed, ..."`) instead of the envelope. New
  code should use `spawn_collaborator` (structured WorkerHandle) +
  `connect`. Legacy-spawned workers reply WITHOUT correlation_id, exercising
  connect's one-release legacy fallback.

### Machines (cross-realm)
- `query_machines() -> dict` - Registered peer machines: `data` = `{id:
  {type, data_dir, ...}, ...}`. Empty until machine registration is done.
- `help_connect_machines() -> dict` - `data` = step-by-step guide for the
  one-time host <-> WSL handshake. Call this when the user wants to link
  machines, then follow it - asking clarifications and driving both sides'
  handshake scripts yourself (cross-realm exec).

## Cross-realm (Windows host <-> WSL2)

To talk across the host/WSL boundary, register the two machines once:
1. On the **host**: `python .../server/machine_add.py` (prints "activated,
   listening...").
2. On **WSL**: `python3 .../server/machine_sign_up.py` (prints "success!").
After that, `query_session`/`check_alive`/`connect`/`send_message`
automatically fan out to the peer machine. Cross-machine messages live on
the host. A WSL caller's `listen` also scans the host (where its
cross-machine convs live).

## Caveats

- **Restart CC after install.** SessionStart only fires for sessions starting
  while the plugin is active.
- **Call `my_session_id` first.** You need your own sid before connect /
  send_message / close_connection / spawn_collaborator.
- **`connect` blocks.** Up to `hold_time` (default 300s) waiting for the
  reply.
- **Connect BEFORE listen.** Connect confirms the handshake; then run the
  listen loop. (Connect consumes the hello-reply itself.)
- **Keep the watermark / cursors.** `listen` returns `{messages,
  watermark}`; `listen_v2` returns `{messages, next_cursors}` - pass them
  forward on the next call. If you lose them, recover via
  `query_my_ACK_timestamp` / `query_my_cursors`. The kernel only archives
  what you've confirmed, so an interrupted listen is safe - but you must
  keep passing the ACK forward or messages re-deliver (harmless
  duplicates, no loss).
- **`listen`/`listen_v2` block in the MCP server.** They are NOT shell
  commands - call them directly as tools. Do not invoke listen.py or write a
  bash listener.
- **Never parse `message` text.** Branch on `ok` / `code` / `retryable` /
  `data` only - see "The envelope" above.
- **Spawned CCs run with `--dangerously-skip-permissions`** so they skip the
  workspace-trust dialog (automation agents). The `permission_mode`
  parameter already exists; Wave 3 flips its default.
- **Cross-realm needs registration.** `query_machines()` is empty until
  `machine_add` (host) + `machine_sign_up` (WSL) have been run once.
