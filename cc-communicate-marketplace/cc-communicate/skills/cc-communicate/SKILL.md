---
name: cc-communicate
description: Use to discover and communicate with other Claude Code sessions on this machine — query sessions, check liveness, send messages, listen for replies, connect p2p, and spawn collaborator sessions.
---

# cc-communicate — open session registry

## Architecture: append-only event log + kernel server

The cc-communicate plugin records session activity as an **append-only log** of event
files in `${CLAUDE_PLUGIN_ROOT}/data/session_ctrl/`:

- `start_<event_ts>_<session_id>.json` — written by the **SessionStart** hook.
  Body: `{ event, event_ts, session_id, pid, cwd, start_time, source }`.
- `end_<event_ts>_<session_id>.json` — written by the **SessionEnd** hook.
  Body: `{ event, event_ts, session_id }`.

The hooks **never read, never lock, never mutate** a shared table — they only
append one uniquely-named file per event (filename uniqueness = timestamp +
session_id + exclusive-create retry). This eliminates all writer contention.

A separate **kernel server** (the consumer; not part of the hook) reads this
folder, replays events in timestamp order into an in-memory `session_status`
table (`start` → upsert by `session_id`, `end` → delete), and owns
**liveness / zombie judgement** — lazily, only when the upper layer submits an
access request. Because the table lives in the server's private memory, it
needs no lock; the append-only log is the durable source of truth and lets the
server be started/rebuilt on demand.

### Liveness rule (server's job)

A row is **LIVE** iff a process with the recorded `pid` exists **and** its
creation time matches `start_time` (matching defeats PID reuse). Otherwise
**ZOMBIE** (process gone) or **STALE?** (pid reused by an unrelated process).

## How to access the registry

> The kernel server is the intended access point. While the server is not yet
> wired in, the raw event log can be inspected directly for debugging by listing
> `${CLAUDE_PLUGIN_ROOT}/data/session_ctrl/`. Once the server exists it will
> expose the live, pruned table on request (this section will be updated with
> the exact invocation).

## Caveats

- Sessions open **before** the plugin was enabled are NOT logged — SessionStart
  only fires for sessions that start while the plugin is active. Restart cc
  after enabling.
- The event log is a **temporary, dynamic** artifact; it is not version-
  controlled and accumulates over time (compaction/reaping is the server's job).
- `start_time` is the `claude` process's creation time (for liveness); `event_ts`
  is when the hook fired (for log ordering). Keep them distinct.
