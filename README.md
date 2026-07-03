# cc-communicate — Claude Code p2p communication plugin

A Claude Code plugin enabling **peer-to-peer communication between CC sessions**
on the same machine: sessions discover each other via hooks, connect
bidirectionally, exchange messages through file-based pipes, and spawn new
collaborator sessions on demand.

## Project status

| Layer | What | Status |
|---|---|---|
| **Lower** (Node) | `cc-monitor/scripts/` — hook-triggered event log: SessionStart/End → append-only JSON in `data/session_ctrl/` | ✅ built & verified (Windows) |
| **Upper** (Python) | `cc-monitor/server/` — kernel daemon + MCP tools (queue RPC, session registry, p2p message pipes, keep_listen, connect, evoke, create_collaborator) | ✅ built & unit-tested (Windows) |
| **End-to-end** | Real two-CC p2p communication through an installed plugin | ❌ not yet tested |
| **Linux** | Spawn + proc branches | ❌ stubs only (Win-only) |

Built in 8 commits (all pushed to `origin/main`, repo: `MocryMin/cc-communicate`). The `core_plan.md` at the project root is the authoritative design document
— most implementation decisions reference it.

---

## Repository layout

```
cc-monitor-marketplace/          ← marketplace root (for /plugin marketplace add)
├── .claude-plugin/
│   └── marketplace.json          ← lists plugin ./cc-monitor
└── cc-monitor/                   ← THE PLUGIN
    ├── .claude-plugin/
    │   └── plugin.json           ← plugin manifest
    ├── .mcp.json                 ← MCP server declaration (CC reads this)
    ├── .gitignore                ← excludes data/ + __pycache__/
    ├── hooks/
    │   └── hooks.json            ← SessionStart/End → registrar.js
    ├── scripts/                  ← LOWER LAYER (Node, frozen)
    │   ├── registrar.js          ← event producer (hook entry)
    │   └── lib/
    │       ├── paths.js          ← path constants (shared contract)
    │       └── proc.js           ← process introspection (shared contract)
    ├── skills/cc-monitor/
    │   └── SKILL.md              ← agent-facing skill (⚠️ still placeholder — see §Remaining)
    └── server/                   ← UPPER LAYER (Python) — THIS IS WHAT WAS BUILT
        ├── paths.py              ← path constants (frozen-equiv of paths.js)
        ├── proc.py               ← process introspection (psutil; port of proc.js)
        ├── check_core.py         ← ensures kernel alive (lazy-start + verify)
        ├── kernel.py             ← kernel daemon (backoff loop, event replay, queue dispatch)
        ├── kernel_api.py         ← kernel functions (query_session, check_alive, send_message, …)
        ├── conversations.py      ← folder/pipe path helpers + count_undelivered
        ├── rpc_client.py         ← tool-side queue RPC client
        ├── spawn.py              ← platform-specific CC spawning (spawn_cc_new / spawn_cc_resume)
        ├── listen_poller.py      ← background poller for keep_listen
        ├── mcp_server.py         ← FastMCP server — thin shell exposing 14 tools
        ├── user_functions.py     ← orchestration: connect, close_connection, create_collaborator, my_session_id
        └── requirements.txt      ← psutil, filelock, mcp
```

The `data/` directory inside `cc-monitor/` is runtime-only (gitignored):

```
data/
├── session_ctrl/                 ← append-only event log (lower layer writes)
│   ├── start_<ts>_<sid>.json
│   └── end_<ts>_<sid>.json
├── server/                       ← kernel products (sessions.json, core_status.json, kernel.log)
├── queue/                        ← RPC request files (MCP tools write → kernel reads)
│   └── responses/                ← RPC response files (kernel writes → MCP tools read)
└── conversations/                ← p2p message pipes
    └── <sid_a>__<sid_b>/         ← canonical folder (sids sorted)
        ├── info.json             ← conversation metadata (future)
        ├── pipe/                 ← undelivered messages: <ts>__<fromid>__<toid>.md
        └── log/                  ← delivered/archived messages
```

---

## Architecture overview

