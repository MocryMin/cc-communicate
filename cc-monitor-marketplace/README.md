# cc-monitor — lower-layer (hook / event producer) report

This document covers **one layer only**: the lower layer that CC hooks run to
record session activity. The upper layer (a kernel server that consumes the
events and serves a live session table) is **not yet implemented**; this
document describes the contract the lower layer commits to, so the upper layer
can be built against it, and so the two can be packed into one plugin later.

> **Status of this layer:** implemented and verified on Windows. The model is
> **append-only event log**: hooks never read, never lock, never mutate a shared
> table — they only append uniquely-named event files. The shared-table +
> `cc-status.js`/`store.js` design described in some earlier notes is
> **superseded and deleted**; this file is now the authoritative description.

---

## 1. Service provided to the upper layer

### What this layer does

For every Claude Code session lifecycle event the CC hooks fire on
(`SessionStart`, `SessionEnd`), this layer writes **exactly one event file**
into a well-known folder. It performs **no aggregation, no liveness checks, no
deletion, no locking**. It is a pure, dumb, durable event producer.

Two event kinds are produced:

| Hook | Event kind | Filename | Written payload |
|---|---|---|---|
| `SessionStart` | `start` | `start_<event_ts>_<session_id>.json` | `{ event:"start", event_ts, session_id, pid, cwd, start_time, source }` |
| `SessionEnd`   | `end`   | `end_<event_ts>_<session_id>.json`   | `{ event:"end", event_ts, session_id }` |

Field semantics (the upper layer must respect these):

- `event_ts` — wall-clock ms when the hook **fired**. Used by the server to
  **order** events during replay. Not a process creation time.
- `pid` — the real `claude` process id (resolved by walking the process tree,
  skipping transient hook shells). Used by the server for liveness.
- `start_time` — the `claude` process's **creation time** (ISO8601). Used by
  the server to defeat PID reuse (pid + start_time match ⇒ live). Distinct from
  `event_ts`.
- `cwd`, `source` — passed through from the CC hook input.

### Properties this layer guarantees to the upper layer

1. **Append-only.** Existing event files are never read or modified by this
   layer. The upper layer can treat the folder as a write-once log.
2. **Uniquely-named, collision-safe.** Filenames never collide: timestamp +
   session_id + exclusive `wx` create with `__N` retry. Concurrent hooks writing
   in the same millisecond produce distinct files; no event is silently lost.
3. **Monotonic-ish ordering.** Filenames are zero-padded so `ls` / lexical sort
   yields chronological order *within the resolution of `event_ts`* (ms). The
   server should still sort by `event_ts` for correctness, but the on-disk order
   is already a usable approximation.
4. **No contention surface.** Because this layer appends only, the upper layer
   never has to coordinate locks *with this layer*. The only coordination is
   "read the folder".
5. **Durable source of truth.** The folder is the ground truth; the upper
   layer's in-memory table is a derived view and can be rebuilt at any time.

### The interface / access method to this layer

There is **no live API** between the layers at runtime — the contract is a
**folder + file convention**, intentionally decoupling the two layers' lifetimes:

- **Upper layer reads** the folder `${CLAUDE_PLUGIN_ROOT}/data/session_ctrl/`
  directly (directory listing + per-file JSON parse). It may list the whole
  folder, or watch it for changes; either works because this layer never
  rewrites files.
- **Lower layer writes** to that same folder only. It does not know the upper
  layer exists.

This means the upper layer can be **ephemeral**: start, read the folder, replay
into memory, serve, exit. No long-running daemon is required, and no
handshake/IPC protocol is needed between the layers. (If the upper layer later
wants an active interface — e.g. a socket/CLI the agent calls — that lives
entirely *inside* the upper layer; this lower layer is unaffected.)

### What this layer explicitly does NOT do (upper layer's job)

- Replay events into an in-memory `session_status` table.
- Liveness / zombie / PID-reuse judgement (pid + `start_time` match).
- Pruning or compaction of the event log (the folder grows unbounded until the
  upper layer reaps it).
- Exposing any queryable interface to agents / the upper-layer caller.

---

## 2. Packing the upper + lower layers into one plugin

