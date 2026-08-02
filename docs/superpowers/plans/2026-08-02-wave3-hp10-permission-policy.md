# HP-10 Spawn Permission Policy + Identity Boundary + Threat Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution for THIS wave (user mandate, durable): INLINE — no context-heavy subagents.** Execute task-by-task in the session (executing-plans style), `py -3 tools/run_regression.py --tier auto` as the exit gate. Live gates (full L1–L6) run at WAVE 3 exit (after HP-10/11).

**Goal:** Flip the spawn permission default to "standard" (D4), wire `permission_mode` end-to-end (it exists on the MCP tool but is dropped today), mark the legacy `create_collaborator` bypass path, and ship the threat-model README.

**Architecture:** `permission_mode` flows MCP tool → user_functions → kernel RPC args → `spawn.py`, which splices `--dangerously-skip-permissions` into the claude argv ONLY for "bypass". New-spawn APIs default "standard"; the resume path (evoke/spawn_cc_resume) defaults "bypass" (documented deviation, R8). The WorkerHandle carries the mode; the legacy wrapper passes bypass explicitly and marks its return strings + kernel log. The README documents the honest threat model.

**Tech Stack:** Python 3 (`py -3`), pytest. No new dependencies.

## Global Constraints

- **`py -3` for ALL Python** on Windows (git-bash; quote paths with spaces/CJK).
- **Tests isolated**: conftest `server` fixture sets `CC_COMMUNICATE_DATA_DIR` → tmp_path and reloads modules per test. `user_functions`/`mcp_server` are importable in tests (module objects are stable; their `validation`/`paths` references resolve the per-test reloaded modules). Use `str(server.paths.DATA_DIR)` as cwd in mcp-entry tests (it exists; `/tmp` may not on Windows Python).
- **Parity**: v2_win ↔ v2_wsl byte-identical outside `.mcp.json`; sync modified files (incl. README.md + SKILL.md) before any parity run (Task 6); verify with `py -3 tools/check_parity.py`.
- **Commit format**: `feat/fix/test/docs(scope): subject` + `Co-Authored-By: Claude <noreply@anthropic.com>`; work on main (user consent).
- **Records**: every bug found during implementation gets a T# entry in `tested&2betest.md` §1.
- **Run from repo root** `C:\研究生\实习\learn AI\projects\cc-communicate`; per-task test: `py -3 -m pytest tests/unit/test_X.py -v`; full: `py -3 -m pytest -q`.
- **Design spec**: `docs/superpowers/specs/2026-08-02-wave3-hp10-permission-policy-design.md` (user-approved). Deviations require user approval.
- **No string parsing for control flow** (Wave-2 standing rule) — the legacy create_collaborator STRING returns are the legacy contract (suffix appended, prefixes byte-exact).

---

### Task 1: `validate_permission_mode` + `spawn.py` permission argv

**Files:**
- Modify: `v2_win/cc-communicate/server/validation.py`
- Modify: `v2_win/cc-communicate/server/spawn.py`
- Create: `tests/unit/test_permission_mode.py`

**Interfaces:**
- Produces: `validation.validate_permission_mode(value) -> str` (only `"standard"`/`"bypass"`); `spawn._permission_argv(mode) -> list` (`"bypass"` → `["--dangerously-skip-permissions"]`, else `[]`); `spawn.spawn_cc_new(cwd, prompt, spawn_token=None, permission_mode="standard")`; `spawn.spawn_cc_resume(session_id, prompt, cwd=None, permission_mode="bypass")`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_permission_mode.py`:

```python
"""HP-10 (D4): permission_mode validation + spawn argv splicing."""
import pytest

import mcp_server
import user_functions
from result import Code


def test_validate_permission_mode(server):
    v = server.validation
    assert v.validate_permission_mode("standard") == "standard"
    assert v.validate_permission_mode("bypass") == "bypass"
    for bad in ("root", "", 42, None):
        with pytest.raises(v.InvalidArgumentError):
            v.validate_permission_mode(bad)


def test_permission_argv(server):
    sp = server.spawn
    assert sp._permission_argv("standard") == []
    assert sp._permission_argv("bypass") == ["--dangerously-skip-permissions"]


def test_spawn_cc_new_default_standard_no_flag(server, monkeypatch):
    captured = {}
    monkeypatch.setattr(server.spawn, "_detached_popen",
                        lambda cmd_args, cwd=None, env=None:
                        captured.update(cmd=cmd_args))
    server.spawn.spawn_cc_new("/tmp", "prompt")
    assert "--dangerously-skip-permissions" not in captured["cmd"]


def test_spawn_cc_new_bypass_has_flag(server, monkeypatch):
    captured = {}
    monkeypatch.setattr(server.spawn, "_detached_popen",
                        lambda cmd_args, cwd=None, env=None:
                        captured.update(cmd=cmd_args))
    server.spawn.spawn_cc_new("/tmp", "prompt", permission_mode="bypass")
    assert "--dangerously-skip-permissions" in captured["cmd"]


