# HP-08 Kernel Lifecycle + Safe GC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution for THIS wave (user mandate, durable): INLINE — no context-heavy subagents.** Execute task-by-task in the session (executing-plans style), `py -3 tools/run_regression.py --tier auto` as the exit gate. Live gates (full L1–L6) run at WAVE 3 exit (after HP-09/10/11), per the user's locked decision.

**Goal:** Decouple the kernel's exit decision from conversation registration (D10), add a safe whitelist GC, add a pending_spawn marker TTL, fix the T38 spawn-env leak, and dedup the `_pid_live`/`_match` liveness logic.

**Architecture:** Exit looks ONLY at queue/activity/terminate-flag — registration is persistent data (`alive_conversations.json`), not a process lease; restart+reload is the safety net, client retry + `_wake_remote` the race backstop, and a second queue scan the optimization. GC lives in a new `server/cleanup.py` module (whitelist = session_ctrl ≥7d, pending_spawn > TTL, queue/responses ≥7d; `pipe/`/`log/` structurally untouchable), triggered at kernel start + daily + on-demand RPC.

**Tech Stack:** Python 3 (`py -3`), pytest, psutil (liveness), filelock (ensure_core). No new dependencies.

## Global Constraints

- **`py -3` for ALL Python** on Windows (git-bash; quote paths with spaces/CJK).
- **Tests isolated**: conftest `server` fixture sets `CC_COMMUNICATE_DATA_DIR` → tmp_path and reloads modules per test. New module `cleanup` MUST be added to the conftest reload list (Task 3).
- **Parity**: v2_win ↔ v2_wsl byte-identical outside `.mcp.json`; sync modified files BEFORE any parity run (Task 6); verify with `py -3 tools/check_parity.py`.
- **Commit format**: `feat/fix/test/docs(scope): subject` + `Co-Authored-By: Claude <noreply@anthropic.com>`; work on main (user consent).
- **Records**: every bug found during implementation gets a T# entry in `tested&2betest.md` §1 (Method/Result/Confidence).
- **Run from repo root** `C:\研究生\实习\learn AI\projects\cc-communicate`; per-task test: `py -3 -m pytest tests/unit/test_X.py -v`; full: `py -3 -m pytest -q`.
- **Design spec**: `docs/superpowers/specs/2026-08-02-wave3-hp08-kernel-lifecycle-design.md` (user-approved). Deviations require user approval.

---

### Task 1: `proc.pid_matches` — shared liveness helper (dedup ride-along)

**Files:**
- Modify: `v2_win/cc-communicate/server/proc.py` (add `pid_matches` after `proc_start_time`)
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (`check_alive._match` + `session_by_pid._pid_live` → `proc.pid_matches`; import swap)
- Create: `tests/unit/test_proc_pid_matches.py`
- Modify: `tests/unit/test_check_alive_fallback.py` (8 monkeypatch sites: `ka.proc_start_time` → `server.proc.proc_start_time`)

