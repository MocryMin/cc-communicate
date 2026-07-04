# cc-communicate v0.1 Ship Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship cc-communicate v0.1.0 — rewrite SKILL.md + fix stale docstrings, produce a manual test checklist, validate portability in a clean venv, complete the install spec, and tag v0.1.0.

**Architecture:** Three sequential phases. Phase 1 (docs): fix 4 stale tool docstrings in `mcp_server.py` and rewrite `SKILL.md` as a black-box workflow+reference. Phase 2 (manual test): write `TEST_CHECKLIST.md` Part A, then the USER executes it on the dev machine. Phase 3 (portability + packaging): update root README install spec + Roadmap, append TEST_CHECKLIST Part B, USER executes the clean-venv portability test, then tag v0.1.0. Two user-execution checkpoints (functional test, portability test) gate the packaging.

**Tech Stack:** Python 3 (FastMCP server, psutil/filelock/mcp deps), Node.js (hooks, stdlib only), Claude Code plugin system (`.mcp.json`, `hooks.json`, `plugin.json`, `${CLAUDE_PLUGIN_ROOT}`).

## Global Constraints

- **Platform:** Windows only (Linux is a stub — `spawn.py`/`proc.py` raise `NotImplementedError`).
- **Python deps:** `psutil>=5.9`, `filelock>=3.12`, `mcp>=1.28` (from `server/requirements.txt`). v0.1 installs them via manual `pip install` prerequisite; `launch.py` auto-install is Roadmap only.
- **Node deps:** none (hooks use stdlib + local `./lib/*`; no `package.json`).
- **Version:** `0.1.0` (matches `plugin.json` + `marketplace.json`); git tag `v0.1.0`.
- **SKILL.md style:** black-box — no kernel/event-log/queue-RPC internals. Workflow quick-start + 14-tool reference + caveats.
- **Tool naming in SKILL.md:** short names (`connect`, etc.) with a one-line note that CC exposes them as `mcp__plugin_cc-communicate_cc-communicate__<tool>`.
- **Consistency rule:** SKILL.md tool descriptions and `mcp_server.py` docstrings must agree (both are sources the agent may see first).

---

### Task 1: Fix 4 stale tool docstrings in mcp_server.py

**Files:**
- Modify: `cc-communicate-marketplace/cc-communicate/server/mcp_server.py` (docstrings for `evoke`, `arm_poller`, `connect`, `close_connection`)

**Interfaces:**
- Consumes: actual behavior verified in `kernel_api.py` + `user_functions.py` (audit already done — see below)
- Produces: docstrings accurate; no behavior change (only docstring text)

**Audit findings (verified against implementation):**
- `evoke`: docstring says "fresh session_id" — actual uses `claude --resume` (SAME session_id); also says "or has no cwd" — `--resume` needs no cwd.
- `arm_poller`: docstring says return field `watching` — actual returns `baseline`.
- `connect`: docstring says failure returns `'failed, ...'` — actual also returns `'connect failed, ...'` for two cases.
- `close_connection`: docstring says `'[CONNECTION CLOSED]'` — actual sends `'[CONNECTION CLOSED by <sid>]'`.
- Other 10 tools: verified correct, no change.

- [ ] **Step 1: Fix `evoke` docstring (mcp_server.py:74-79)**

Edit — old:
```
    """Spawn a new Claude Code session in the given session's working directory
    (Windows). Use to revive a dead peer: the spawned CC loads the plugin and
    waits for messages. The new CC gets a fresh session_id (discovered later via
    its SessionStart hook). Fails if the session is unknown or has no cwd."""
```
new:
```
    """Revive a dead CC session via `claude --resume <session_id>` (Windows).
    The SAME session_id is resumed (not a fresh one), so connect can talk to
    target_sid directly afterward. The revived CC fires SessionStart -> the
    kernel updates alive_sessions with the new pid; poll check_alive until
    alive. Returns 'evoke spawned (resumed)' or 'failed, session unknown'."""
```

- [ ] **Step 2: Fix `arm_poller` docstring (mcp_server.py:83-87)**