def test_spawn_cc_resume_default_bypass_has_flag(server, monkeypatch):
    captured = {}
    monkeypatch.setattr(server.spawn, "_detached_popen",
                        lambda cmd_args, cwd=None, env=None:
                        captured.update(cmd=cmd_args))
    server.spawn.spawn_cc_resume("s1", "prompt", "/tmp")
    assert "--dangerously-skip-permissions" in captured["cmd"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_permission_mode.py -v`
Expected: FAIL — `AttributeError: module 'validation' has no attribute 'validate_permission_mode'`; `spawn_cc_new` TypeError on unexpected kwarg

- [ ] **Step 3: Implement in `validation.py`**

Add after `validate_artifact_refs`:

```python
def validate_permission_mode(value) -> str:
    """HP-10 (D4): spawn permission mode - "standard" (default for NEW
    spawns; the spawned CC makes normal permission decisions) or "bypass"
    (explicit opt-in for unattended automation; skips the trust dialog)."""
    if value not in ("standard", "bypass"):
        raise InvalidArgumentError(
            f"permission_mode must be 'standard' or 'bypass'; got {value!r}")
    return value
```

- [ ] **Step 4: Implement in `spawn.py`**

1. Add after `_child_env`:

```python
def _permission_argv(mode: str) -> list:
    """HP-10 (D4): argv fragment for the spawn's permission mode. "bypass"
    skips the workspace-trust dialog (unattended automation opt-in); the
    "standard" default omits the flag so the spawned CC makes normal
    permission decisions (a trust dialog may appear)."""
    if mode == "bypass":
        return ["--dangerously-skip-permissions"]
    return []
```

2. `spawn_cc_new` (lines ~100-110) — add the param, splice the argv, both branches:

```python
def spawn_cc_new(cwd: str, prompt: str, spawn_token: str = None,
                 permission_mode: str = "standard"):
    """Spawn a NEW interactive CC in cwd (for create_collaborator /
    spawn_collaborator). `claude <prompt>` (no -p) processes the prompt then
    enters the REPL (stays alive). permission_mode (HP-10/D4): "standard"
    default - the spawned CC decides permissions normally; "bypass" adds
    --dangerously-skip-permissions (unattended automation opt-in). cwd is
    set via Popen (T25). spawn_token (HP-04) is injected into the child
    environment so the SessionStart hook can bind the session to its spawn
    request (plan A, D8)."""
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "claude"]
                        + _permission_argv(permission_mode) + [prompt],
                        cwd=cwd, env=_child_env(spawn_token))
    else:
        _tmux_spawn(cwd, [_claude_bin()] + _permission_argv(permission_mode)
                    + [prompt], env_token=spawn_token)
```

3. `spawn_cc_resume` (lines ~118-130) — same pattern, bypass default:

```python
def spawn_cc_resume(session_id: str, prompt: str, cwd: str = None,
                    permission_mode: str = "bypass"):
    """Resume an existing CC session by id (for evoke). Same session_id
    restored. `claude --resume <id> <prompt>` enters the REPL, processes the
    prompt, stays alive. cwd MUST be the session's original cwd (T25).
    permission_mode (HP-10/D4): "bypass" default - resume of an established
    session is not a new trust decision (R8); pass "standard" to override.
    `--resume` restores the conversation, NOT the process cwd, so set cwd
    here explicitly (Popen on Windows, -c on tmux)."""
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "claude", "--resume", session_id]
                        + _permission_argv(permission_mode) + [prompt],
                        cwd=cwd, env=_child_env())
    else:
        _tmux_spawn(cwd or "", [_claude_bin(), "--resume", session_id]
                    + _permission_argv(permission_mode) + [prompt])
```

- [ ] **Step 5: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_permission_mode.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/validation.py v2_win/cc-communicate/server/spawn.py tests/unit/test_permission_mode.py
git commit -m "feat(HP-10): permission_mode validation + spawn argv splicing (standard default, bypass explicit)"
```

---

### Task 2: kernel API + dispatch routing for `permission_mode`

**Files:**
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (`spawn_cc_new`, `spawn_cc_resume`)
- Modify: `v2_win/cc-communicate/server/kernel.py` (`_ARG_VALIDATORS`, `_dispatch`)
- Modify: `tests/unit/test_permission_mode.py`

**Interfaces:**
- Produces: `kernel_api.spawn_cc_new(cwd, prompt, spawn_token=None, permission_mode="standard")`; `kernel_api.spawn_cc_resume(session_id, prompt, cwd=None, permission_mode="bypass")`; dispatch defaults `"standard"`/`"bypass"` when the arg is absent; dispatch validates `permission_mode` for both + `evoke`.

- [ ] **Step 1: Write the failing tests (append to `test_permission_mode.py`)**

```python
def test_dispatch_spawn_cc_new_permission_mode(server, monkeypatch):
    k = server.kernel
    calls = {}
    monkeypatch.setattr(server.spawn, "spawn_cc_new",
                        lambda cwd, prompt, spawn_token=None,
                        permission_mode="standard":
                        calls.update(mode=permission_mode))
    k._dispatch("spawn_cc_new", {"cwd": str(server.paths.DATA_DIR),
                                 "prompt": "p"})
    assert calls["mode"] == "standard"           # D4 default
    k._dispatch("spawn_cc_new", {"cwd": str(server.paths.DATA_DIR),
                                 "prompt": "p", "permission_mode": "bypass"})
    assert calls["mode"] == "bypass"
    with pytest.raises(server.validation.InvalidArgumentError):
        k._dispatch("spawn_cc_new", {"cwd": str(server.paths.DATA_DIR),
                                     "prompt": "p", "permission_mode": "root"})


def test_dispatch_spawn_cc_resume_permission_mode(server, monkeypatch):
    k = server.kernel
    calls = {}
    monkeypatch.setattr(server.spawn, "spawn_cc_resume",
                        lambda sid, prompt, cwd=None, permission_mode="bypass":
                        calls.update(mode=permission_mode))
    k._dispatch("spawn_cc_resume", {"session_id": "s1", "prompt": "p"})
    assert calls["mode"] == "bypass"             # resume default (D4-b)
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_permission_mode.py -v`
Expected: FAIL — dispatch passes no `permission_mode` (TypeError or mode missing)

