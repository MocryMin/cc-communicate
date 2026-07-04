# Session Handoff — cc-communicate v0.1.0

> **Read this first.** The previous session (2026-07-04, model dsv4) shipped
> v0.1.0 after completing the brainstorming→plan→implement cycle for tool
> disclosure, manual end-to-end testing, two critical bug fixes, and
> portability validation. Everything below is what you need to know to
> continue.

---

## 1. What is this project?

**cc-communicate** — a Claude Code plugin enabling p2p communication between
CC sessions on the same machine. Two layers:

| Layer | Language | Role |
|---|---|---|
| **Lower** | Node.js | Hook-triggered append-only event log (`data/session_ctrl/`) for SessionStart/End |
| **Upper** | Python | Kernel daemon (lazy-started) + 14 MCP tools (FastMCP, stdio transport) |

The kernel is **lazy-started** (one per machine): the first MCP tool call
triggers `ensure_core()` which spawns `kernel.py` as a detached daemon. The
kernel replays session events, dispatches queue-RPC requests, and manages
p2p conversation pipes. All 14 tools go through `rpc_client.call()` →
queue file → kernel dispatches to `kernel_api.py` → response file.

**Repository**: `MocryMin/cc-communicate` on GitHub (tagged `v0.1.0`).
Local root: `c:\研究生\实习\learn AI\projects\hello cc`.

Key design documents:
- `core_plan.md` — the original technical spec (written by user before build)
- `ToCollaboratorCC.md` — handoff doc from kernel-layer agent (build summary)
- `docs/superpowers/specs/2026-07-04-cc-communicate-v0.1-ship-design.md` — brainstorming spec
- `docs/superpowers/plans/2026-07-04-cc-communicate-v0.1-ship.md` — implementation plan

## 2. What's installed where

```
hello cc/                                ← project root
├── core_plan.md                         ← original design spec
├── ToCollaboratorCC.md                  ← kernel-layer handoff doc (renamed from README)
├── SESSION_HANDOFF.md                   ← THIS FILE
├── .gitignore                           ← workspace artifacts
├── docs/superpowers/
│   ├── specs/2026-07-04-...-design.md   ← brainstorming output
│   └── plans/2026-07-04-...-ship.md     ← implementation plan (6 tasks + 2 checkpoints)
├── .superpowers/sdd/                    ← SDD progress ledger + task briefs
│   ├── progress.md                      ← task completion log
│   ├── task-N-brief.md                  ← extracted task briefs
│   └── task-N-report.md                 ← implementer reports
└── cc-communicate-marketplace/          ← THE PLUGIN (marketplace root)
    ├── TEST_CHECKLIST.md                ← manual test checklist (Part A + Part B)
    ├── .claude-plugin/marketplace.json  ← marketplace manifest (v0.1.0)
    └── cc-communicate/                  ← plugin source
        ├── .mcp.json                    ← MCP server declaration (FIXED: no env field)
        ├── hooks/hooks.json             ← SessionStart/End → registrar.js
        ├── scripts/                     ← LOWER LAYER (Node, frozen)
        │   ├── registrar.js             ← event producer
        │   └── lib/paths.js             ← path resolution (FIXED: ${ guard)
        ├── skills/cc-communicate/
        │   └── SKILL.md                 ← agent-facing skill (REWRITTEN: black-box, 14 tools)
        ├── server/                      ← UPPER LAYER (Python)
        │   ├── mcp_server.py            ← FastMCP thin shell, 14 @mcp.tool() (FIXED: 4 docstrings)
        │   ├── kernel.py                ← daemon (lazy-started, backoff loop, event replay)
        │   ├── kernel_api.py            ← kernel functions (query_session, check_alive, send_message, etc.)
        │   ├── check_core.py            ← ensure_core() — lazy-start + single-instance enforcement
        │   ├── rpc_client.py            ← tool-side RPC: write queue, poll response, timeout+retry
        │   ├── user_functions.py        ← orchestration: connect, close_connection, create_collaborator, my_session_id
        │   ├── spawn.py                 ← CC spawning (FIXED: --cwd → start /D)
        │   ├── paths.py                 ← path resolution (FIXED: ${ guard)
        │   ├── proc.py                  ← psutil-based process introspection
        │   ├── conversations.py         ← conversation folder/pipe helpers
        │   ├── listen_poller.py         ← background poller for keep_listen
        │   └── requirements.txt         ← psutil, filelock, mcp
        └── data/                        ← RUNTIME (gitignored by plugin's .gitignore)
            ├── session_ctrl/            ← append-only event log (lower layer writes)
            ├── server/                  ← kernel products (core_status.json, sessions.json, kernel.log)
            ├── queue/                   ← RPC request/response files
            └── conversations/           ← p2p message pipes
```