Edit — replace `Returns {armed, command, timeout, watching}.` with `Returns {armed, command, timeout, baseline}.` (only the field name changes; rest of docstring stays).

- [ ] **Step 3: Fix `connect` docstring (mcp_server.py:101-107)**

Edit — old:
```
    'connect succeed; reply: ...' on success, or 'failed, ...' on failure
    (unknown target, could not revive, no reply, timeout)."""
```
new:
```
    'connect succeed; reply: ...' on success, or a 'failed, ...' /
    'connect failed, ...' string on failure (unknown target, could not revive,
    no reply, timeout)."""
```

- [ ] **Step 4: Fix `close_connection` docstring (mcp_server.py:118-124)**

Edit — old:
```
    """Close the connection from session_id to toid. Drains pending messages
    addressed to session_id (returns them as delivered_pending), notifies the
    peer with a '[CONNECTION CLOSED]' message, and unregisters. The peer
    learns of the close via its next collect_messages."""
```
new:
```
    """Close the connection from session_id to toid. Drains pending messages
    addressed to session_id (returns them as delivered_pending), notifies the
    peer with a '[CONNECTION CLOSED by <session_id>]' message, and
    unregisters. The peer learns of the close via its next collect_messages.
    Returns {closed: True, delivered_pending: [...]}."""
```

- [ ] **Step 5: Verify no stale terms remain**

Run: `grep -n "fresh session_id\|timeout, watching\|or has no cwd" cc-communicate-marketplace/cc-communicate/server/mcp_server.py`
Expected: no output (all stale phrases gone).

- [ ] **Step 6: Commit**

```bash
git add cc-communicate-marketplace/cc-communicate/server/mcp_server.py
git commit -m "fix(server): correct stale docstrings — evoke/arm_poller/connect/close_connection

- evoke: fresh session_id -> same sid via claude --resume; drop 'no cwd'
- arm_poller: return field 'watching' -> 'baseline'
- connect: note 'connect failed, ...' failure prefix too
- close_connection: notification is '[CONNECTION CLOSED by <sid>]'; add return shape"
```

---

### Task 2: Rewrite SKILL.md (black-box, workflow + reference)

**Files:**
- Modify: `cc-communicate-marketplace/cc-communicate/skills/cc-communicate/SKILL.md` (full rewrite — replace lower-layer placeholder)

**Interfaces:**
- Consumes: the 14-tool interface (README tool table + verified docstrings from Task 1)
- Produces: agent-facing skill doc; referenced by TEST_CHECKLIST workflows (Task 3)

- [ ] **Step 1: Replace SKILL.md with the full black-box content**

Write `cc-communicate-marketplace/cc-communicate/skills/cc-communicate/SKILL.md` with exactly:

```markdown
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
```

- [ ] **Step 2: Verify black-box (no internals leaked)**

Run: `grep -niE "kernel|event.log|append.only|queue|alive_sessions|pid reuse|filelock|core_status" cc-communicate-marketplace/cc-communicate/skills/cc-communicate/SKILL.md`
Expected: no output (no internal-implementation terms).

- [ ] **Step 3: Verify all 14 tools listed**

Run: `grep -cE '^- \`' cc-communicate-marketplace/cc-communicate/skills/cc-communicate/SKILL.md`
Expected: `14`

- [ ] **Step 4: Commit**

```bash
git add cc-communicate-marketplace/cc-communicate/skills/cc-communicate/SKILL.md
git commit -m "docs(skill): rewrite SKILL.md — black-box 14-tool reference + quick-start

Replaces the lower-layer 'inspect the raw log' placeholder. Workflow-first
quick start, grouped tool reference, caveats. No kernel/event-log internals
(agent-facing, not developer-facing)."
```

---

### Task 3: Write TEST_CHECKLIST.md Part A (functional tests)

**Files:**
- Create: `cc-communicate-marketplace/TEST_CHECKLIST.md`

**Interfaces:**
- Consumes: SKILL.md workflows (Task 2) — checklist steps mirror them
- Produces: a checkable manual-test doc; Part A executed at Checkpoint 1, Part B appended at Task 5