- [ ] **Step 3: Implement in `kernel_api.py`**

`spawn_cc_new` (lines ~260-275):

```python
def spawn_cc_new(cwd: str, prompt: str, spawn_token: str = None,
                 permission_mode: str = "standard") -> dict:
    """Kernel function for (cross-machine) create_collaborator /
    spawn_collaborator (v2.1 §3.4.6): a peer MCP server calls this via
    call_remote so THIS kernel spawns a local CC (it knows its own claude path
    / spawn mechanism). HP-04: writes pending_spawn/<token>.json BEFORE
    spawning - the marker makes same-token retries safe (no double spawn) and
    is the plan B claim record; the child gets the token via env (plan A).
    HP-10 (D4): permission_mode default "standard"; "bypass" is the explicit
    unattended-automation opt-in (splices --dangerously-skip-permissions)."""
    if spawn_token:
        validation.validate_spawn_token(spawn_token)
        os.makedirs(PENDING_SPAWN_DIR, exist_ok=True)
        fileutil.atomic_write_json(
            os.path.join(PENDING_SPAWN_DIR, spawn_token + ".json"),
            {"schema_version": 1, "spawn_token": spawn_token, "cwd": cwd,
             "created_at_ms": int(time.time() * 1000)})
    spawn.spawn_cc_new(cwd, prompt, spawn_token, permission_mode)
    return {"spawned": True, "spawn_token": spawn_token}
```

`spawn_cc_resume` (lines ~278-280):

```python
def spawn_cc_resume(session_id: str, prompt: str, cwd: str = None,
                    permission_mode: str = "bypass") -> dict:
    spawn.spawn_cc_resume(session_id, prompt, cwd, permission_mode)
    return {"spawned": True, "session_id": session_id}
```

- [ ] **Step 4: Implement in `kernel.py`**

In `_ARG_VALIDATORS`:

```python
    "spawn_cc_new": {"cwd": validation.validate_cwd,
                     "spawn_token": validation.validate_spawn_token,
                     "permission_mode": validation.validate_permission_mode},
    "spawn_cc_resume": {"session_id": validation.validate_session_id,
                        "cwd": validation.validate_cwd,
                        "permission_mode": validation.validate_permission_mode},
    "evoke": {"session_id": validation.validate_session_id,
              "permission_mode": validation.validate_permission_mode},
```

(Replace the existing `spawn_cc_new`/`spawn_cc_resume`/`evoke` entries.)

In `_dispatch`:

```python
    if function == "spawn_cc_new":
        return kernel_api.spawn_cc_new(args["cwd"], args["prompt"],
                                       args.get("spawn_token"),
                                       args.get("permission_mode", "standard"))
    if function == "spawn_cc_resume":
        return kernel_api.spawn_cc_resume(args["session_id"], args["prompt"],
                                          args.get("cwd"),
                                          args.get("permission_mode", "bypass"))
    if function == "evoke":
        return kernel_api.evoke(sessions, args["session_id"],
                                args.get("permission_mode", "bypass"))
```

(Replace the existing three dispatch branches; `evoke`'s new param lands in Task 4's signature — implement the `kernel_api.evoke` signature change now so the dispatch compiles, per Task 4 Step 3.)