### Two-layer design

The plugin has a **persistent kernel** (one per machine, lazy-started) and a
**per-session MCP server** (one per CC session, auto-started by CC):

```
                  ┌──────────┐       ┌──────────┐
                  │  CC-A    │       │  CC-B    │
                  └────┬─────┘       └────┬─────┘
                       │ MCP              │ MCP
                  ┌────▼─────┐       ┌────▼─────┐
                  │MCP server│       │MCP server│  (per-session, thin shell)
                  └────┬─────┘       └────┬─────┘
                       │ queue RPC        │
                  ┌────▼──────────▼───────▼─────┐
                  │        KERNEL (daemon)      │  (one per machine)
                  │  backoff loop, event replay,│
                  │  queue dispatch, conv state │
                  └────────────────────────────┘
                       │
          ┌────────────┼─────────────┐
  ┌───────▼──────┐ ┌───▼────┐ ┌─────▼──────────┐
  │session_ctrl/ │ │ queue/ │ │ conversations/ │ (file-based storage)
  │ (event log)  │ │ (RPC)  │ │ (p2p pipes)    │
  └──────────────┘ └────────┘ └────────────────┘
```

### Kernel lifecycle (core_plan #11)

The kernel is **lazy-started**, not a boot-time daemon:

1. **Start**: any MCP tool call triggers `check_core.ensure_core()`, which
   acquires a file lock on `core_status.json`, verifies no alive kernel exists
   (checks pid + start_time via proc.py → psutil), and spawns `kernel.py` as a
   detached subprocess if needed. The kernel writes `{status:1, pid, start_time}`
   as a READY signal; `check_core` waits for it before returning.

2. **Loop**: backoff 1ms → … → 1s. Each cycle replays new `session_ctrl/` events
   and drains `queue/` request files, dispatching to `kernel_api`.

3. **Exit** (three conditions, all must be true): alive_conversations empty
   **AND** no queue activity for idle_timeout (default 600s)
   **AND** queue empty (exit-race mitigation). Writes `status=0`, saves
   `sessions.json`, exits.

### Queue RPC

MCP tools (in `mcp_server.py`) don't call the kernel directly. They write a
request to `data/queue/` and poll for the response in
`data/queue/responses/`:

```
MCP tool → rpc_client.call("function", {args})
  → ensure_core()                   // kernel alive
  → write queue/<ts>_<rid>.json     // request
  → poll queue/responses/<rid>.json // wait for response (timeout + 1 retry per #11c)
  → return result
```

The kernel's `drain_queue()` picks up requests (sorted by file name),
deserializes `{request_id, function, args}`, calls `_dispatch(function, args)`,
writes the response, and removes the request file.

Kernel functions live in `kernel_api.py` and receive state (sessions,
alive_sessions, alive_conversations) as explicit parameters — no globals,
easy to test in isolation.

---

## MCP tools — the upper-layer CC interface

All 14 tools are exposed via `mcp_server.py` (FastMCP, stdio transport).
CC names them `mcp__plugin_cc-monitor_cc-communicate__<tool>`.

### Identity

| Tool | Sig | Returns |
|---|---|---|
| `my_session_id` | `() -> str` | This CC's session_id, or "failed, …". Walks the process tree up to `claude.exe` via `proc.resolve_claude()`, then looks up the session by pid. Call this FIRST to learn your own id before calling connect etc. |

### Read-only queries

| Tool | Sig | Description |
|---|---|---|
| `query_session` | `(session_id: str) -> dict` | Session info `{pid, cwd, start_time, started_at, ended_at, …}` or null |
| `check_alive` | `(session_id: str) -> int` | 1 if truly alive (pid + start_time verified via psutil, defeats PID reuse). 0 otherwise. Drops stale records in place. |
| `query_conversations` | `(session_id: str) -> list` | `[{partner: sid}, …]` — partners from the conversations folder (includes ended but not withdrawn) |

### Messaging