- [ ] **Step 1: Create TEST_CHECKLIST.md with Part A (Phases 0-7)**

Write `cc-communicate-marketplace/TEST_CHECKLIST.md` with exactly:

```markdown
# cc-communicate — manual test checklist

Run this yourself (manual testing). Check each box; if a result deviates from
Expected, that's a bug — stop and report it before continuing.

## Part A — Functional tests (dev machine)

Run on the dev machine (Python deps already installed). Repeat Phase 0 hygiene
cleanup before every full run.

### Phase 0 — Hygiene cleanup

- [ ] Kill leftover kernels:
      `powershell "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*kernel.py*' } | Stop-Process"`
      Expected: no error (or "no process found").
- [ ] Clean runtime state:
      `rm -f cc-communicate/data/server/core_status.json*; rm -rf cc-communicate/data/queue cc-communicate/data/conversations`
      Expected: removed, no error.

### Phase 1 — Install

- [ ] `/plugin marketplace add "<absolute path to cc-communicate-marketplace>"`
      Expected: marketplace added confirmation.
- [ ] `/plugin install cc-communicate@cc-communicate-local`
      Expected: install success.
- [ ] Fully restart CC (close and reopen the session).
      Expected: SessionStart hook fires on the new session.

### Phase 2 — Install verify

- [ ] `/mcp` — Expected: cc-communicate server listed.
- [ ] `my_session_id()` — Expected: a UUID string (not `"failed, ..."`).
- [ ] `query_session(<your sid>)` — Expected: a dict with pid, cwd, started_at, etc.

### Phase 3 — Two-CC setup

- [ ] Open a second CC (CC-B) in another terminal; restart it after install so
      the plugin is active.
      Expected: two CCs running, both with the plugin.
- [ ] `my_session_id()` in CC-A — record as sid_A. Expected: UUID.
- [ ] `my_session_id()` in CC-B — record as sid_B. Expected: UUID, differs from sid_A.

### Phase 4 — Core p2p loop

- [ ] In CC-A: `connect(sid_A, sid_B)` — Expected: `"connect succeed; reply: ..."`.
      If `"failed, ..."` or `"connect failed, ..."`, stop — bug.
- [ ] In CC-A: `send_message(sid_A, sid_B, "hello from A")` — Expected: `"message_sent at <ts>"`.
- [ ] In CC-B: `arm_poller(sid_B, timeout=600)` — record the `command`.
      Expected: `{armed: True, command: "python .../listen_poller.py <sid_B>", timeout: 600, baseline: <n>}`.
- [ ] In CC-B: run the command via `Bash(run_in_background: true)`.
      Expected: a background task starts; CC-B is free.
- [ ] In CC-A: `send_message(sid_A, sid_B, "second message")`.
- [ ] In CC-B: wait for `<task-notification>` (poller exited 0).
      Expected: notification within a few seconds.
- [ ] In CC-B: `collect_messages(sid_B)` — Expected: `[{time, from_id: sid_A, message: "hello from A"}, {time, from_id: sid_A, message: "second message"}]` (sorted by time).
- [ ] In CC-B: reply — `send_message(sid_B, sid_A, "hi from B")` — Expected: `"message_sent at <ts>"`.
- [ ] In CC-A: `arm_poller(sid_A, timeout=600)` + `Bash(command, bg=true)`, wait
      for task-notification, `collect_messages(sid_A)` — Expected: sees `"hi from B"`.

### Phase 5 — Teardown

- [ ] In CC-A: `close_connection(sid_A, sid_B)` — Expected: `{closed: True, delivered_pending: [...]}`.
- [ ] In CC-B: `arm_poller(sid_B)` + `Bash(bg)`, wait, `collect_messages(sid_B)` —
      Expected: a message with `message: "[CONNECTION CLOSED by <sid_A>]"`.

### Phase 6 — Spawn collaborator

- [ ] In CC-A: `create_collaborator(sid_A, "C:/tmp/collab-test", hold_time=60)` —
      Expected: a new CC window opens in that cwd; within ~30s returns `"connect succeed; reply: ..."`.
      If `"failed, new session did not register within 30s (...)"`, the new CC didn't load the plugin (check user-level install).
- [ ] `query_conversations(sid_A)` — Expected: lists both sid_B and the new collaborator's sid.

### Phase 7 — Edge cases

- [ ] `check_alive(sid_B)` while CC-B is alive — Expected: `1`.
- [ ] Close CC-B, then `check_alive(sid_B)` — Expected: `0` (record dropped).
- [ ] `query_session("00000000-0000-0000-0000-000000000000")` — Expected: `null`.
- [ ] `evoke(<a dead sid>)` — Expected: `"evoke spawned (resumed)"`; then `check_alive(<that sid>)` after a few seconds — Expected: `1`.
- [ ] `query_conversations(sid_A)` — Expected: `[{partner: ...}, ...]`.
- [ ] `withdraw(sid_A, sid_B, init_connect=1)` — Expected: `"conversation withdrawn"`; the conversation folder is removed.

---

<!-- Part B (portability test) is appended by Task 5 after the root README install spec is updated. -->
```