## 3. What was accomplished (v0.1.0)

### Phase 1 — Tool disclosure
- ✅ Rewrote `skills/cc-communicate/SKILL.md` — black-box workflow + 14-tool reference + caveats. No kernel internals.
- ✅ Fixed 4 stale docstrings in `mcp_server.py`: `evoke` (same-sid via `--resume`), `arm_poller` (`baseline` not `watching`), `connect` (failure prefix), `close_connection` (notification format + return shape).

### Phase 2 — Manual end-to-end testing
- ✅ Created `TEST_CHECKLIST.md` — 8 functional-test phases (0-7) + portability test (Part B).
- ✅ Manual p2p test: `my_session_id` → `register_conversation` → `send_message`(hello) → `arm_poller` + `Bash(bg=true)` + `collect_messages`(reply) → `close_connection`. **Bidirectional messaging confirmed.**
- ✅ Edge cases: `query_session(nonexistent)`→null, `check_alive(dead)`→0, `send_message after close`→"connection not registered", `withdraw(init_connect=1)`→"conversation withdrawn".

### Bug fixes (found during testing)
- 🐛 **`CLAUDE_PLUGIN_ROOT` literal** — `.mcp.json`'s `env` field passed `${CLAUDE_PLUGIN_ROOT}` literally (CC doesn't substitute `${...}` in `env` values). Fix: removed `env` field + added `${` guard in `paths.py`/`paths.js`. Commit `17c5e4e`.
- 🐛 **`claude --cwd` not a valid flag** — `spawn.py:spawn_cc_new()` used a nonexistent CLI option. Fix: `start /D <cwd>` instead. Commit `4d63b11`.
- 🐛 **Phase 0 kill command silent no-op** — `Get-CimInstance | Stop-Process` doesn't bind `ProcessId`. Fix: `ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`. Commit `48f09cf`.
- 🐛 **Wrong pip install path** — docs said `cc-communicate/server/requirements.txt`, correct is `cc-communicate-marketplace/cc-communicate/server/requirements.txt`. Commit `9ce8c83`.

### Phase 3 — Portability validation
- ✅ Hidden dependency audit: `registrar.js` stdlib-only, `paths.py`/`paths.js` use `__file__` fallback, `.mcp.json` no hardcoded paths.
- ✅ Clean-venv install test: fresh venv → `pip install` deps → launch CC → `/mcp` sees plugin → `my_session_id` returns sid.

## 4. The 14 MCP tools — interface reference

CC exposes each as `mcp__plugin_cc-communicate_cc-communicate__<tool>`.
Call them by the short names below.

### Identity
| Tool | Sig | Returns | Notes |
|---|---|---|---|
| `my_session_id` | `() -> str` | sid or `"failed, ..."` | Walks process tree to `claude.exe`, looks up session by pid |
| `query_session` | `(session_id) -> dict\|null` | `{pid, cwd, start_time, ...}` | null if unknown |
| `check_alive` | `(session_id) -> int` | 1 or 0 | pid+start_time verified via psutil |
| `query_conversations` | `(session_id) -> list` | `[{partner: sid}]` | From conversations folder; includes ended |

### Messaging
| Tool | Sig | Key behavior |
|---|---|---|
| `send_message` | `(fromid, toid, message) -> str` | Fails `"failed, connection not registered"` if conv not active |
| `register_conversation` | `(sid_a, sid_b)` | Order-independent (sorted tuple). connect calls this |
| `unregister_conversation` | `(sid_a, sid_b)` | Removes from alive_conversations |
| `withdraw` | `(fromid, toid, init_connect=0) -> str` | `1`: remove folder+unregister; `0`: remove latest pipe msg |

### Spawning
| Tool | Sig | Notes |
|---|---|---|
| `evoke` | `(session_id) -> str` | `claude --resume <sid>`; SAME sid revived; connect auto-calls this |