When the upper layer (kernel server) is finished, both ship as files inside the
same `cc-monitor` plugin directory. CC treats a plugin as one unit: hooks,
scripts, and skills are all discovered by their location, so co-locating is
sufficient — no cross-layer wiring is needed. Below is the checklist of **what
to touch on this layer** when packing.

### 2.1 Files this layer already owns (do not move/rename)

```
cc-monitor/
  hooks/hooks.json                 # declares SessionStart/End → registrar.js
  scripts/registrar.js             # the event producer (this layer's core)
  scripts/lib/paths.js             # path resolution (shared — see 2.3)
  scripts/lib/proc.js              # process introspection (shared — see 2.3)
  skills/cc-monitor/SKILL.md       # the agent-facing skill description
  .claude-plugin/plugin.json       # plugin manifest
  .gitignore                       # excludes data/
```

### 2.2 What to do with this layer's files when the upper layer is added

- **Keep `hooks/hooks.json` exactly as-is.** It already points at
  `registrar.js`; the upper layer does not add hooks (it is not hook-driven — it
  is request-driven). Do not let the upper layer register a conflicting
  SessionStart/SessionEnd hook.
- **Keep `registrar.js` as-is.** The upper layer must not modify how events are
  written (filename scheme, payload fields) — that is the contract from §1. If
  the schema ever needs to change, treat it as a versioned migration, not an
  in-place edit.
- **Update `skills/cc-monitor/SKILL.md`** to point agents at the **upper
  layer's** entry point (e.g. a `server.js` / CLI it exposes) instead of the
  current "inspect the raw log" placeholder text. The skill is the *only* file
  where the two layers meet visibly — it describes to the agent how to query,
  which is the upper layer's concern.
- **Add the upper layer's files alongside**, e.g. `scripts/server.js` (or a
  `server/` subfolder). No structural change to this layer is required for them
  to coexist.

### 2.3 Two shared libraries both layers should reuse

To avoid the two layers diverging on where things live, the upper layer **must
import the same modules** this layer uses, rather than re-deriving paths:

- **`scripts/lib/paths.js`** — single source of truth for
  `SESSION_CTRL_DIR`, `DATA_DIR`, `DEBUG_FILE`, `PLUGIN_ROOT`. The server must
  read `SESSION_CTRL_DIR` from here (resolving via `CLAUDE_PLUGIN_ROOT` with a
  `__dirname` fallback), so both layers always agree on the folder.
- **`scripts/lib/proc.js`** — exports `liveProcs()` (pid → start-time map) and
  `resolveClaude()`. The upper layer's liveness check should call `liveProcs()`
  from here so both layers use one cross-platform implementation (Windows
  PowerShell/CIM verified; Linux `/proc` and macOS `ps` written but untested).

> The deleted `scripts/lib/store.js` (lock + atomic table) and
> `scripts/cc-status.js` (old consumer) are **gone** — do not resurrect them.
> The new model has no shared mutable table and therefore no lock.

### 2.4 Runtime data directory

`cc-monitor/data/` is created at runtime and **must not** be shipped or
committed (`.gitignore` already excludes it). When the upper layer is added:

- The event log remains at `data/session_ctrl/` (this layer writes it; the upper
  layer reads/reaps it).
- If the upper layer needs its own runtime artifacts (e.g. a pidfile, a socket),
  place them under `data/` too, keeping `session_ctrl/` exclusively for events
  so this layer's reads-by-convention stay simple.
- On **uninstall**, CC removes the plugin cache dir including `data/`; since the
  log is a temporary, dynamic artifact, that is the intended cleanup. On
  **disable**, hooks stop firing (no new events), and any lingering event files
  describe sessions the server will judge ZOMBIE — no special teardown needed.

### 2.5 Versioning the contract

Bump `cc-monitor/.claude-plugin/plugin.json` `version` whenever the event
filename scheme or payload schema changes. The upper layer should read the
`event` field defensively and ignore unknown fields, so additive changes
(`source` gaining values, new optional fields) stay forward-compatible.

---

## 3. Lower-layer implementation details

### 3.1 File layout (current, lower layer only)

