# cc-communicate v0.1 — tool disclosure, manual testing, portability validation

**Date**: 2026-07-04
**Status**: Approved (brainstorming complete)
**Scope**: Three sequential phases to ship cc-communicate v0.1 — (1) rewrite SKILL.md + audit tool docstrings, (2) manual end-to-end testing on dev machine, (3) portability validation + v0.1.0 packaging.

## Background

cc-communicate is a Claude Code plugin for p2p communication between CC sessions on the same machine. Two layers are built and unit-tested on Windows:

- **Lower (Node)**: hook-triggered append-only event log (`scripts/registrar.js`)
- **Upper (Python)**: kernel daemon + 14 MCP tools (`server/`)

Three things remain (per root README "Remaining work" + this project's goals):

1. `skills/cc-communicate/SKILL.md` is still a lower-layer placeholder ("inspect the raw log") — must be rewritten to describe the 14 MCP tools so CC agents discover and use them.
2. Real two-CC end-to-end testing has never been done (all testing so far is in-process/simulated).
3. The install procedure is incomplete (missing `pip install` step) and has never been validated from a clean state.

## Decisions (from brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| SKILL.md style | Workflow-first quick start + full tool reference below | Agent needs both "how to do p2p" and "which tool does X" |
| SKILL.md scope | SKILL.md rewrite + docstring audit | `evoke` docstring is stale (says "fresh session_id", impl uses `--resume` same sid); SKILL.md and docstrings must be consistent |
| Architecture exposure | Black box — no kernel/event-log internals | SKILL.md reader is the CC agent, not a human developer; internals belong in README |
| Test method | Manual (user runs it) | User said "我自己手测"; deliverable is a checklist, not automation |
| Release nature | Portability validation, not user-facing | GitHub v0.1.0 tag synced, not promoted; docs are for self-verification |
| Python dep handling | v0.1: document `pip install` as prerequisite (A); formal: `launch.py` wrapper (C, roadmap) | A is transparent and validates dep declarations; C is future optimization |

Overall structure: **one spec, three sequential phases** (1 is prerequisite to 2, 2 to 3).

---

## Phase 1 — SKILL.md rewrite + docstring audit

### SKILL.md structure

```
---
name: cc-communicate
description: Discover and communicate with other Claude Code sessions on this machine — query sessions, check liveness, send/receive messages, p2p connect, spawn collaborators.
---

# cc-communicate

## Quick start (typical p2p flow)
1. my_session_id()          → get your own sid (call this FIRST)
2. query_conversations(sid) / query_session(target) → find a peer
3. connect(sid, target)      → establish p2p (blocks up to hold_time, default 60s)
4. send_message + arm_poller → Bash(run_in_background=true) → wait for <task-notification> → collect_messages → reply → re-arm
5. close_connection          → graceful teardown
6. create_collaborator(sid, cwd) → separate scenario: spawn a new CC and connect

## Tool reference (14 tools, grouped)
- Identity: my_session_id
- Read-only: query_session, check_alive, query_conversations
- Messaging: send_message, register_conversation, unregister_conversation, withdraw
- Spawning: evoke
- Listening: arm_poller, collect_messages
- Orchestration: connect, close_connection, create_collaborator
(each: signature + one-line description + key caveat)

## Caveats
- Fully restart CC after install (SessionStart only fires for sessions starting while plugin is active)
- Windows only (Linux is stub, not implemented)
- Call my_session_id FIRST to get your sid before connect etc.
- connect blocks (default 60s)
- arm_poller's returned command MUST be run via Bash(run_in_background=true)
```

### Content decisions

- **Tool naming**: short names (`connect`, etc.) with a note that CC exposes them as `mcp__plugin_cc-communicate_cc-communicate__<tool>`. Agent calls by short name.
- **Black box**: no kernel, event-log, queue RPC, PID-reuse logic. Caveats only at the level "agent needs to know to use tools correctly" (restart, blocking, bg run).
- **Workflow**: based on README's 5 steps + `create_collaborator` as a separate scenario. Do not invent new flows.

### Docstring audit

- **Must fix**: `mcp_server.py:74-79` `evoke` — change "The new CC gets a fresh session_id (discovered later via its SessionStart hook)" to reflect `claude --resume <sid>` keeping the same session_id (aligns with README deviation table + user_functions impl).
- **Audit all 14**: compare each docstring vs README tool table vs user_functions actual behavior; fix any staleness/contradiction. Known suspect: `arm_poller` return fields (docstring says `{armed, command, timeout, watching}`, README says `{armed, command, baseline}` — inconsistent, must reconcile against actual `kernel_api` return).
- **Consistency rule**: SKILL.md tool descriptions and docstrings must agree (both are sources the agent may see first).

### Out of scope (YAGNI)

- No architecture section (black box)
- No example output screenshots (text is enough)
- No changes to tool logic in mcp_server.py (only docstring text)

---

## Phase 2 — Manual test checklist

### Form

A checkable markdown file: `cc-communicate-marketplace/TEST_CHECKLIST.md`. Table structure:

| Phase | Step | Action | Expected | ☐ |
|---|---|---|---|---|

User executes in order, checks off; deviation from expected = bug.

### Test environment

Dev machine (deps already installed). **Hygiene cleanup before each round** — README warns: leftover `kernel.py` processes cause dual-kernel races (`WinError 32`, "connection not registered"). Cleanup commands embedded in checklist Phase 0:

```bash
# kill leftover kernels
powershell "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*kernel.py*' } | Stop-Process"
# clean runtime state
rm -f cc-communicate/data/server/core_status.json*
rm -rf cc-communicate/data/queue cc-communicate/data/conversations
```

(Inline commands, no separate script — per user preference. If repeated cleanup becomes tedious, a `scripts/clean_state` helper can be added later.)

### Test scope ("各项基本功能", organized by workflow)

| Phase | What | Tools covered |
|---|---|---|
| 0 | Hygiene cleanup | — |
| 1 | Install (marketplace add + install + **full restart**) | — |
| 2 | Install verify (`/mcp` sees server, `my_session_id` returns sid, `query_session` self) | my_session_id, query_session |
| 3 | Two-CC setup (open CC-A, CC-B, get each sid) | my_session_id |
| 4 | **Core p2p loop** (connect → send_message → arm_poller+Bash(bg) → wait task-notification → collect_messages → reply → re-arm) | connect, send_message, arm_poller, collect_messages |
| 5 | Teardown (close_connection → peer's collect_messages sees `[CONNECTION CLOSED]`) | close_connection |
| 6 | Spawn collaborator (create_collaborator → new CC auto-registers + connect) | create_collaborator |
| 7 | Edge cases (check_alive dead→0, query_session unknown→null, evoke revive, query_conversations list partners, withdraw, register/unregister side) | check_alive, query_conversations, evoke, withdraw, register/unregister_conversation |

Each step has a **concrete expected** (return string/field/phenomenon) so the user can judge pass/fail. E.g., Phase 4 connect expected = `"connect succeed; reply: ..."` not `"failed, ..."`.

### Out of scope (YAGNI)

- No automated test scripts (user tests manually)
- No Linux testing (stub)
- No performance/stress testing (v0.1 only needs functional correctness)

---

## Phase 3 — Portability validation + v0.1.0 packaging

### Key finding: current install procedure is incomplete

Root `README.md` Install section (L320-328) only has:

```
/plugin marketplace add "C:\..."
/plugin install cc-communicate@cc-communicate-local
fully restart CC
```

**Missing `pip install -r server/requirements.txt`** — the exact portability gap. On a fresh machine the MCP server crashes on `import mcp`. Phase 3's core = complete the procedure + verify it works from clean state.

### Portability test

**Part A — Hidden dependency audit** (read code, no execution):

- Confirm `scripts/registrar.js` is stdlib-only (no `package.json` exists → likely, but verify import list)
- Confirm `server/paths.py` uses `${CLAUDE_PLUGIN_ROOT}`, no hardcoded dev paths
- Confirm `.mcp.json` only assumes `python`/`node` on PATH

**Part B — Clean venv install test**:

1. Create fresh venv (no psutil/filelock/mcp): `python -m venv .venv-clean && .venv-clean\Scripts\activate`
2. Follow the completed install procedure:
   ```
   pip install -r cc-communicate/server/requirements.txt
   /plugin marketplace add "<local path>"
   /plugin install cc-communicate@cc-communicate-local
   fully restart CC (launched from the activated venv shell, so `python` on PATH = venv python)
   ```
3. Verify: `/mcp` shows server, `my_session_id()` returns sid, `query_session` self works
4. Pass → procedure is portable. Crash → caught a hidden dep/assumption, fix and re-test.

**Key detail**: `.mcp.json` says `python` (not an absolute path), so the `python` on PATH when CC starts must be the one with deps installed. The install doc must state: "install deps into the Python that CC will invoke."

### Install spec (complete root README's Install section)

```
## Prerequisites
- Windows (Linux is stub, not implemented)
- Python 3.x (with pip) — the one CC will invoke
- Node.js (for hooks)
- Claude Code (a version supporting plugin/.mcp.json/hooks)

## Install steps
1. pip install -r <plugin path>/cc-communicate/server/requirements.txt
2. /plugin marketplace add "<marketplace path>"
3. /plugin install cc-communicate@cc-communicate-local
4. Fully restart CC

## Verify
/mcp shows cc-communicate server
my_session_id() returns a sid
```

Location: update root `README.md`'s Install section directly (single source of truth; README is already the handoff doc). No separate INSTALL.md (YAGNI — not for external users). Portability test checklist goes into `TEST_CHECKLIST.md` as Part B.

### GitHub sync

- `git tag v0.1.0` (matches plugin.json + marketplace.json version)
- `git push origin v0.1.0`
- No external promotion

### Roadmap (documented in root README, not implemented in v0.1)

- **Choice C**: `launch.py` wrapper that auto-`pip install`s deps on first run (replaces manual prerequisite)
- **Linux support**: `spawn.py`/`proc.py` stubs → real implementation
- **True cross-machine testing**: current portability test is same-machine clean venv only; real cross-machine is next

### Deliverables

1. Completed install procedure (root README Install section)
2. Clean-venv portability test executed and passing (TEST_CHECKLIST.md Part B)
3. `v0.1.0` git tag + push
4. Roadmap section (added to root README)

### Out of scope (YAGNI)

- No `launch.py` (C is roadmap; v0.1 uses A)
- No public marketplace release
- No external-user-facing marketing docs
- No Linux implementation

---

## Execution order

1. **Phase 1** (SKILL.md + docstrings) — prerequisite to Phase 2 (checklist references SKILL.md workflows)
2. **Phase 2** (manual testing) — prerequisite to Phase 3 (portability test reuses the install+verify procedure validated functionally)
3. **Phase 3** (portability + packaging) — final, produces v0.1.0 tag