### Listening
| Tool | Sig | Notes |
|---|---|---|
| `arm_poller` | `(session_id, timeout=1800) -> dict` | Returns `{armed, command, timeout, baseline}`. CC runs `command` via `Bash(bg=true)` |
| `collect_messages` | `(session_id) -> list` | `[{time, from_id, message}]` sorted; moves pipe→log |

### Orchestration
| Tool | Sig | Notes |
|---|---|---|
| `connect` | `(caller_sid, target_sid, hold_time=60) -> str` | Query→check_alive→evoke(if dead)+wait→register→send hello→arm+blocking poller→collect reply. Blocks up to `hold_time` |
| `close_connection` | `(session_id, toid) -> dict` | `{closed: True, delivered_pending: [...]}`; sends `[CONNECTION CLOSED by <sid>]`; unregisters |
| `create_collaborator` | `(caller_sid, cwd, hold_time=60) -> str` | Spawns new CC via `start /D`, polls 30s for register, then connect. ⚠️ See limitations |

## 5. Test results (per feature)

| Feature | Status | Notes |
|---|---|---|
| `my_session_id` | ✅ | Returns real sid; `resolve_claude` works from normal CC |
| `query_session` | ✅ | Returns dict for known sid, null for unknown |
| `check_alive` | ✅ | 1 for alive, 0 for dead; drops stale records ⚠️  returns 0 for evoke-resumed CCs (process tree mismatch) |
| `query_conversations` | ✅ | Lists partners from folder |
| `send_message` | ✅ | `"message_sent at <ts>"`; blocks after unregister |
| `register_conversation` | ✅ | `"ok"`; order-independent |
| `unregister_conversation` | ✅ | Removes from alive state |
| `withdraw` | ✅ | `init_connect=1`: folder removed; `=0`: latest msg removed |
| `evoke` | ✅ | `"evoke spawned (resumed)"`; connect auto-calls |
| `arm_poller` | ✅ | Returns `{armed, command, timeout, baseline}` |
| `collect_messages` | ✅ | Returns `[{time, from_id, message}]` sorted |
| `connect` | ✅ | Full flow: evoke→register→hello→listen→reply confirmed |
| `close_connection` | ✅ | `{closed: True}`; peer gets `[CONNECTION CLOSED]`; send blocked after |
| `create_collaborator` | ✅ (with caveat) | `start /D` fix works; ⚠️ workspace trust prompt requires manual intervention |
| **Bidirectional p2p** | ✅ | Full send→wait→collect→reply cycle confirmed with real CC |
| **Portability** | ✅ | Clean-venv install + verify passed |

## 6. Key technical learnings

### Bug root causes & lessons
1. **CC does NOT `${...}`-substitute `env` values in `.mcp.json`** — only `command`/`args`. Don't put `${CLAUDE_PLUGIN_ROOT}` in `env`; rely on `__file__`-relative fallback.
2. **Env-var fallbacks must guard against unsubstituted `${...}` literals** — `env or fallback` is wrong (literal is truthy). Use `env and '${' not in env`.
3. **"Process alive but produces no output" → suspect path/env resolution** — the kernel was hung because `PLUGIN_ROOT` resolved to a nonsense path.
4. **Debug detached subprocesses with module-load diagnostics** — one-line log at top of file (before imports) writing `pid`/`exe`/`cwd`/`env`/`__file__` to a fixed absolute path. This is how the literal `${CLAUDE_PLUGIN_ROOT}` was caught.
5. **Never assume a CLI flag exists without testing** — `claude --cwd` was "verified" in docs but never actually run.
6. **PowerShell pipeline binding isn't automatic** — `Get-CimInstance | Stop-Process` silently fails because CIM objects' `ProcessId` doesn't bind to `-Id`. Use `ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`.

### Architecture insights
- The kernel is lazy-started (not a boot-time daemon). First tool call triggers `ensure_core()` → `_spawn_kernel()` → kernel writes `core_status.json` as READY signal. `_wait_for_ready()` polls for up to 15s.
- `_spawn_kernel()` sets `cwd=SERVER_DATA_DIR` and uses `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`. The kernel survives parent exit.
- Queue RPC: tool writes request to `data/queue/`, polls `data/queue/responses/` for kernel's reply. Timeout + one retry (core_plan #11c).
- Conversation folders are canonical (sorted sids joined by `__`). `connect(A,B)` and `connect(B,A)` resolve to the same folder.
- `paths.py` / `paths.js` share constants and must stay in sync. Both layers read the same `data/session_ctrl/`.