- [ ] **Step 2: Verify the file exists and has all 8 phases**

Run: `grep -c "^### Phase" cc-communicate-marketplace/TEST_CHECKLIST.md`
Expected: `8`

- [ ] **Step 3: Commit**

```bash
git add cc-communicate-marketplace/TEST_CHECKLIST.md
git commit -m "docs(test): add TEST_CHECKLIST.md Part A — manual functional tests (phases 0-7)"
```

---

**USER CHECKPOINT 1 — Execute Part A functional tests**

The executor (subagent) stops here. The USER manually executes `cc-communicate-marketplace/TEST_CHECKLIST.md` Part A (Phases 0-7) on the dev machine, following each step's Expected result.

- If all phases pass: proceed to Task 4.
- If any step deviates from Expected: that's a bug. Report it. Fixes may touch `mcp_server.py` docstrings (Task 1), `SKILL.md` (Task 2), or the checklist itself — re-run the relevant task, then re-execute the failing phase. Do NOT proceed to Task 4 until Part A passes.

This checkpoint is a user action (real CC + plugin + two terminals); a subagent cannot execute it.

---

### Task 4: Update root README — install spec + Roadmap

**Files:**
- Modify: `README.md` (root) — replace Install section (add prerequisites + `pip install`); replace "Remaining work" section with "Roadmap (post-v0.1)"

**Interfaces:**
- Consumes: hidden-dep audit (registrar.js stdlib-only, paths.py portable — all clear)
- Produces: a complete, portable install procedure; Roadmap that TEST_CHECKLIST Part B (Task 5) references

- [ ] **Step 1: Replace the Install section (README "## Install & test" → "### Install")**

Edit — old:
```
### Install

```
/plugin marketplace add "C:\研究生\实习\learn AI\projects\hello cc\cc-communicate-marketplace"
/plugin install cc-communicate@cc-communicate-local
```

Then **fully restart CC** (SessionStart only fires for sessions starting while the plugin is active).
```
new:
```
### Prerequisites

- **Windows** (Linux is a stub, not implemented — see Roadmap)
- **Python 3.x** with pip — the same `python` CC will invoke (must be on PATH
  when CC starts)
- **Node.js** — for the SessionStart/End hooks
- **Claude Code** — a version supporting plugins, `.mcp.json`, and hooks

### Install

```bash
# 1. Install Python deps (required — the MCP server imports mcp/psutil/filelock)
pip install -r cc-communicate/server/requirements.txt

# 2. Add the local marketplace and install the plugin (inside CC)
/plugin marketplace add "<absolute path to cc-communicate-marketplace>"
/plugin install cc-communicate@cc-communicate-local
```

Then **fully restart CC** (SessionStart only fires for sessions starting while
the plugin is active). If you installed deps into a venv, launch CC from that
venv's activated shell so `python` on PATH is the one with deps.
```