- [ ] **Step 5: Implement `kernel_api.evoke` signature now (Task 4's param, needed by dispatch)**

`kernel_api.evoke` (lines ~241-257): add `permission_mode: str = "bypass"` and pass it to `spawn_cc_resume`:

```python
def evoke(sessions: dict, session_id: str, prompt: str = None,
          permission_mode: str = "bypass") -> dict:
    """Revive a CC session by resuming it (core_plan "内核函数 5"). Uses
    `claude --resume <sid> <prompt>` so the SAME session_id is revived. The
    revived CC fires SessionStart -> process_session_ctrl_event updates
    alive_sessions with the new pid. HP-10 (D4): resume defaults to
    permission_mode="bypass" - resuming an established session is not a new
    trust decision (R8); pass "standard" to override. Returns
    {'evoked': True, 'session_id'} or {'evoked': False, 'reason': 'session
    unknown'}."""
    if session_id not in sessions:
        return {"evoked": False, "reason": "session unknown"}
    if prompt is None:
        prompt = ("You have been revived for p2p communication by cc-communicate. "
                  "Call my_session_id to learn your id, then call listen "
                  "(your_id, acked_ts, timeout) - it blocks and returns "
                  "{messages, watermark}. Pass 0 as acked_ts the first time, and "
                  "pass the returned watermark as acked_ts on every later listen "
                  "(the kernel archives only what you've confirmed - never drop "
                  "or duplicate it). Reply to any hello with send_message(your_id, "
                  "peer_id, <message>). KEEP LISTENING: after each listen returns, "
                  "process any messages and call listen again (with the latest "
                  "watermark), in a loop, until you call close_connection(your_id, "
                  "peer_id, your_latest_watermark). If you lose your watermark, "
                  "call query_my_ACK_timestamp(your_id). Never invoke listen.py "
                  "directly or write a shell listener - only use the listen tool.")
    # T25: pass the session's original cwd. `claude --resume <sid>` is cwd-scoped
    # (per-project .jsonl lookup); without the right cwd it runs in the kernel's
    # cwd (data/server/) and fails "No conversation found with session ID: <sid>".
    cwd = sessions.get(session_id, {}).get("cwd")
    spawn.spawn_cc_resume(session_id, prompt, cwd, permission_mode)
    return {"evoked": True, "session_id": session_id}
```

- [ ] **Step 6: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_permission_mode.py tests/unit/test_kernel_structured_returns.py -v`
Expected: PASS (7 + existing; `test_kernel_structured_returns` locks the evoke signature)

- [ ] **Step 7: Commit**

```bash
git add v2_win/cc-communicate/server/kernel_api.py v2_win/cc-communicate/server/kernel.py tests/unit/test_permission_mode.py
git commit -m "feat(HP-10): kernel API + dispatch route permission_mode (spawn_cc_new standard / resume+evoke bypass, D4)"
```

---

### Task 3: user_functions + MCP tool — default flip + pass-through + WorkerHandle field

**Files:**
- Modify: `v2_win/cc-communicate/server/user_functions.py` (`spawn_collaborator`, `_spawn_new`, `_worker_handle`)
- Modify: `v2_win/cc-communicate/server/mcp_server.py` (`spawn_collaborator`)
- Modify: `tests/unit/test_permission_mode.py`

**Interfaces:**
- Produces: `user_functions.spawn_collaborator(caller_sid, cwd, spawn_token=None, machine=None, hold_time=300, permission_mode="standard")`; `_spawn_new` passes `permission_mode` into the `spawn_cc_new` RPC args; `_worker_handle(..., permission_mode)` includes the field; `mcp_server.spawn_collaborator` default `"standard"` + entry validation + pass-through.
- Consumes: Task 1-2 outputs.

- [ ] **Step 1: Write the failing tests (append to `test_permission_mode.py`)**

```python
def test_mcp_spawn_collaborator_default_standard(server, monkeypatch):
    captured = {}
    monkeypatch.setattr(user_functions, "spawn_collaborator",
                        lambda *a, **k: captured.update(kwargs=k) or {
                            "ok": True, "code": None, "message": None,
                            "data": {"session_id": "s1"},
                            "retryable": False})
    r = mcp_server.spawn_collaborator("caller", str(server.paths.DATA_DIR))
    assert r["ok"] is True
    assert captured["kwargs"]["permission_mode"] == "standard"   # D4 flip
    mcp_server.spawn_collaborator("caller", str(server.paths.DATA_DIR),
                                  permission_mode="bypass")
    assert captured["kwargs"]["permission_mode"] == "bypass"


def test_mcp_spawn_collaborator_bad_mode_rejected(server):
    r = mcp_server.spawn_collaborator("caller", str(server.paths.DATA_DIR),
                                      permission_mode="root")
    assert r["ok"] is False and r["code"] == Code.INVALID_ARGUMENT


def test_worker_handle_carries_permission_mode(server):
    server.paths.ensure_runtime_dirs()
    h = user_functions._worker_handle("s1", "t1", "/tmp", None,
                                      permission_mode="bypass")
    assert h["permission_mode"] == "bypass"
    h2 = user_functions._worker_handle("s1", "t1", "/tmp", None,
                                       permission_mode="standard")
    assert h2["permission_mode"] == "standard"
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_permission_mode.py -v`
Expected: FAIL — captured kwargs lack `permission_mode`; `_worker_handle` TypeError

- [ ] **Step 3: Implement in `user_functions.py`**

1. `_spawn_new` (lines 768-775):

```python
def _spawn_new(cwd: str, prompt: str, spawn_token: str, machine: dict = None,
               permission_mode: str = "standard"):
    args = {"cwd": cwd, "prompt": prompt, "spawn_token": spawn_token,
            "permission_mode": permission_mode}
    if machine is None:
        return rpc_client.call("spawn_cc_new", args)
    return rpc_client.call_remote(machine, "spawn_cc_new", args)
```

2. `_worker_handle` (lines 778-784):

```python
def _worker_handle(session_id: str, spawn_token: str, cwd: str,
                   machine: dict = None, permission_mode: str = "standard") -> dict:
    machine_id = (machine or {}).get("id")
    if not machine_id:
        machine_id = machine_identity.load_or_create().get("id")
    return {"session_id": session_id, "machine_id": machine_id, "cwd": cwd,
            "spawn_token": spawn_token, "connection_status": "registered",
            "permission_mode": permission_mode}
```

3. `spawn_collaborator` (lines 787-821) — add the param, thread it through the two handle sites and `_spawn_new`:

```python
def spawn_collaborator(caller_sid: str, cwd: str, spawn_token: str = None,
                       machine: dict = None, hold_time: int = 300,
                       permission_mode: str = "standard") -> dict:
    """Spawn a NEW CC in cwd (on `machine` if given, else local), wait for it
    to register, and return a structured WorkerHandle - NO auto-connect (the
    caller decides when to call connect). spawn_token: caller-supplied (or
    server-generated, returned in the handle); a retry with the SAME token
    returns the original handle instead of spawning again. HP-04.
    permission_mode (HP-10/D4): "standard" default - the spawned CC makes
    normal permission decisions; pass "bypass" for unattended automation
    (skips the trust dialog)."""
    token = spawn_token or uuid.uuid4().hex
    # same-token retry: already registered -> original handle
    try:
        sid = _find_session_by_token(token, machine)
    except KernelError as e:
        return _kernel_err(e)
    if sid:
        return _ok(_worker_handle(sid, token, cwd, machine,
                                  permission_mode=permission_mode))
    # in-flight (pending marker) -> don't re-spawn
    try:
        pending = _has_pending_spawn(token, machine)
    except KernelError as e:
        return _kernel_err(e)
    if pending is None:
        return _remote_err()
    if not pending:
        try:
            r = _spawn_new(cwd, _spawn_prompt(token), token, machine,
                           permission_mode)
        except KernelError as e:
            return _kernel_err(e)
        if r is None:
            return _remote_err()
    # poll for registration (token -> sid; plan A hook event or plan B claim)
    deadline = time.time() + 30
    sid = None
    while time.time() < deadline:
        time.sleep(1)
        try:
            sid = _find_session_by_token(token, machine)
        except KernelError:
            sid = None
        if sid:
            break
    if not sid:
        return _err(Code.TIMEOUT,
                    "new session did not register within 30s (is the plugin "
                    "installed for new CCs?)", retryable=True)
    return _ok(_worker_handle(sid, token, cwd, machine,
                              permission_mode=permission_mode))
```

- [ ] **Step 4: Implement in `mcp_server.py`**

Replace `spawn_collaborator` (lines 280-301):

```python
@mcp.tool()
def spawn_collaborator(caller_sid: str, cwd: str, spawn_token: str = None,
                       permission_mode: str = "standard", machine: dict = None,
                       hold_time: int = 300) -> dict:
    """Spawn a NEW CC in cwd (on `machine` if given - a query_machines entry -
    else this machine) and wait for it to register. Returns the envelope with
    a structured WorkerHandle in data: {session_id, machine_id, cwd,
    spawn_token, connection_status, permission_mode}. Does NOT auto-connect -
    call connect when you want the channel. spawn_token: caller-supplied to
    make retries idempotent (same token -> same handle, no second spawn);
    omitted -> server generates one (returned in the handle).
    permission_mode (HP-10/D4): "standard" DEFAULT - the spawned CC makes
    normal permission decisions (a trust dialog may appear); pass "bypass"
    explicitly for unattended automation (skips the trust dialog)."""
    err = validation.validate_spawn_entry(caller_sid, cwd, machine)
    if err:
        return {"ok": False, "code": Code.INVALID_ARGUMENT,
                "message": err, "data": None, "retryable": False}
    if spawn_token is not None:
        err2 = _entry_error((validation.validate_spawn_token, spawn_token))
        if err2:
            return err2
    err3 = _entry_error((validation.validate_permission_mode, permission_mode))
    if err3:
        return err3
    return user_functions.spawn_collaborator(caller_sid, cwd, spawn_token,
                                             machine, hold_time,
                                             permission_mode=permission_mode)
```

- [ ] **Step 5: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_permission_mode.py tests/unit/test_spawn_collaborator.py tests/unit/test_user_functions_envelope.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/user_functions.py v2_win/cc-communicate/server/mcp_server.py tests/unit/test_permission_mode.py
git commit -m "feat(HP-10): spawn_collaborator default flips to standard (D4) - mode threaded to kernel + WorkerHandle carries it"
```

---

### Task 4: evoke override param (MCP tool + user_functions)

**Files:**
- Modify: `v2_win/cc-communicate/server/mcp_server.py` (`evoke`)
- Modify: `v2_win/cc-communicate/server/user_functions.py` (`evoke`)
- Modify: `tests/unit/test_permission_mode.py`

**Interfaces:**
- Produces: `mcp_server.evoke(session_id, permission_mode="bypass")`; `user_functions.evoke(session_id, permission_mode="bypass")` → RPC args include `permission_mode`. (The kernel side landed in Task 2.)
- Consumes: Task 2's `kernel_api.evoke` signature.

- [ ] **Step 1: Write the failing tests (append to `test_permission_mode.py`)**

```python
def test_evoke_kernel_passes_bypass_default(server, monkeypatch):
    """evoke -> spawn_cc_resume with bypass (D4-b: resume != new trust
    decision); explicit override allowed."""
    ka = server.kernel_api
    calls = {}
    monkeypatch.setattr(server.spawn, "spawn_cc_resume",
                        lambda sid, prompt, cwd=None, permission_mode="bypass":
                        calls.update(mode=permission_mode))
    ka.evoke({"s1": {"cwd": "/tmp"}}, "s1")
    assert calls["mode"] == "bypass"
    ka.evoke({"s1": {"cwd": "/tmp"}}, "s1", permission_mode="standard")
    assert calls["mode"] == "standard"


def test_mcp_evoke_override_param(server, monkeypatch):
    captured = {}
    monkeypatch.setattr(user_functions, "evoke",
                        lambda *a, **k: captured.update(kwargs=k) or {
                            "ok": True, "code": None, "message": None,
                            "data": {"evoked": True},
                            "retryable": False})
    mcp_server.evoke("s1", permission_mode="standard")
    assert captured["kwargs"]["permission_mode"] == "standard"
    r = mcp_server.evoke("s1", permission_mode="root")
    assert r["ok"] is False and r["code"] == Code.INVALID_ARGUMENT
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_permission_mode.py -v`
Expected: FAIL — `mcp_server.evoke` TypeError on unexpected kwarg

- [ ] **Step 3: Implement in `mcp_server.py`**

Replace `evoke` (lines 151-159):

```python
@mcp.tool()
def evoke(session_id: str, permission_mode: str = "bypass") -> dict:
    """Revive a dead CC session on whatever machine it lives on (local or remote
    peer). Returns the envelope: ok({evoked: True, session_id}) or
    err(NOT_FOUND) when the session does not exist. permission_mode
    (HP-10/D4): "bypass" default - resume of an established session is not a
    new trust decision; pass "standard" to override."""
    err = _entry_error((validation.validate_session_id, session_id),
                       (validation.validate_permission_mode, permission_mode))
    if err:
        return err
    return user_functions.evoke(session_id, permission_mode)
```

- [ ] **Step 4: Implement in `user_functions.py`**

Replace `evoke` (lines 394-...):

```python
def evoke(session_id: str, permission_mode: str = "bypass") -> dict:
    """Revive a dead CC on whatever machine it lives on (local or remote)."""
    is_local, machine = _find_target_machine(session_id)
    if not is_local and machine is None:
        return _err(Code.NOT_FOUND, "session not exists")
    try:
        if is_local:
            r = rpc_client.call("evoke",
                                {"session_id": session_id,
                                 "permission_mode": permission_mode})
        else:
            r = rpc_client.call_remote(machine, "evoke",
                                       {"session_id": session_id,
                                        "permission_mode": permission_mode})
    except KernelError as e:
        return _kernel_err(e)
    if r is None:
        return _remote_err()
    if r.get("evoked"):
        return _ok({"evoked": True, "session_id": r.get("session_id")})
    return _err(Code.NOT_FOUND, r.get("reason", "evoke failed"))
```

(Only the signature, docstring, and the two RPC args change — the current result mapping (`NOT_FOUND` tail, `r.get("session_id")`) stays byte-exact.)

- [ ] **Step 5: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_permission_mode.py tests/unit/test_user_functions_envelope.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/mcp_server.py v2_win/cc-communicate/server/user_functions.py tests/unit/test_permission_mode.py
git commit -m "feat(HP-10): evoke permission_mode override param (default bypass per D4-b)"
```

---

### Task 5: legacy create_collaborator marking (D4: return + log)

**Files:**
- Modify: `v2_win/cc-communicate/server/user_functions.py` (`create_collaborator`)
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (bypass log in `spawn_cc_new`)
- Modify: `tests/unit/test_permission_mode.py`, `tests/unit/test_spawn_collaborator.py` (legacy-string assertion)

**Interfaces:**
- Produces: `user_functions.create_collaborator` passes `permission_mode="bypass"` explicitly; all return strings gain the suffix `" ; permission_mode=bypass (legacy)"` (prefixes byte-exact); `kernel_api.spawn_cc_new` logs a line for bypass spawns.
- Consumes: Task 3.

- [ ] **Step 1: Write the failing tests (append to `test_permission_mode.py`)**

```python
def test_create_collaborator_legacy_bypass_marked(server, monkeypatch):
    monkeypatch.setattr(user_functions, "spawn_collaborator",
                        lambda *a, **k: {
                            "ok": True, "code": None, "message": None,
                            "data": {"session_id": "s9", "machine_id": "m1",
                                     "cwd": "/tmp", "spawn_token": "t1",
                                     "connection_status": "registered",
                                     "permission_mode": "bypass"},
                            "retryable": False})
    monkeypatch.setattr(user_functions, "connect",
                        lambda *a, **k: {
                            "ok": True, "code": None, "message": None,
                            "data": {"connection_id": "c1", "reply": "hello bob",
                                     "established_at_ms": 1, "reused": False},
                            "retryable": False})
    s = user_functions.create_collaborator("caller", "/tmp", hold_time=300)
    assert s.endswith(" ; permission_mode=bypass (legacy)")
    assert s.startswith("connect succeed; reply: hello bob")   # prefix intact


def test_bypass_spawn_logged(server, caplog, monkeypatch):
    """D4 '日志标记': bypass spawns leave a durable kernel-log line."""
    import logging
    ka = server.kernel_api
    monkeypatch.setattr(server.spawn, "spawn_cc_new",
                        lambda *a, **kw: None)
    with caplog.at_level(logging.INFO, logger="cc-communicate.kernel"):
        ka.spawn_cc_new("/tmp", "p", spawn_token="t1",
                        permission_mode="bypass")
    assert any("permission_mode=bypass" in r.message for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="cc-communicate.kernel"):
        ka.spawn_cc_new("/tmp", "p", spawn_token="t2",
                        permission_mode="standard")
    assert not any("permission_mode=bypass" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_permission_mode.py -v`
Expected: FAIL — legacy string has no suffix; no log line

- [ ] **Step 3: Implement in `user_functions.py`**

Add the module constant near `_MIN_HOLD_TIME`:

```python
# D4: the legacy create_collaborator predates the standard default - mark
# its bypass mode in the returned string (suffix; prefixes stay byte-exact).
_LEGACY_BYPASS_SUFFIX = " ; permission_mode=bypass (legacy)"
```

Replace `create_collaborator` (lines 870-886):

```python
def create_collaborator(caller_sid: str, cwd: str, hold_time: int = 300,
                        machine=None) -> str:
    """LEGACY wrapper (one release, HP-07): spawn + connect, returns the
    legacy string shape. New code should use spawn_collaborator (structured
    WorkerHandle) + connect. The spawn prompt stays the OLD text so its
    correlation_id-less replies exercise connect's legacy fallback (D9).
    HP-10 (D4): keeps permission_mode="bypass" EXPLICITLY (pre-dates the
    standard default) and marks it in the returned string + kernel log."""
    hold_time = max(hold_time, _MIN_HOLD_TIME)
    res = spawn_collaborator(caller_sid, cwd, spawn_token=None,
                             machine=machine, hold_time=hold_time,
                             permission_mode="bypass")
    if not res["ok"]:
        return "failed, " + str(res.get("message")) + _LEGACY_BYPASS_SUFFIX
    handle = res["data"]
    cr = connect(caller_sid, handle["session_id"], hold_time=hold_time)
    if cr["ok"]:
        reply = (cr["data"] or {}).get("reply")
        base = ("connect succeed; reply: " + reply) if reply else "connect succeed"
        return base + _LEGACY_BYPASS_SUFFIX
    return "connect failed, " + str(cr.get("message")) + _LEGACY_BYPASS_SUFFIX
```

- [ ] **Step 4: Implement the kernel log in `kernel_api.py`**

Add `import logging` to the imports and a module logger next to the constants:

```python
log = logging.getLogger("cc-communicate.kernel")
```

In `spawn_cc_new`, after the marker write, before `spawn.spawn_cc_new(...)`:

```python
    if permission_mode == "bypass":
        log.info("spawn_cc_new permission_mode=bypass (spawn_token=%s)",
                 spawn_token)
```

- [ ] **Step 5: Update the existing legacy-string assertion**

In `tests/unit/test_spawn_collaborator.py` line 118, replace:

```python
    assert s == "connect succeed; reply: hello bob"
```

with:

```python
    assert s == "connect succeed; reply: hello bob ; permission_mode=bypass (legacy)"
```

- [ ] **Step 6: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_permission_mode.py tests/unit/test_spawn_collaborator.py tests/unit/test_user_functions_envelope.py -v`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add v2_win/cc-communicate/server/user_functions.py v2_win/cc-communicate/server/kernel_api.py tests/unit/test_permission_mode.py tests/unit/test_spawn_collaborator.py
git commit -m "feat(HP-10): legacy create_collaborator marks bypass in return + kernel log (D4)"
```

---

### Task 6: README threat model + SKILL.md + parity sync + gate + records

**Files:**
- Create: `v2_win/cc-communicate/README.md`
- Modify: `v2_win/cc-communicate/skills/cc-communicate/SKILL.md`
- Sync: `v2_wsl/cc-communicate/` ← modified server files + README.md + SKILL.md
- Modify: `tested&2betest.md` (T43 record)

**Interfaces:**
- Consumes: all Tasks 1-5 outputs.

- [ ] **Step 1: Create `v2_win/cc-communicate/README.md`**

```markdown
# cc-communicate

A p2p transport for Claude Code sessions: message pipes, connection lifecycle
(connect/listen/close), structured envelopes, and spawn/revive of collaborator
sessions — same machine or Windows-host ↔ WSL2.

## Threat model

This plugin is built for a **trusted single-user, trusted registered peer
realm** and is **NOT safe against a malicious local process with data-dir
access**.

- The data root (`data/`) is plaintext JSON with no authentication: sessions,
  message pipes, connection state, and the operation journal are readable and
  writable by anything that can reach the files.
- Any local process that can write `data/` can impersonate a session, forge
  messages, or poison connection/spawn state — the plugin provides no
  cryptographic authentication.
- Cross-machine peers are trusted by registration (a one-time handshake), not
  by credentials.
- Full authentication is deliberately out of scope until the threat model
  widens; this plugin does not pretend to be authenticated.

What IS enforced: session/message/connection id charset and length (HP-06),
path containment for destructive operations, single-active connection per pair
(HP-05), per-store cursor ACK semantics (HP-02), and the permission_mode
spawn policy below.

## Spawn permission policy (permission_mode)

| mode | meaning |
|---|---|
| `standard` (DEFAULT for new spawns) | The spawned CC makes normal permission decisions — a workspace-trust dialog may appear, and coordinator-driven autonomy requires human approval. |
| `bypass` | Explicit opt-in for unattended automation. Splices `--dangerously-skip-permissions`; the spawned CC runs fully autonomous. The legacy `create_collaborator` wrapper and the resume path (evoke) are bypass. |

A spawned `standard`-mode CC may stall at the trust dialog until a human
approves — that is the designed cost of the secure default.

## Configuration (env vars)

`CC_COMMUNICATE_DATA_DIR`, `CC_COMMUNICATE_MAX_INLINE_BYTES`,
`CC_COMMUNICATE_MAX_ARTIFACT_REFS`, `CC_COMMUNICATE_MAX_BACKLOG`,
`CC_COMMUNICATE_PENDING_SPAWN_TTL_SECONDS`, `CC_MONITOR_IDLE_TIMEOUT`,
`CC_COMMUNICATE_MAX_INLINE_BYTES` (documented per-feature in SKILL.md).
```

- [ ] **Step 2: Update SKILL.md**

1. Line 127-131 (playbook step 6), replace:

```markdown
6. **Spawn a collaborator** - `spawn_collaborator(sid, cwd)` starts a NEW CC
   in `cwd` and returns a structured WorkerHandle (it does NOT auto-connect -
   call `connect` when you want the channel). Pass `machine=<entry>` (from
   `query_machines`) to spawn on a registered peer machine. The new CC must
   have the plugin installed to be discoverable.
```

with:

```markdown
6. **Spawn a collaborator** - `spawn_collaborator(sid, cwd)` starts a NEW CC
   in `cwd` and returns a structured WorkerHandle (it does NOT auto-connect -
   call `connect` when you want the channel). Pass `machine=<entry>` (from
   `query_machines`) to spawn on a registered peer machine. The new CC must
   have the plugin installed to be discoverable. The worker runs in
   `permission_mode="standard"` by default (HP-10/D4): it makes normal
   permission decisions and a trust dialog may appear; pass
   `permission_mode="bypass"` explicitly for unattended automation.
```

2. Lines 135-151 (worker playbook signature), replace:

```markdown
`spawn_collaborator(caller_sid, cwd, spawn_token=None, permission_mode="bypass",
machine=None, hold_time=300)` starts a new CC in `cwd` and waits for it to
register (up to 30s). Returns the envelope with `data` = the WorkerHandle:

```
{session_id, machine_id, cwd, spawn_token, connection_status}
```
```

with:

```markdown
`spawn_collaborator(caller_sid, cwd, spawn_token=None, permission_mode="standard",
machine=None, hold_time=300)` starts a new CC in `cwd` and waits for it to
register (up to 30s). Returns the envelope with `data` = the WorkerHandle:

```
{session_id, machine_id, cwd, spawn_token, connection_status, permission_mode}
```
```

3. Replace the `permission_mode` bullet (lines 149-151):

```markdown
- `permission_mode` is accepted now (default `"bypass"` = current behavior;
  Wave 3 HP-10 flips the default to `"standard"` - the parameter surface
  never changes).
```

with:

```markdown
- `permission_mode` (HP-10/D4): `"standard"` DEFAULT - the spawned CC makes
  normal permission decisions (a trust dialog may appear; unattended
  automation must pass `"bypass"` explicitly). The legacy
  `create_collaborator` and the resume path (`evoke`) are bypass. Threat
  model: see the plugin README.
```

4. Add an `evoke` note where the revive tool is documented (the working
   playbook's revive section): append to the evoke bullet — `evoke`
   resumes with `permission_mode="bypass"` by default (resume of an
   established session is not a new trust decision); pass `"standard"` to
   override.

- [ ] **Step 3: Sync v2_wsl + full suite + parity**

```bash
cp v2_win/cc-communicate/server/{kernel,kernel_api,mcp_server,spawn,user_functions,validation}.py v2_wsl/cc-communicate/server/
cp v2_win/cc-communicate/README.md v2_wsl/cc-communicate/README.md
cp v2_win/cc-communicate/skills/cc-communicate/SKILL.md v2_wsl/cc-communicate/skills/cc-communicate/SKILL.md
py -3 -m pytest -q
py -3 tools/check_parity.py
```

Expected: full suite PASS; `PARITY OK (31 files compared, allowlist=['.mcp.json'])` (README.md joins the compared set)

- [ ] **Step 4: Run the full auto gate**

Run: `py -3 tools/run_regression.py --tier auto`
Expected: `GATE PASS`

- [ ] **Step 5: Record T43 in `tested&2betest.md` §1**

Append:

```markdown
### T43 — HP-10 unit acceptance: permission default flip (D4) + legacy marking + threat-model README

- **Method**: unit (test_permission_mode.py): validate_permission_mode
  matrix; spawn argv splicing (_permission_argv; spawn_cc_new default
  standard has NO --dangerously-skip-permissions, bypass has it;
  spawn_cc_resume default bypass); kernel dispatch routes permission_mode
  (spawn_cc_new standard / resume+evoke bypass); mcp_server
  spawn_collaborator default standard + pass-through + entry validation;
  WorkerHandle carries permission_mode; evoke override param;
  create_collaborator legacy suffix + explicit bypass; kernel log line for
  bypass spawns (caplog). README.md (plugin root) ships the D4 threat model.
  Full auto gate `py -3 tools/run_regression.py --tier auto` -> GATE PASS.
- **Result**: PASS (unit + auto gate; parity OK 31 files). Live gates (full
  L1-L6, incl. the mandated L3/L4) deferred to the Wave 3 exit gate per the
  user's locked decision.
- **Confidence**: high for unit semantics; live verification at Wave 3 exit.
```

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/README.md v2_win/cc-communicate/skills/cc-communicate/SKILL.md v2_wsl/cc-communicate tested&2betest.md
git commit -m "docs(W3/HP-10): threat-model README + SKILL.md permission_mode docs, parity sync, auto gate PASS, T43 record"
```

HP-10 is done. Wave 3 continues with HP-11(余) (next design), then the full live L1-L6 gate at the Wave 3 exit.