**Interfaces:**
- Produces: `proc.pid_matches(pid, recorded) -> bool|None` — None=unknown/unset, False=dead, True=alive (same 1s start-time rule as today's `_match`/`_pid_live`).
- Consumes: `proc.proc_start_time(pid)` (existing).

- [ ] **Step 1: Write the failing tests for `pid_matches`**

Create `tests/unit/test_proc_pid_matches.py`:

```python
"""HP-08 ride-along: proc.pid_matches is the shared liveness rule
(previously duplicated as check_alive._match and session_by_pid._pid_live)."""
import pytest
import proc


def test_pid_matches_unknown(server):
    assert proc.pid_matches(None, 1.0) is None
    assert proc.pid_matches(123, None) is None


def test_pid_matches_dead(server, monkeypatch):
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: None)
    assert proc.pid_matches(123, 1.0) is False


def test_pid_matches_alive(server, monkeypatch):
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: 1000.0)
    assert proc.pid_matches(123, 1000.0) is True


def test_pid_matches_start_time_mismatch(server, monkeypatch):
    """A live pid with a different start time is a DIFFERENT incarnation
    (pid reuse) - never a match."""
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: 9999.0)
    assert proc.pid_matches(123, 1000.0) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_proc_pid_matches.py -v`
Expected: FAIL — `AttributeError: module 'proc' has no attribute 'pid_matches'`

- [ ] **Step 3: Add `pid_matches` to `proc.py`**

In `v2_win/cc-communicate/server/proc.py`, after `proc_start_time` (line 42):

```python
def pid_matches(pid, recorded):
    """None=unknown/unset, False=dead, True=alive. The shared liveness rule
    (was check_alive._match / session_by_pid._pid_live): the pid must exist
    AND its start time must match the recorded one within 1s (rejects pid
    reuse)."""
    if pid is None or recorded is None:
        return None
    current = proc_start_time(pid)
    if current is None:
        return False
    return abs(current - float(recorded)) <= 1.0
```

- [ ] **Step 4: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_proc_pid_matches.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Refactor `kernel_api.py` to use the shared helper + update test patch targets (atomic)**

In `v2_win/cc-communicate/server/kernel_api.py`:
1. Replace the import `from proc import proc_start_time` (line 29) with `import proc`.
2. Replace the `_match` inner function in `check_alive` (lines 57-64) and its two call sites (lines 71, 79): delete `_match`, call `proc.pid_matches(pid, recorded)` directly.
3. Delete the module-level `_pid_live` (lines 547-556); in `session_by_pid` (line 570) call `proc.pid_matches(pid, known[pid])`.

The resulting `check_alive` body:

```python
    known = info.get("known_pids")
    if known:
        # newest-first: the primary (last write) is checked first - the hot
        # path stays O(1) when it is alive; a dead last-write is pruned BEFORE
        # an older live pid can match and return (T30).
        for pid, recorded in list(known.items())[::-1]:
            m = proc.pid_matches(pid, recorded)
            if m is True:
                info["pid"], info["start_time"] = pid, recorded
                return 1
            if m is False:
                known.pop(pid, None)  # dead - don't re-check next time
    else:
        m = proc.pid_matches(info.get("pid"), info.get("start_time"))
        if m is True:
            return 1
        if m is False:
            alive_sessions.pop(session_id, None)
        return 0
    alive_sessions.pop(session_id, None)
    return 0
```

And `session_by_pid`'s fallback loop keeps its shape but the inner condition becomes:

```python
        if pid in known and proc.pid_matches(pid, known[pid]) is True:
            return sid
```

In `tests/unit/test_check_alive_fallback.py`, update ALL 8 monkeypatch sites (lines 23, 38, 49, 62, 89, 100, 109, 125) from:
`monkeypatch.setattr(ka, "proc_start_time", lambda pid: ...)` to:
`monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: ...)`

- [ ] **Step 6: Run the full suite to verify the refactor is behavior-preserving**

Run: `py -3 -m pytest -q`
Expected: PASS — all tests green (the check_alive/session_by_pid behavior tests are the refactor's safety net)

- [ ] **Step 7: Commit**

```bash
git add v2_win/cc-communicate/server/proc.py v2_win/cc-communicate/server/kernel_api.py tests/unit/test_proc_pid_matches.py tests/unit/test_check_alive_fallback.py
git commit -m "refactor(HP-08): dedup pid liveness into proc.pid_matches (_match/_pid_live ride-along)"
```

---

### Task 2: Spawn env sanitization (T38 code-level fix)

**Files:**
- Modify: `v2_win/cc-communicate/server/spawn.py`
- Create: `tests/unit/test_spawn_env.py`

**Interfaces:**
- Produces: `spawn._child_env(spawn_token=None) -> dict` — sanitized child env (strips `CLAUDE_CODE_CHILD_SESSION`, injects `CC_COMMUNICATE_SPAWN_TOKEN` when given).
- Consumes: nothing new. `_detached_popen(cmd_args, cwd, env)` and `_tmux_spawn(cwd, claude_argv, env_token)` signatures unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_spawn_env.py`:

```python
"""HP-08 / T38: spawned CCs must not inherit CC-internal env that breaks
resume. CLAUDE_CODE_CHILD_SESSION -> transcript saving off -> non-resumable.
Only CC-internal vars are stripped; user config is preserved."""
import os


def test_child_env_strips_child_session_and_injects_token(server, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CC_COMMUNICATE_SPAWN_TOKEN", "stale")
    monkeypatch.setenv("ANTHROPIC_MODEL", "x")  # user vars are NOT stripped
    env = server.spawn._child_env("tok-1")
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert env["CC_COMMUNICATE_SPAWN_TOKEN"] == "tok-1"
    assert env["ANTHROPIC_MODEL"] == "x"


def test_child_env_no_token(server, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    env = server.spawn._child_env()
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert "CC_COMMUNICATE_SPAWN_TOKEN" not in env


def test_spawn_cc_new_passes_sanitized_env(server, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    captured = {}
    monkeypatch.setattr(server.spawn, "_detached_popen",
                        lambda cmd_args, cwd=None, env=None:
                        captured.update(cmd=cmd_args, env=env))
    server.spawn.spawn_cc_new("/tmp", "prompt", spawn_token="t1")
    assert "CLAUDE_CODE_CHILD_SESSION" not in captured["env"]
    assert captured["env"]["CC_COMMUNICATE_SPAWN_TOKEN"] == "t1"


def test_spawn_cc_resume_passes_sanitized_env(server, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    captured = {}
    monkeypatch.setattr(server.spawn, "_detached_popen",
                        lambda cmd_args, cwd=None, env=None:
                        captured.update(env=env))
    server.spawn.spawn_cc_resume("s1", "prompt", "/tmp")
    assert "CLAUDE_CODE_CHILD_SESSION" not in captured["env"]


def test_tmux_spawn_strips_child_session(server, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    captured = {}
    monkeypatch.setattr(server.spawn.subprocess, "Popen",
                        lambda *a, **kw: captured.update(args=a[0]))
    server.spawn._tmux_spawn("/tmp", ["/bin/claude", "p"], env_token="t1")
    args = captured["args"]
    assert args[0] == "tmux"
    assert "env" in args and "-u" in args
    assert "CLAUDE_CODE_CHILD_SESSION" in args
    assert "CC_COMMUNICATE_SPAWN_TOKEN=t1" in args
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_spawn_env.py -v`
Expected: FAIL — `AttributeError: module 'spawn' has no attribute '_child_env'`

- [ ] **Step 3: Implement `_child_env` + wire into both spawn paths**

In `v2_win/cc-communicate/server/spawn.py`:

1. Add after `_detached_popen` (line 42):

```python
def _child_env(spawn_token: str = None) -> dict:
    """Sanitized child env for spawned CCs (T38 / HP-08): CC-internal
    CLAUDE_CODE_CHILD_SESSION must not leak into children (it turns
    transcript saving off -> non-resumable sessions). Whitelist-extensible -
    only add vars with concrete evidence. HP-04 spawn_token (plan A) is
    injected here."""
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_CHILD_SESSION", None)
    if spawn_token:
        env["CC_COMMUNICATE_SPAWN_TOKEN"] = spawn_token
    return env
```

2. `spawn_cc_new` (lines 89-98): replace the `env = None`/`env = {...}` block with a single sanitized env:

```python
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "claude",
                         "--dangerously-skip-permissions", prompt],
                        cwd=cwd, env=_child_env(spawn_token))
```

3. `spawn_cc_resume` (lines 110-115): sanitized env even without a token:

```python
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "claude", "--resume", session_id,
                         "--dangerously-skip-permissions", prompt],
                        cwd=cwd, env=_child_env())
```

4. `_tmux_spawn` (lines 60-79): replace the `env_token` block so the POSIX `env` wrapper also strips the var (works with or without a token):

```python
    env_args = ["env", "-u", "CLAUDE_CODE_CHILD_SESSION"]
    if env_token:
        env_args.append("CC_COMMUNICATE_SPAWN_TOKEN=" + env_token)
    cmd += env_args + claude_argv
```

- [ ] **Step 4: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_spawn_env.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add v2_win/cc-communicate/server/spawn.py tests/unit/test_spawn_env.py
git commit -m "fix(HP-08/T38): spawn env sanitization - strip CLAUDE_CODE_CHILD_SESSION from CC-spawned children"
```

---

### Task 3: `server/cleanup.py` — safe-GC module (whitelist + run_gc + state + maybe_run_gc)

**Files:**
- Create: `v2_win/cc-communicate/server/cleanup.py`
- Modify: `v2_win/cc-communicate/server/paths.py` (add `GC_STATE_FILE`)
- Modify: `tests/conftest.py` (add `"gc"` to the reload list)
- Create: `tests/unit/test_gc.py`

**Interfaces:**
- Produces:
  - cleanup.PENDING_SPAWN_TTL_SECONDS` (float, env `CC_COMMUNICATE_PENDING_SPAWN_TTL_SECONDS`, default 3600)
  - cleanup.pending_marker_expired(path) -> bool` (created_at_ms-based, mtime fallback)
  - cleanup.collect_candidates() -> dict[str, list[str]]` — `{"session_ctrl": [...], "pending_spawn": [...], "responses": [...]}`
  - cleanup.run_gc(dry_run=False) -> {"deleted": int, "dry_run": bool, "violations": list, "details": list}`
  - cleanup.gc_due(last_run_at) -> bool`; cleanup.load_last_gc_run() -> float|None`; cleanup.save_last_gc_run(ts)`; cleanup.maybe_run_gc() -> dict|None`
- Consumes: `paths.{SESSION_CTRL_DIR, PENDING_SPAWN_DIR, QUEUE_RESPONSES_DIR, GC_STATE_FILE}`, `fileutil.atomic_write_json`.

- [ ] **Step 1: Add `GC_STATE_FILE` to `paths.py`**

In `v2_win/cc-communicate/server/paths.py`, in the upper-layer block (after `TERMINATE_FLAG`, line 41):

```python
GC_STATE_FILE = os.path.join(SERVER_DATA_DIR, 'gc_state.json')  # HP-08: last-run timestamp for the daily GC sweep
```

- [ ] **Step 2: Add `"gc"` to the conftest reload list**

In `tests/conftest.py` (line 30-33), insert `"gc"` after `"spawn"` (it must reload BEFORE `kernel_api`, which imports it in Task 4):

```python
    for name in ("paths", "result", "validation", "proc", "conversations",
                 "spawn", "cleanup", "machine_identity", "check_core", "rpc_client",
                 "kernel_api", "kernel"):
```

- [ ] **Step 3: Write the failing tests**

Create `tests/unit/test_gc.py`:

```python
"""HP-08: safe-GC whitelist. pipe/ (unacked) and log/ (conversation records)
are NEVER touched - structural (enumerated roots + path guardrail), not
convention."""
import json
import os
import time


def _make_file(path, age_seconds):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"k": "v"}, f)
    old = time.time() - age_seconds
    os.utime(path, (old, old))


def _write_pending(server, token, created_ms):
    d = server.paths.PENDING_SPAWN_DIR
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, token + ".json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "spawn_token": token,
                   "created_at_ms": created_ms}, f)


def test_collect_only_whitelisted(server):
    # old whitelisted files -> candidates
    _make_file(os.path.join(server.data_root, "session_ctrl", "e1.json"), 8 * 86400)
    _make_file(os.path.join(server.data_root, "queue", "responses", "r1.json"), 8 * 86400)
    _write_pending(server, "t-old", int((time.time() - 2 * 3600) * 1000))
    # fresh whitelisted -> NOT candidates
    _make_file(os.path.join(server.data_root, "session_ctrl", "e2.json"), 60)
    _write_pending(server, "t-fresh", int(time.time() * 1000))
    # decoys in the NEVER-TOUCH dirs (old mtime on purpose)
    _make_file(os.path.join(server.data_root, "conversations", "a__b", "pipe", "m1.json"), 90 * 86400)
    _make_file(os.path.join(server.data_root, "conversations", "a__b", "log", "m2.json"), 90 * 86400)
    got = server.gc.collect_candidates()
    assert sorted(k for k, v in got.items() if v) == \
        ["pending_spawn", "responses", "session_ctrl"]
    assert len(got["session_ctrl"]) == 1
    assert got["session_ctrl"][0].endswith("e1.json")
    assert len(got["responses"]) == 1
    assert len(got["pending_spawn"]) == 1
    assert got["pending_spawn"][0].endswith("t-old.json")


def test_run_gc_deletes_only_candidates(server):
    _make_file(os.path.join(server.data_root, "session_ctrl", "e1.json"), 8 * 86400)
    _make_file(os.path.join(server.data_root, "conversations", "a__b", "pipe", "m1.json"), 90 * 86400)
    res = server.gc.run_gc()
    assert res["deleted"] == 1
    assert res["violations"] == []
    assert not os.path.exists(os.path.join(server.data_root, "session_ctrl", "e1.json"))
    assert os.path.exists(os.path.join(server.data_root, "conversations", "a__b", "pipe", "m1.json"))


def test_run_gc_dry_run_deletes_nothing(server):
    _make_file(os.path.join(server.data_root, "session_ctrl", "e1.json"), 8 * 86400)
    res = server.gc.run_gc(dry_run=True)
    assert res["dry_run"] is True
    assert res["deleted"] == 0
    assert os.path.exists(os.path.join(server.data_root, "session_ctrl", "e1.json"))


def test_run_gc_violation_guardrail(server, monkeypatch):
    """Defense in depth: even a crafted candidate that contains pipe/log
    components is skipped + reported, never deleted."""
    bad = os.path.join(server.data_root, "conversations", "a__b", "pipe", "m1.json")
    _make_file(bad, 90 * 86400)
    monkeypatch.setattr(server.gc, "collect_candidates",
                        lambda: {"session_ctrl": [bad]})
    res = server.gc.run_gc()
    assert res["violations"] == [bad]
    assert res["deleted"] == 0
    assert os.path.exists(bad)


def test_pending_marker_ttl_fresh_and_expired(server):
    fresh = os.path.join(server.paths.PENDING_SPAWN_DIR, "t1.json")
    old = os.path.join(server.paths.PENDING_SPAWN_DIR, "t2.json")
    _write_pending(server, "t1", int(time.time() * 1000))
    _write_pending(server, "t2", int((time.time() - 2 * 3600) * 1000))
    assert server.gc.pending_marker_expired(fresh) is False
    assert server.gc.pending_marker_expired(old) is True


def test_pending_marker_mtime_fallback(server):
    """Markers without created_at_ms (older producers) fall back to file
    mtime - fresh mtime stays fresh."""
    p = os.path.join(server.paths.PENDING_SPAWN_DIR, "t1.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "spawn_token": "t1"}, f)
    assert server.gc.pending_marker_expired(p) is False   # fresh mtime
    old = time.time() - 2 * 3600
    os.utime(p, (old, old))
    assert server.gc.pending_marker_expired(p) is True    # old mtime


def test_gc_due_and_state(server):
    g = server.gc
    assert g.gc_due(None) is True
    assert g.gc_due(time.time()) is False
    assert g.gc_due(time.time() - g.GC_INTERVAL_SECONDS - 1) is True
    g.save_last_gc_run(time.time())
    assert g.maybe_run_gc() is None              # just ran -> skip
    g.save_last_gc_run(time.time() - g.GC_INTERVAL_SECONDS - 1)
    res = g.maybe_run_gc()                       # due -> runs
    assert res is not None and "deleted" in res
```

- [ ] **Step 4: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_gc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gc'` (paths/conftest changes alone don't create the module)

- [ ] **Step 5: Implement `server/cleanup.py`**

Create `v2_win/cc-communicate/server/cleanup.py`:

```python
"""Safe GC for the cc-communicate data root (HP-08 / D10).

WHITELIST: the ONLY artifacts GC may ever touch. Everything else (pipe/,
log/ - unacked messages and conversation records) is NEVER deleted. The
whitelist is structural: collect_candidates() enumerates exactly these three
roots, and run_gc() re-checks every candidate path for pipe/log components
(violations are skipped + reported).

  session_ctrl/*.json     >= 7 days   (processed start/end events; replay of
                                       a >7d-old event is a no-op - sessions
                                       .json already holds ended_at)
  pending_spawn/*.json    > TTL       (poisoned spawn markers; TTL default
                                       1h, CC_COMMUNICATE_PENDING_SPAWN_TTL_
                                       SECONDS)
  queue/responses/*.json  >= 7 days   (request ids are uuid4, never re-polled
                                       - each retry generates a fresh rid)

Minimum age is the race guard: nothing younger than its threshold is ever
touched, so out-of-process writers (registrar.js writing session_ctrl) can't
lose a file they just wrote. GC runs in the kernel's single thread - no
intra-kernel races. Deletions are best-effort: per-file OSError -> details,
never raised (a GC failure must not take down the kernel).
"""
from __future__ import annotations

import json
import os
import time

import fileutil
from paths import (
    PENDING_SPAWN_DIR, QUEUE_RESPONSES_DIR, SESSION_CTRL_DIR, GC_STATE_FILE,
)

PENDING_SPAWN_TTL_SECONDS = float(
    os.environ.get("CC_COMMUNICATE_PENDING_SPAWN_TTL_SECONDS", "3600"))
SESSION_CTRL_MAX_AGE_SECONDS = 7 * 24 * 3600
RESPONSES_MAX_AGE_SECONDS = 7 * 24 * 3600
GC_INTERVAL_SECONDS = 24 * 3600
_FORBIDDEN_COMPONENTS = ("pipe", "log")


def _age_seconds(path: str) -> float:
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return float("inf")  # missing/unreadable -> beyond any threshold


def pending_marker_expired(path: str) -> bool:
    """True when a pending_spawn marker is older than the TTL. Freshness
    comes from the marker's created_at_ms (authoritative - written at
    spawn); a marker without it (older producers) falls back to file mtime."""
    age = None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        ts = data.get("created_at_ms")
        if isinstance(ts, (int, float)) and ts > 0:
            age = time.time() - ts / 1000.0
    except (OSError, ValueError):
        pass
    if age is None:
        age = _age_seconds(path)
    return age > PENDING_SPAWN_TTL_SECONDS


def _candidates_older(root: str, max_age: float) -> list:
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return []
    return [os.path.join(root, n) for n in names
            if n.endswith(".json")
            and _age_seconds(os.path.join(root, n)) >= max_age]


def collect_candidates() -> dict:
    """Whitelist scan: {kind: [abs paths]} eligible for deletion. Enumerates
    ONLY the three whitelisted roots - pipe/ and log/ are never listed."""
    out = {
        "session_ctrl": _candidates_older(SESSION_CTRL_DIR,
                                          SESSION_CTRL_MAX_AGE_SECONDS),
        "responses": _candidates_older(QUEUE_RESPONSES_DIR,
                                       RESPONSES_MAX_AGE_SECONDS),
    }
    pending = []
    try:
        names = os.listdir(PENDING_SPAWN_DIR)
    except FileNotFoundError:
        names = []
    for n in names:
        if n.endswith(".json") and \
                pending_marker_expired(os.path.join(PENDING_SPAWN_DIR, n)):
            pending.append(os.path.join(PENDING_SPAWN_DIR, n))
    out["pending_spawn"] = pending
    return out


def run_gc(dry_run: bool = False) -> dict:
    """Delete all whitelisted candidates. dry_run: report, delete nothing.
    Returns {"deleted", "dry_run", "violations", "details"} - never raises."""
    violations, deleted, details = [], 0, []
    for kind, paths in collect_candidates().items():
        for path in paths:
            parts = path.replace(os.sep, "/").split("/")
            if any(comp in _FORBIDDEN_COMPONENTS for comp in parts):
                violations.append(path)  # guardrail - should be impossible
                continue
            if dry_run:
                details.append({"kind": kind, "path": path, "dry_run": True})
                continue
            try:
                os.remove(path)
                deleted += 1
                details.append({"kind": kind, "path": path, "deleted": True})
            except OSError as e:
                details.append({"kind": kind, "path": path, "error": str(e)})
    return {"deleted": deleted, "dry_run": bool(dry_run),
            "violations": violations, "details": details}


def load_last_gc_run() -> float | None:
    try:
        with open(GC_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        ts = data.get("last_run_at")
        return float(ts) if isinstance(ts, (int, float)) else None
    except (OSError, ValueError):
        return None


def save_last_gc_run(ts: float):
    try:
        os.makedirs(os.path.dirname(GC_STATE_FILE), exist_ok=True)
        fileutil.atomic_write_json(
            GC_STATE_FILE, {"schema_version": 1, "last_run_at": ts})
    except OSError:
        pass  # best-effort: a failed state write just re-runs GC next time


def gc_due(last_run_at: float | None) -> bool:
    if last_run_at is None:
        return True
    return time.time() - last_run_at >= GC_INTERVAL_SECONDS


def maybe_run_gc() -> dict | None:
    """Run GC when due (never ran, or last run >= GC_INTERVAL_SECONDS ago).
    Returns the run_gc result, or None when skipped. Never raises."""
    try:
        last = load_last_gc_run()
        if not gc_due(last):
            return None
        res = run_gc()
        save_last_gc_run(time.time())
        return res
    except Exception:
        return None
```

- [ ] **Step 6: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_gc.py -v`
Expected: PASS (8 passed)

- [ ] **Step 7: Commit**

```bash
git add v2_win/cc-communicate/server/cleanup.py v2_win/cc-communicate/server/paths.py tests/conftest.py tests/unit/test_gc.py
git commit -m "feat(HP-08): safe-GC module - whitelist + min-age + dry-run + daily state (cleanup.py)"
```

---

### Task 4: pending_spawn TTL semantics (kernel_api)

**Files:**
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (`has_pending_spawn`, `claim_pending_spawn`)
- Modify: `tests/unit/test_spawn_token.py` (extend helper + 3 new tests)

**Interfaces:**
- Consumes: cleanup.pending_marker_expired(path)`, cleanup.PENDING_SPAWN_TTL_SECONDS` (Task 3).
- Produces: unchanged signatures — `has_pending_spawn(token) -> bool` (expired ⇒ False), `claim_pending_spawn(spawn_tokens, token, session_id) -> dict` (expired ⇒ same no-pending result as missing).

- [ ] **Step 1: Write the failing tests (extend `test_spawn_token.py`)**

In `tests/unit/test_spawn_token.py`:
1. Update the `_write_pending` helper (line 6-11) to carry `created_at_ms`:

```python
def _write_pending(server, token, created_ms=None):
    d = server.paths.PENDING_SPAWN_DIR
    os.makedirs(d, exist_ok=True)
    marker = {"schema_version": 1, "spawn_token": token}
    if created_ms is not None:
        marker["created_at_ms"] = created_ms
    with open(os.path.join(d, token + ".json"), "w", encoding="utf-8") as f:
        json.dump(marker, f)
```

2. Append:

```python
# ---------- HP-08: pending-spawn marker TTL ----------
# A poisoned marker (kernel crash in the write-window: marker written, child
# never spawned, no start event) must expire - otherwise same-token retries
# never re-spawn (Wave-2 deferred minor).


def test_has_pending_spawn_expired_marker(server):
    ka = server.kernel_api
    _write_pending(server, "t1", int((time.time() - 2 * 3600) * 1000))
    assert ka.has_pending_spawn("t1") is False


def test_claim_expired_marker_rejected(server):
    ka = server.kernel_api
    _write_pending(server, "t1", int((time.time() - 2 * 3600) * 1000))
    assert ka.claim_pending_spawn({}, "t1", "s1") == \
        {"claimed": False, "reason": "no pending spawn for token"}


def test_spawn_after_expiry_writes_fresh_marker(server, monkeypatch):
    ka = server.kernel_api
    monkeypatch.setattr(server.spawn, "spawn_cc_new", lambda *a, **kw: None)
    _write_pending(server, "t1", int((time.time() - 2 * 3600) * 1000))
    assert ka.has_pending_spawn("t1") is False
    ka.spawn_cc_new("/tmp", "prompt", spawn_token="t1")
    assert ka.has_pending_spawn("t1") is True  # fresh marker replaced the stale one
```

`test_spawn_token.py` needs `import time` at the top (add if absent).

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_spawn_token.py -v`
Expected: FAIL — `test_has_pending_spawn_expired_marker` asserts False but gets True (no TTL logic yet)

- [ ] **Step 3: Implement the TTL in `kernel_api.py`**

In `v2_win/cc-communicate/server/kernel_api.py`:
1. Add `import cleanup` to the imports (with the other `import X` lines; note `import conversations` style is already used).
2. Replace `has_pending_spawn` (lines 294-295):

```python
def has_pending_spawn(token: str) -> bool:
    """HP-08: a marker older than the TTL counts as ABSENT (poisoned-marker
    un-poisoning; the expired file itself is removed by the GC sweep)."""
    path = os.path.join(PENDING_SPAWN_DIR, token + ".json")
    if not os.path.isfile(path):
        return False
    return not gc.pending_marker_expired(path)
```

3. `claim_pending_spawn` (lines 298-313): the existing `if not has_pending_spawn(token)` line now covers expired markers automatically — update its docstring:

```python
def claim_pending_spawn(spawn_tokens: dict, token: str, session_id: str) -> dict:
    """Plan B: the spawned worker claims its token on its first tool call.
    Idempotent: an existing binding is kept (worker retries are no-ops).
    HP-08: an expired marker is treated as absent (same result as missing)."""
```

- [ ] **Step 4: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_spawn_token.py tests/unit/test_gc.py -v`
Expected: PASS (existing + 3 new tests; gc tests unaffected)

- [ ] **Step 5: Commit**

```bash
git add v2_win/cc-communicate/server/kernel_api.py tests/unit/test_spawn_token.py
git commit -m "feat(HP-08): pending_spawn marker TTL - expired markers count as absent (un-poisons same-token retries)"
```

---

### Task 5: Exit predicate (D10) + second queue scan + kernel GC triggers + `run_gc` kernel function

**Files:**
- Modify: `v2_win/cc-communicate/server/kernel.py`
- Modify: `v2_win/cc-communicate/server/validation.py` (add `validate_bool`)
- Modify: `tests/unit/test_validation.py` (add `validate_bool` test)
- Create: `tests/unit/test_kernel_exit.py`

**Interfaces:**
- Consumes: cleanup.maybe_run_gc()` (Task 3); `kernel_api.run_gc` (added here).
- Produces:
  - `kernel._should_exit() -> bool` — queue/activity/terminate ONLY (registration no longer blocks).
  - `kernel._exit_decision() -> bool` — `_should_exit` + R4 second queue scan.
  - `kernel_api.run_gc(dry_run=False) -> dict` — dispatcher wrapper around cleanup.run_gc`.
  - `validation.validate_bool(value) -> bool` — raises `InvalidArgumentError` on non-bool.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_kernel_exit.py`:

```python
"""HP-08 / D10: exit looks ONLY at queue/activity/terminate - a registered-
but-idle conversation is NOT a process lease (state persists + reloads)."""
import os
import time


def test_registered_but_idle_exits(server):
    """THE behavior change: registration no longer blocks exit."""
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 0.0
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    k._last_activity = time.monotonic() - 1.0
    assert k._should_exit() is True


def test_fresh_activity_blocks_exit(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 600.0
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    k._last_activity = time.monotonic()
    assert k._should_exit() is False


def test_queue_pending_blocks_exit(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 0.0
    k._last_activity = time.monotonic() - 1.0
    req = os.path.join(server.paths.QUEUE_DIR, "1234_rid.json")
    with open(req, "w", encoding="utf-8") as f:
        f.write("{}")
    assert k._should_exit() is False
    os.remove(req)
    assert k._should_exit() is True


def test_explicit_exit_and_flag_win(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 600.0
    k._last_activity = time.monotonic()
    k._exit_requested = True
    assert k._should_exit() is True
    k._exit_requested = False
    open(server.paths.TERMINATE_FLAG, "w").close()
    assert k._should_exit() is True


def test_exit_decision_second_queue_scan(server):
    """R4: a request that lands in the exit window (between _should_exit and
    the break) restarts the cycle - the second scan is the optimization,
    client retry + _wake_remote the correctness backstop."""
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 0.0
    k._last_activity = time.monotonic() - 1.0
    assert k._exit_decision() is True
    req = os.path.join(server.paths.QUEUE_DIR, "1234_rid.json")
    with open(req, "w", encoding="utf-8") as f:
        f.write("{}")
    assert k._exit_decision() is False  # second scan sees the request
    os.remove(req)


def test_registered_convs_survive_exit_and_restart(server):
    """Acceptance: registered-but-idle kernel exits; a fresh kernel instance
    reloads the registration from disk; send_message still works."""
    k = server.kernel
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 0.0
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    k._last_activity = time.monotonic() - 1.0
    assert k._should_exit() is True    # can exit while registered
    k._save_alive_convs()              # exit path persists
    k.alive_conversations.clear()      # process gone
    k._load_alive_convs()              # restart reloads
    assert ("a", "b") in k.alive_conversations
    r = ka.send_message(k.alive_conversations, {}, "store", "a", "b", "hi")
    assert r["sent"] is True
```

Add to `tests/unit/test_validation.py`:

```python
def test_validate_bool(server):
    v = server.validation
    assert v.validate_bool(True) is True
    assert v.validate_bool(False) is False
    with pytest.raises(v.InvalidArgumentError):
        v.validate_bool("yes")
```

(`pytest` is already imported in test_validation.py — verify; add `import pytest` if not.)

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_kernel_exit.py tests/unit/test_validation.py -v`
Expected: FAIL — `test_registered_but_idle_exits` (old predicate returns False), `_exit_decision` AttributeError, `validate_bool` AttributeError

- [ ] **Step 3: Rewrite the exit predicate + add `_exit_decision` in `kernel.py`**

In `v2_win/cc-communicate/server/kernel.py`:
1. Add `import cleanup` to the imports.
2. Replace `_should_exit` (lines 482-493):

```python
def _should_exit() -> bool:
    """D10: exit looks ONLY at queue/activity/terminate - a registered-but-
    idle conversation is NOT a process lease. All kernel state is persistent
    (alive_conversations.json etc.), so a restart reloads it; the exit path
    saves it (main's finally block)."""
    if _exit_requested or os.path.exists(TERMINATE_FLAG):
        return True
    if _queue_has_pending():                    # queue: in-flight request
        return False
    if time.monotonic() - _last_activity < _IDLE_TIMEOUT:  # activity
        return False
    return True


def _exit_decision() -> bool:
    """True = exit now. Guards the exit-vs-request race (R4): a request that
    landed in the window between _should_exit() and the break restarts the
    cycle (second queue scan - the optimization; client retry + _wake_remote
    is the correctness backstop)."""
    if not _should_exit():
        return False
    return not _queue_has_pending()
```

3. Update the module docstring's Lifecycle line (line 17):

```python
-> EXIT (idle_timeout AND queue empty - registration is NOT a lease (D10);
or SIGINT/SIGTERM; or kernel_terminate).
```

4. In `main()`: after `_load_operation_journal()` / `process_session_ctrl_event()` and BEFORE `_write_core_status(1)` (line 535), add the start-time GC:

```python
    res = gc.maybe_run_gc()
    if res:
        log.info("GC at start: %s", res)
    _last_gc_check = time.time()
```

5. Add the module global `_last_gc_check: float = 0.0` next to `_last_activity` (line 58).

6. Replace the loop's exit check (line 556) and add the daily sweep before `time.sleep`:

```python
            if _exit_decision():
                break
            # HP-08: daily GC sweep (due-check once per minute of wall time;
            # gc.maybe_run_gc is a no-op between due dates)
            if time.time() - _last_gc_check >= 60:
                _last_gc_check = time.time()
                res = gc.maybe_run_gc()
                if res:
                    log.info("GC sweep: %s", res)
            time.sleep(sleep)
```

- [ ] **Step 4: Add `validate_bool` + `run_gc` kernel function**

In `v2_win/cc-communicate/server/validation.py`, after `validate_cursors`:

```python
def validate_bool(value) -> bool:
    if not isinstance(value, bool):
        raise InvalidArgumentError(
            f"expected a bool; got {type(value).__name__}")
    return value
```

In `v2_win/cc-communicate/server/kernel_api.py`:
1. Add at the end (control section, after `kernel_terminate`):

```python
def run_gc(dry_run: bool = False) -> dict:
    """HP-08: invoke the safe-GC sweep. Kernel function ONLY - not an MCP
    tool (the upper layer never needs it); callable via RPC for tests and
    live gates."""
    return gc.run_gc(dry_run=bool(dry_run))
```

2. In `kernel.py`: add the validator + dispatch route. In `_ARG_VALIDATORS` (after `get_connection_info`, line 396):

```python
    "run_gc": {"dry_run": validation.validate_bool},
```

In `_dispatch` (after the `kernel_terminate` branch, line 470):

```python
    if function == "run_gc":
        return kernel_api.run_gc(args.get("dry_run", False))
```

`run_gc` is NOT added to `_JOURNALED_FUNCTIONS` — it is idempotent cleanup, not an HP-03 mutation (journaling it would churn the journal for zero benefit).

- [ ] **Step 5: Run the full suite**

Run: `py -3 -m pytest -q`
Expected: PASS — all unit tests (new + existing)

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/kernel.py v2_win/cc-communicate/server/kernel_api.py v2_win/cc-communicate/server/validation.py tests/unit/test_kernel_exit.py tests/unit/test_validation.py
git commit -m "feat(HP-08): exit predicate decoupled from registration (D10) + second queue scan + kernel GC triggers + run_gc kernel function"
```

---

### Task 6: Regression gate + parity sync + records

**Files:**
- Modify: `tools/run_regression.py` (add L5/L6 live checklists — the Wave 3 exit gate re-runs ALL of L1-L6)
- Modify: `tests/unit/test_run_regression.py` (checklist-count assertion, if any)
- Sync: `v2_wsl/cc-communicate/server/` ← modified files from `v2_win/`
- Modify: `tested&2betest.md` (T40 record)

**Interfaces:**
- Consumes: all Tasks 1-5 outputs.

- [ ] **Step 1: Extend the live checklists with L5/L6**

In `tools/run_regression.py`, the `LIVE_CHECKLISTS` list currently ends at L4 (line ~80). Append (follow the existing tuple shape `(title, body)`, using the Wave-2 gate evidence recorded in T37):

```python
    ("L5 - Same-cwd spawn race (T37 live gate)", """\
  Spawn 2+ collaborators in the SAME cwd; verify each window gets its OWN
  session (no cross-session id bleed); collect via session_ctrl events.
  Record:   T# in tested&2betest.md sec1 with per-window session ids"""),
    ("L6 - Correlated connect handshake (T37 live gate)", """\
  connect with an explicit connection_id; verify the reply matched by
  correlation_id (hello record kind='hello', correlation_id = connection_id);
  verify info.json single-active CONFLICT on a second id.
  Record:   T# in tested&2betest.md sec1 with correlation match evidence"""),
```

Check `tests/unit/test_run_regression.py::test_live_tier_prints_all_checklists_exits_0` — if it asserts a fixed checklist count, update it to the new count (or it passes automatically if it just prints + exits 0).

- [ ] **Step 2: Run the full auto gate on v2_win (expect parity RED — v2_wsl not yet synced)**

Run: `py -3 -m pytest -q`
Expected: PASS (all unit tests)

- [ ] **Step 3: Sync v2_wsl (byte-identical)**

Run (from repo root, git-bash):

```bash
cp v2_win/cc-communicate/server/{gc,kernel,kernel_api,paths,proc,spawn,validation}.py v2_wsl/cc-communicate/server/
```

Then run: `py -3 tools/check_parity.py`
Expected: `PASS (PARITY OK ...)` — if any file mismatches, fix by copying the v2_win file (canonical).

- [ ] **Step 4: Run the full auto gate**

Run: `py -3 tools/run_regression.py --tier auto`
Expected: `GATE PASS` — T0 syntax (both trees), T1 pytest, T2 parity all PASS

- [ ] **Step 5: Record T40 in `tested&2betest.md` §1**

Append (following the existing T# format):

```markdown
### T40 — HP-08 unit acceptance: registered-but-idle kernel exits; restart reloads state; GC whitelist holds
- **Method**: unit (test_kernel_exit.py / test_gc.py / test_spawn_token.py / test_spawn_env.py):
  exit predicate decoupled from registration (D10); R4 second queue scan;
  GC whitelist (session_ctrl ≥7d, pending_spawn >TTL, responses ≥7d) never
  touches pipe/log (structural guardrail test); pending_spawn TTL un-poisons
  same-token retries; spawn env sanitization (T38 code-level fix);
  proc.pid_matches dedup. Full auto gate `py -3 tools/run_regression.py
  --tier auto` → GATE PASS.
- **Result**: PASS (unit + auto gate). Live gates (full L1-L6, incl. the
  mandated L3/L4) deferred to the Wave 3 exit gate per the user's locked
  decision.
- **Confidence**: high for unit semantics; live verification at Wave 3 exit.
```

- [ ] **Step 6: Commit**

```bash
git add tools/run_regression.py tests/unit/test_run_regression.py v2_wsl/cc-communicate/server tested&2betest.md
git commit -m "docs(W3/HP-08): auto gate PASS + v2_wsl parity sync + T40 record + L5/L6 live checklists"
```

HP-08 is done. Wave 3 continues with HP-09 (next design), then HP-10, HP-11(余); the full live L1-L6 gate runs at the Wave 3 exit.