- [ ] **Step 2: Replace the "Remaining work" section with "Roadmap (post-v0.1)"**

Edit — old (the entire `## Remaining work` section, items 1-4):
```
## Remaining work

1. **`skills/cc-communicate/SKILL.md`** — still a lower-layer placeholder
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
```
new:
```
## Roadmap (post-v0.1)

v0.1.0 ships: SKILL.md (14-tool reference), manual end-to-end testing
(`TEST_CHECKLIST.md`), clean-venv portability validation, and the completed
install procedure above. Remaining work:

- **Auto dependency install** — a `server/launch.py` wrapper that `pip install`s
  `requirements.txt` on first run, replacing the manual prerequisite. v0.1 uses
  the manual `pip install` step (transparent, validates dep declarations); this
  wrapper is the formal-release upgrade.
- **Linux support** — `server/spawn.py` and `server/proc.py` have Windows-only
  branches with `NotImplementedError` stubs. Linux needs terminal-open commands
  (`gnome-terminal`/`xterm`) and `/proc`-based process introspection.
- **Cross-machine testing** — v0.1 portability is validated in a clean venv on
  the same machine. Real cross-machine install/run is the next milestone.
```

- [ ] **Step 3: Verify install section now mentions pip install**

Run: `grep -n "pip install -r" README.md`
Expected: at least one match in the Install section.

- [ ] **Step 4: Verify "Remaining work" is gone, "Roadmap" present**

Run: `grep -c "## Remaining work" README.md`
Expected: `0`
Run: `grep -c "## Roadmap (post-v0.1)" README.md`
Expected: `1`

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): complete install spec (prerequisites + pip install) + Roadmap

- Install: add Prerequisites (Win/Python/Node/CC) and the pip install step
  (was missing — the portability gap v0.1 closes)
- Replace 'Remaining work' with 'Roadmap (post-v0.1)': auto-dep-install
  (launch.py), Linux, cross-machine testing"
```

---

### Task 5: Append TEST_CHECKLIST.md Part B (portability test)

**Files:**
- Modify: `cc-communicate-marketplace/TEST_CHECKLIST.md` — replace the trailing placeholder comment with Part B

**Interfaces:**
- Consumes: the install procedure from Task 4 (Part B follows it step-by-step)
- Produces: a complete checklist; Part B executed at Checkpoint 2

- [ ] **Step 1: Replace the trailing placeholder comment with Part B**

Edit — old:
```
<!-- Part B (portability test) is appended by Task 5 after the root README install spec is updated. -->
```
new:
```
## Part B — Portability test (clean venv)

Validates the plugin installs and runs from a clean state (no dev-machine
dependency leakage). Run AFTER Part A passes and AFTER the root README install
section is updated. Follows the README install procedure step-by-step.

### Phase 0 — Hidden dependency audit (read code, no execution)

- [ ] Confirm `scripts/registrar.js` imports only `fs`, `path`, and local
      `./lib/proc`, `./lib/paths` (no npm packages). No `package.json` needed.
      Expected: stdlib + local lib only.
- [ ] Confirm `server/paths.py` resolves `PLUGIN_ROOT` via `CLAUDE_PLUGIN_ROOT`
      env or `__file__`-relative fallback — no hardcoded dev paths.
      Expected: portable resolution.
- [ ] Confirm `.mcp.json` only assumes `python` + `node` on PATH.
      Expected: no absolute paths.

### Phase 1 — Clean venv install

- [ ] Create a fresh venv: `python -m venv .venv-clean`
      Expected: `.venv-clean/` created.
- [ ] Activate it: `.venv-clean\Scripts\activate` (Windows).
      Expected: prompt shows `(.venv-clean)`.
- [ ] Confirm deps absent: `python -c "import mcp"` — Expected: fails with `ModuleNotFoundError` (proves the venv is clean).
- [ ] Install deps: `pip install -r cc-communicate/server/requirements.txt`
      Expected: psutil, filelock, mcp installed.