| Tool | Sig | Description |
|---|---|---|
| `send_message` | `(fromid: str, toid: str, message: str) -> str` | Write a message to the peer's pipe. Fails ("failed, connection not registered") if the conversation wasn't registered by connect. |
| `register_conversation` | `(sid_a: str, sid_b: str) -> str` | Mark a conversation active. Order-independent (sids sorted). connect does this; exposed for testing/bootstrapping. |
| `unregister_conversation` | `(sid_a: str, sid_b: str) -> str` | Mark inactive. |
| `withdraw` | `(fromid: str, toid: str, init_connect: int=0) -> str` | `init_connect=1`: remove the whole conversation folder + unregister. `=0`: remove fromid's latest undelivered pipe message only. |

### Spawning

| Tool | Sig | Description |
|---|---|---|
| `evoke` | `(session_id: str) -> str` | **Revive** a dead session via `claude --resume <sid> <prompt>` (same session_id, same cwd — user-verified on Windows). connect polls `check_alive` afterward until the session is alive again. Fails if session unknown. |

### Listening (keep_listen)

| Tool | Sig | Description |
|---|---|---|
| `arm_poller` | `(session_id: str, timeout: int=1800) -> dict` | Write a poller config `{baseline: current undelivered count, deadline}` and return `{armed, command, baseline}`. CC should run the `command` via `Bash(run_in_background=true)`. The poller exits 0 when a new message arrives, 2 on timeout. Scans ALL conversation folders (re-scanning each cycle) so a folder appearing AFTER arming is still detected. |
| `collect_messages` | `(session_id: str) -> list` | `[{time, from_id, message}, …]` sorted by time. Moves collected messages from `pipe/` to `log/`. Call after the poller exits 0, then process the messages and re-arm. |

### Orchestration

| Tool | Sig | Description |
|---|---|---|
| `connect` | `(caller_sid: str, target_sid: str, hold_time: int=60) -> str` | Establish a p2p connection. Flow: query→check_alive→evoke+wait if dead→register→send hello→arm+blocking poller→collect reply. Returns "connect succeed" or "failed, …". Blocks up to `hold_time` seconds. |
| `close_connection` | `(session_id: str, toid: str) -> dict` | Drains pending messages addressed to session_id (returns them as `delivered_pending`), sends a `[CONNECTION CLOSED]` notification to the peer, then unregisters. The peer learns of the close via its next `collect_messages`. |
| `create_collaborator` | `(caller_sid: str, cwd: str, hold_time: int=60) -> str` | Spawn a NEW CC in `cwd` (`claude --cwd <cwd> <prompt>`, detached window), poll `find_new_session` until the new CC's SessionStart registers it, then `connect`. Returns connect's result. The new CC must load the plugin (user-level install) to be discoverable. Times out after 30s if the new CC doesn't register. |

---

## How a CC uses the plugin (typical flow)

### 1. Get your own session_id

```
my_session_id()  →  "8ed4ef97-f04c-45dd-9742-d56af88ce551"
```

### 2. List known sessions and find a peer

```
query_conversations("8ed4ef97-…")  →  [{partner: "57609cfc-…"}, …]
query_session("57609cfc-…")        →  {pid, cwd, started_at, …}
check_alive("57609cfc-…")          →  1  (alive)
```

### 3. Connect and send messages

```
connect("8ed4ef97-…", "57609cfc-…")  →  "connect succeed; reply: …"
send_message("8ed4ef97-…", "57609cfc-…", "hello peer")
```

### 4. Listen for replies (ongoing)

```
arm_poller("8ed4ef97-…", timeout=600)  →  {command: "python …/listen_poller.py 8ed4ef97-…"}
Bash(command, run_in_background: true)
# … CC waits (nothing to do during arm)
# <task-notification> poller exited 0  (new message arrived)
collect_messages("8ed4ef97-…")  →  [{time, from_id, message: "reply"}, …]
# process the messages, reply, re-arm
```

### 5. Spawn a collaborator

```
create_collaborator("8ed4ef97-…", "C:/projects/worker", hold_time=60)
  → spawns CC in C:/projects/worker, waits for register, connects
  → "connect succeed; reply: …"
```