## 7. Known issues & limitations

| Issue | Severity | Detail |
|---|---|---|
| `create_collaborator` needs manual trust | Medium | New CC window shows workspace trust prompt; user must click "Yes". Cannot be fully automated. |
| `check_alive` fails on evoke-resumed CCs | Medium | Evoked CC's process tree differs; `resolve_claude` can't find `claude.exe` ancestor. `my_session_id` also fails in evoked CCs. The CC still works (can call kernel via queue RPC). Root cause: `cmd /c start ... claude --resume` creates a process hierarchy unlike a normal CC start. |
| `my_session_id` returns "no session recorded" for pre-install sessions | Low | SessionStart only fires for sessions starting while the plugin is active. Documented caveat. |
| Linux not supported | Low | `spawn.py`, `proc.py` have Windows-only branches with `NotImplementedError`. |
| No auto dependency install | Low | Python deps (`psutil`, `filelock`, `mcp`) must be manually `pip install`ed. Roadmap item for v0.2 (launch.py wrapper). |
| Dual-kernel race if hygiene not done | Low | If old kernels survive Phase 0 cleanup, new spawns conflict. Documented in README hygiene section. |
| `--resume` used for evoke but not fully verified | Low | README says "user-confirmed working" but end-to-end test with a manually-resumed session wasn't done. The connect flow's evoked CC IS documented to have `my_session_id` issues (see above). |

## 8. Key files to read first

If you're new and need context, read these in order:

1. **`SESSION_HANDOFF.md`** (this file) ← you are here
2. **`core_plan.md`** — the original technical design
3. **`ToCollaboratorCC.md`** — full architecture, tool table, Bug fix log
4. **`cc-communicate-marketplace/cc-communicate/skills/cc-communicate/SKILL.md`** — the agent-facing skill
5. **`cc-communicate-marketplace/TEST_CHECKLIST.md`** — everything tested and how
6. **`cc-communicate-marketplace/cc-communicate/server/mcp_server.py`** — all 14 tool definitions (FastMCP thin shell)
7. **`cc-communicate-marketplace/cc-communicate/.mcp.json`** — how CC starts the MCP server

## 9. How to set up & run

```bash
# Prerequisites: Windows, Python 3.x (pip), Node.js, Claude Code

# 1. Install Python deps (from project root)
pip install -r cc-communicate-marketplace/cc-communicate/server/requirements.txt

# 2. Add local marketplace (inside CC)
/plugin marketplace add "C:\研究生\实习\learn AI\projects\hello cc\cc-communicate-marketplace"

# 3. Install the plugin (inside CC)
/plugin install cc-communicate@cc-communicate-local

# 4. Fully restart CC (SessionStart only fires for new sessions)

# 5. Verify
/mcp                          # should show cc-communicate server
my_session_id()               # should return a UUID

# 6. Before any manual test, run hygiene:
# Kill leftover kernels:
powershell "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*kernel.py*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }"
# Clean runtime state:
rm -f cc-communicate/data/server/core_status.json*
rm -rf cc-communicate/data/queue cc-communicate/data/conversations
```

## 10. What's next (next phase)

The original plan (see `docs/superpowers/plans/2026-07-04-cc-communicate-v0.1-ship.md`) specified post-v0.1 roadmap items:

1. **Formal release packaging** — `launch.py` wrapper that auto-`pip install`s deps on first run (replaces manual prerequisite).
2. **Linux support** — implement `spawn.py` / `proc.py` for Linux (`gnome-terminal`/`xterm`, `/proc` introspection).
3. **Cross-machine testing** — validate portability on a truly different machine.
4. **Fix `check_alive` for evoke-resumed CCs** — the process tree mismatch is the root cause; `resolve_claude` needs to handle the `cmd /c start` intermediate layer.
5. **Fix `my_session_id` for evoked CCs** — same root cause as above.
6. **Document `create_collaborator` workspace-trust limitation** — and explore if there's a CC config option to pre-trust specific directories.

The user will tell you the specific next-phase task. This section is a summary of ALL remaining known work — pick whichever the user asks for.

---

*Last updated: 2026-07-04. Model: dsv4. Branch: main. Tag: v0.1.0.*