```
cc-monitor-marketplace/                         ← marketplace root (for local install)
├── .claude-plugin/marketplace.json             ← lists plugin "./cc-monitor"
└── cc-monitor/                                 ← the plugin
    ├── .claude-plugin/plugin.json              ← manifest
    ├── hooks/hooks.json                        ← SessionStart/End → registrar.js
    ├── scripts/
    │   ├── registrar.js                        ← event producer (hook entry)
    │   └── lib/
    │       ├── paths.js                        ← path resolution (shared)
    │       └── proc.js                         ← process introspection (shared)
    ├── skills/cc-monitor/SKILL.md              ← agent-facing skill
    ├── .gitignore                              ← excludes data/
    └── data/                                   ← runtime only (NOT shipped)
        ├── session_ctrl/                       ← append-only event log
        │   ├── start_<ts>_<sid>.json
        │   └── end_<ts>_<sid>.json
        └── debug.log                           ← per-hook trace
```

### 3.2 Hook wiring — `hooks/hooks.json`

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command",
      "command": "node \"${CLAUDE_PLUGIN_ROOT}/scripts/registrar.js\" start",
      "timeout": 30 }] }],
    "SessionEnd":   [{ "hooks": [{ "type": "command",
      "command": "node \"${CLAUDE_PLUGIN_ROOT}/scripts/registrar.js\" end",
      "timeout": 30 }] }]
  }
}
```

CC expands `${CLAUDE_PLUGIN_ROOT}` to the plugin's install path (verified — the
installed `superpowers` plugin uses the same variable). The hook payload
(`session_id`, `cwd`, `source`) arrives as JSON on the registrar's **stdin**.

### 3.3 `registrar.js` — the producer

Modes: `start` (SessionStart), `end` (SessionEnd), `diag` (no write; prints the
resolved claude PID + ancestor chain for debugging).

Flow on `start`:
1. Read hook JSON from stdin; bail (with a debug log line) if no `session_id`.
2. `resolveClaude(process.pid)` → walk the process tree to find the real
   `claude` process (see 3.5), yielding `{ pid, start_time, chain }`.
3. `appendEvent('start', payload)` → write one JSON file (see 3.4).

Flow on `end`: skip process resolution (the claude process is exiting; its pid
is stale anyway) and just `appendEvent('end', { event_ts, session_id })`.

`diag` mode is the only one that prints to stdout; `start`/`end` are silent
(hooks shouldn't pollute the session) and log to `data/debug.log`.

### 3.4 `appendEvent(type, payload)` — collision-safe append

```js
const ts  = String(Date.now()).padStart(13, '0');      // fixed-width ⇒ lex-sortable
const sid = (payload.session_id || 'unknown').replace(/[^A-Za-z0-9_-]/g, '_');
const base = `${type}_${ts}_${sid}`;
for (let i = 0; i < 1000; i++) {
  const name = i === 0 ? `${base}.json` : `${base}__${i}.json`;
  const fd = fs.openSync(path.join(SESSION_CTRL_DIR, name), 'wx'); // atomic+exclusive
  fs.writeSync(fd, JSON.stringify(payload, null, 2)); fs.closeSync(fd); return;
  // EEXIST ⇒ i++ and retry with __N suffix
}
```

Design points:
- **`wx` flag** = open-exclusive-create: the open is atomic and fails with
  `EEXIST` if the name exists. This is the concurrency primitive — two hooks
  racing for the same name cannot both win; the loser retries with a suffix.
  **No lock file is needed** because there is no shared mutable file to
  protect; each writer creates its own unique file.
- **Filename uniqueness** = `timestamp + session_id`. Timestamp alone collides
  (two sessions starting in the same ms); adding `session_id` covers distinct
  sessions, and the `__N` retry covers the same-session-same-ms edge (e.g. a
  hook firing twice under unusual conditions). Zero-padding the timestamp makes
  lexical directory listing already chronological.
- **payload written as pretty JSON** for human-inspectability; the upper layer
  parses it regardless of formatting.

### 3.5 `proc.js` — resolving the real claude PID

The hook process is a *child* of `claude`, often via intermediate shells
(`cmd.exe`, `bash`, Git Bash snapshot wrappers). Naively using `$PPID` would
record a transient shell, not the session. `resolveClaude(selfPid)`:

1. Builds a process table (`pid → { ppid, cmd, start }`) — platform-specific
   (see 3.6).
2. Walks up from `selfPid`, skipping self, until it finds an ancestor whose
   command line matches `/claude/i` but excludes `cc-monitor` / `registrar.js`
   / `cc-status.js` (so it doesn't match its own scripts).
3. Returns that ancestor's `{ pid, start (creation time), chain (for debug) }`.
4. Fallback: if no claude ancestor is found, returns the immediate parent
   (degraded but non-fatal — the event is still written; the upper layer's
   liveness check will then likely flag it ZOMBIE, which is the honest outcome).

`start_time` here is the **process creation time** (CIM `CreationDate` on
Windows). This is what makes liveness robust: a pid can be reused by a
different process later, but `pid + creation_time_match` cannot be spoofed.

`liveProcs()` returns the full `pid → start` map; the upper layer will use it
for liveness checks (the lower layer itself does not call it — it only needs
the single claude ancestor's start time).

### 3.6 Platform support in `proc.js`

| Platform | Mechanism | Status |
|---|---|---|
| Windows | `Get-CimInstance Win32_Process` via PowerShell | **Verified working** (resolved real `claude.exe -r`) |
| Linux | `/proc/<pid>/stat` + `/proc/<pid>/cmdline` + `/proc/stat` btime | Written, **untested** |
| macOS | `ps -eo pid,ppid,etime,command` | Best-effort, **untested** |

### 3.7 `paths.js` — self-locating path resolution

```js
const PLUGIN_ROOT = process.env.CLAUDE_PLUGIN_ROOT || path.join(__dirname, '..', '..');
```

`CLAUDE_PLUGIN_ROOT` is set when CC runs a hook. The `__dirname` fallback means
the same scripts also run correctly when invoked *without* that env var (e.g. a
server process the upper layer spawns, or manual `node registrar.js diag`
during development). This is why the upper layer should reuse this module
rather than re-resolving paths.

### 3.8 Invariants the upper layer can rely on

- The folder `data/session_ctrl/` is the only output channel; nothing else is
  written by this layer except `data/debug.log`.
- Every file in that folder is a complete JSON event written atomically
  (`open wx` + `write` + `close`); there are no partially-written files visible
  to a reader on the same filesystem (the open is exclusive; close makes it
  visible).
- Event files are **immutable** after creation — the upper layer may cache or
  index them by filename without worrying about changes.
- `event_ts` inside the payload is authoritative for ordering; the filename
  timestamp is the same value, padded, and is a convenience for sorting.

---

## 4. Verification performed (Windows)

- ✅ `start` writes `start_<ts>_<sid>.json` with real `claude.exe` pid + its
  creation time; `end` writes `end_<ts>_<sid>.json`. No `sessions.json` is
  created (old model fully removed).
- ✅ Multiple events for distinct sessions produce distinct, lex-sorted files.
- ✅ **Collision retry**: forcing two `start`s for the same `session_id` at the
  same frozen `Date.now()` produced `…_sess-CCC.json` and `…_sess-CCC__1.json`,
  both with correct, non-overwritten payloads — no event lost.
- ✅ `registrar.js diag` walks node → bash → cmd → `claude.exe` and reports the
  real session pid.

---

## 5. Open / deferred items (honest)

- **Upper layer (kernel server) not built** — replay, in-memory table,
  liveness, log compaction, and the agent-facing query interface are all
  future work. This layer's contract (§1) is what they will be built against.
- **`/plugin install` not run end-to-end** — install commands are correct by
  construction (schema mirrors the installed `superpowers` plugin), but do a
  real install + restart to confirm.
- **`${CLAUDE_PLUGIN_ROOT}` substitution in skill content** (vs in hook
  commands) is confirmed for hooks but should be re-checked once the upper
  layer's SKILL.md entry point is written.
- **Linux/macOS process code** in `proc.js` is untested (Windows branch
  verified).
- **Log growth**: `session_ctrl/` is append-only and never compacted by this
  layer; reaping is explicitly the upper layer's responsibility.

---

## Appendix: install commands (unchanged)

```
/plugin marketplace add "C:\研究生\实习\learn AI\projects\hello cc\cc-monitor-marketplace"
/plugin install cc-monitor@cc-monitor-local
```
Then fully restart cc (SessionStart only fires for sessions starting while the
plugin is active).