---

## Technical details — things the next builder must know

### `conversations.py`: folder/pipe conventions

- **Separator**: `__` (double underscore). Session_ids are UUIDs (hyphens only, no underscores), so splitting on `__` is unambiguous.
- **Canonical folder**: the two sids are **sorted** before joining — `conv_dir(A,B)` and `conv_dir(B,A)` return the same path. This satisfies core_plan #8 (order-independent).
- **Pipe filename**: `<ts:013d>__<fromid>__<toid>.md` — ts-first so lexical sort = chronological (same convention as the lower layer's event files).
- **`count_undelivered(sid)`**: counts pipe messages addressed to `sid` across ALL conversation folders. The poller calls this every cycle (re-scans all folders), so a conversation folder appearing AFTER arming (a new partner's first message) is still detected. No fixed watch-dirs.

### Lower layer contract (event log replay)

The lower layer (`scripts/registrar.js`) writes event files to `data/session_ctrl/` with filenames `start_<ts>_<sid>.json` / `end_<ts>_<sid>.json`. The kernel replays these via `process_session_ctrl_event()`.

**Critical sorting bug**: event filenames are NOT chronologically sorted by file name (because `end_` < `start_` alphabetically, all end events sort before start events regardless of timestamp). The kernel MUST sort by `event_ts` (the field inside the payload), as the lower layer's README (README §1) explicitly requires. The sorted list of new events is read, sorted by `event_ts`, and processed in that order.

Event replay logic:
- **start** event: upsert `sessions[sid]` (latest pid/cwd/start_time; `first_seen` preserved), add to `alive_sessions[sid]`.
- **end** event: remove from `alive_sessions`, set `sessions[sid]["ended_at"]`.

Edge cases correctly handled:
- **Pre-install end** (end event for a session with no preceding start): ignored (session not yet in sessions). Later start creates the record.
- **Resumed session** (start→end→start→end for the same sid): each start resets `ended_at=None`, the final end sets it.

### Cross-layer start_time comparison

`proc.js` (lower layer, Node) writes `start_time` as a CIM ISO8601 string
(e.g. `2026-07-03T14:59:00.1599700+08:00`, up to 7 fractional digits).
`proc.py` (upper layer, Python) reads via `psutil.create_time()` which returns
epoch seconds.

For `check_alive` to compare them: `proc.parse_start_time(event_iso_string)` →
epoch float. Then `abs(stored_epoch - psutil_create_time) < 1.0` → match.
Verified exact-match on a real process (pid 57700, `1783061940.15997 ==
1783061940.15997`).

`parse_start_time()` truncates 7-digit fractions to 6 (Python <3.11
`fromisoformat` compatibility) and handles `Z` suffix → `+00:00`.

### Kernel single-instance enforcement

`check_core.ensure_core()` uses `filelock` on `core_status.json.lock`. The lock
serializes all callers. `core_status.json` = `{status: 0|1, pid, start_time}`.
`_is_kernel_alive(st)` doesn't just read `status=1` — it verifies the recorded
`pid` is actually alive with the same `start_time` (`proc_start_time(pid) +
tolerance`). This defeats stale status after a hard crash (the kernel dies
without writing status=0).

### Exit-race mitigations (core_plan #11c)

1. **Kernel side**: exit condition includes `_queue_has_pending()` — the kernel
   won't exit while queue has work. Before writing `status=0`, it does a final
   scan; if the queue has work, it stays alive.
2. **Tool side**: `rpc_client.call()` has a timeout + one retry. On timeout, it
   re-runs `ensure_core()` (which detects a dead kernel and starts a new one)
   and re-submits.

### Key design deviations from core_plan

| Deviation | Plan said | What was built | Why |
|---|---|---|---|
| Poller language | `listen-poller.sh` (bash) | `listen_poller.py` (Python) | User audits Python, not bash. Token economy unchanged (CC issues one Bash call, poller consumes no tokens, exit wakes CC). Paths via `paths.py` (no Git-Bash Windows-path quirks). |
| `evoke` revives | `claude --cwd <dir> <prompt>` (new session_id) | `claude --resume <sid> <prompt>` (same session_id) | User verified `--resume` works on Windows. Same session_id means `connect` can talk to the revived peer without pending-connect. |
| `connect` blocking | 10min timeout, register after success | 60s default, register before hello | 10min blocks CC too long. Register before hello so `send_message` works (plan's order would fail — send_message checks registration). Withdraw on failure cleans up. |
| `create_collaborator` | kernel-side pending-connect file | caller-side poll (`find_new_session` reads sessions by cwd) | Simpler, no kernel-side pending state. Caller polls until the new CC's SessionStart registers it. |
| `my_session_id` | (not in plan) | `proc.resolve_claude` + `session_by_pid` | A CC needs its own session_id to call connect/etc. Without this tool the plugin is unusable. |
| `alive_sessions` snapshot | persist + load on restart | Dropped; replay-all on init | Event log is ground truth. Replay all events on init is simple, correct, and fast for current volumes. `sessions.json` is the persistent registry. |

### Python dependencies

`server/requirements.txt`: `psutil>=5.9`, `filelock>=3.12`, `mcp>=1.28`.

The MCP SDK (`mcp`) provides `FastMCP` for the server and the client SDK for
testing. `filelock` provides the cross-platform lock for `check_core`.

---

## Install & test

### Install

```
/plugin marketplace add "C:\研究生\实习\learn AI\projects\hello cc\cc-monitor-marketplace"
/plugin install cc-monitor@cc-monitor-local
```

Then **fully restart CC** (SessionStart only fires for sessions starting while the plugin is active).

### Verify installation

```
/mcp                          # look for cc-communicate server
claude --debug                # look for MCP server startup logs
my_session_id()               # should return your session_id (resolve_claude works)
query_session(<your_sid>)     # should return your session info
```

### In-process unit testing

```bash
cd cc-monitor/server
python -m pip install -r requirements.txt

# Run individual test: kill leftover kernels first
powershell "Get-CimInstance Win32_Process -Filter ... | ... Stop-Process"
rm -f ../data/server/core_status.json*
rm -rf ../data/queue ../data/conversations
CC_MONITOR_IDLE_TIMEOUT=60 python -c "…"
```

⚠️ **Test hygiene**: always kill leftover `python.exe *kernel.py*` processes
before a test. If an old kernel is still alive and you `rm core_status.json`,
`check_core` spawns a second kernel → two kernels polling the same queue → race
conditions (`WinError 32` on file replace, `"connection not registered"`
failures). Check with:
```
powershell "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*kernel.py*' }).Count"
```

### Real-CC testing (not yet done)

1. Install the plugin, restart CC.
2. Start a second CC with the plugin.
3. Use `my_session_id` in each to get their session_ids.
4. `connect(cc_a_sid, cc_b_sid)` from one, verify the reply.
5. `send_message`, `arm_poller` + `Bash(poller, bg)` + `collect_messages` loop.

---

## Remaining work

1. **`skills/cc-monitor/SKILL.md`** — still a lower-layer placeholder
   ("inspect the raw log"). Must be rewritten to describe the cc-communicate
   MCP tools (the 14-tool interface above) so CC agents discover and use them.
   README §2.2 said to update this when the upper layer was added.

2. **Real-CC end-to-end testing** — install the plugin for real, start two CCs,
   exercise the full p2p loop (`my_session_id` → `connect` → `send_message` →
   `arm_poller` + poller + `collect_messages` → `close_connection` →
   `create_collaborator`). All testing so far is in-process/simulated.

3. **Linux support** — `spawn.py` and `proc.py` have stubs
   (`raise NotImplementedError`). The Windows branches are verified. Linux
   needs terminal-open commands (`gnome-terminal`/`xterm`) and `/proc`-based
   process introspection (or `psutil` which already handles `/proc`).

4. **Current CC's session not tracked** — the dev CC (Mocry's session) doesn't
   have the plugin installed, so `my_session_id` returns "no session recorded".
   Install + restart to make it discoverable.