- [ ] `/plugin marketplace add "<marketplace path>"` (if not already added).
- [ ] `/plugin install cc-communicate@cc-communicate-local` (if not already).
- [ ] Launch CC from the activated venv shell (so `python` on PATH = venv
      python). Fully restart.
      Expected: CC starts.

### Phase 2 — Verify in clean env

- [ ] `/mcp` — Expected: cc-communicate server listed (MCP server started, no import crash).
- [ ] `my_session_id()` — Expected: a UUID (not `"failed, ..."`).
- [ ] `query_session(<your sid>)` — Expected: session info dict.

If all pass: the install procedure is portable. If the MCP server fails to
start: a hidden dependency or path assumption was found — fix it and re-run.
```

- [ ] **Step 2: Verify Part B present**

Run: `grep -c "## Part B — Portability test" cc-communicate-marketplace/TEST_CHECKLIST.md`
Expected: `1`

- [ ] **Step 3: Commit**

```bash
git add cc-communicate-marketplace/TEST_CHECKLIST.md
git commit -m "docs(test): append TEST_CHECKLIST Part B — clean-venv portability test"
```

---

**USER CHECKPOINT 2 — Execute Part B portability test**

The executor stops here. The USER manually executes `cc-communicate-marketplace/TEST_CHECKLIST.md` Part B (clean venv install + verify) following each Expected result.

- If all phases pass: proceed to Task 6.
- If the MCP server fails to start or any step deviates: a hidden dependency or path assumption was found. Report it. Fix (likely in `requirements.txt`, `.mcp.json`, or `paths.py`), re-run Part B. Do NOT tag v0.1.0 until Part B passes.

This checkpoint is a user action (clean venv + real CC); a subagent cannot execute it.

---

### Task 6: Tag v0.1.0 and push

**Files:**
- None (git metadata only)

**Interfaces:**
- Consumes: Checkpoint 1 (functional test) AND Checkpoint 2 (portability test) both passed
- Produces: `v0.1.0` git tag on `main`, pushed to `origin`

- [ ] **Step 1: Confirm both checkpoints passed**

Ask the user: "Did both Part A (functional) and Part B (portability) tests pass?"
Expected: user confirms both passed. If not, do not tag — return to the failing checkpoint.

- [ ] **Step 2: Confirm working tree is clean (all prior tasks committed)**

Run: `git status --porcelain`
Expected: empty (or only untracked files unrelated to this plan, e.g. `.playwright-mcp/`).

- [ ] **Step 3: Confirm version matches across manifests**

Run: `grep '"version"' cc-communicate-marketplace/.claude-plugin/marketplace.json cc-communicate-marketplace/cc-communicate/.claude-plugin/plugin.json`
Expected: both show `"version": "0.1.0"`.

- [ ] **Step 4: Tag and push**

```bash
git tag -a v0.1.0 -m "cc-communicate v0.1.0 — first portable release

SKILL.md (14-tool black-box reference), manual end-to-end test checklist,
clean-venv portability validation, completed install spec.
Windows only (Linux stubs)."
git push origin v0.1.0
```
Expected: tag created and pushed.

- [ ] **Step 5: Verify the tag exists remotely**

Run: `git ls-remote --tags origin v0.1.0`
Expected: a line ending in `refs/tags/v0.1.0`.

---

## Self-review notes

- **Spec coverage:** Phase 1 (SKILL.md + docstrings) → Tasks 1-2. Phase 2 (manual test checklist) → Task 3 + Checkpoint 1. Phase 3 (portability + packaging) → Tasks 4-5 + Checkpoint 2 + Task 6. All spec deliverables covered.
- **No placeholders:** all doc content (SKILL.md, TEST_CHECKLIST Parts A+B, README install + Roadmap) is written in full. Docstring fixes show exact old→new text.
- **Type consistency:** return shapes in SKILL.md match the corrected docstrings (Task 1) and the verified implementation (`{armed, command, timeout, baseline}`; `[CONNECTION CLOSED by <sid>]`; `connect failed, ...`).
- **Hidden-dep audit** (spec Phase 3 Part A) is embedded as TEST_CHECKLIST Part B Phase 0 so the user runs it as part of portability validation.
