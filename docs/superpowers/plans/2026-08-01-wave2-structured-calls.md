# Wave 2 — Parallel Workers + Structured Calls: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Wave 2 of the hardening program: a structured Result/Error envelope on every tool (HP-07), spawn_token + WorkerHandle for race-free same-cwd spawns (HP-04), and connection_id + correlation_id-matched handshake (HP-05).

**Architecture:** The envelope is the MCP API contract only — `user_functions.py` builds `{ok, code, message, data, retryable}` via `result.ok()/err()`, `mcp_server.py` passes through, the kernel stays envelope-free but its string returns become structured dicts. One token→sid map in the kernel resolves spawns (env-injected `CC_COMMUNICATE_SPAWN_TOKEN` via SessionStart hook = plan A; `pending_spawn/<token>.json` claimed by the worker's first tool call = plan B; D8 probe decides). One `info.json` per conversation dir enforces a single active `connection_id` per pair (D9).

**Tech Stack:** Python 3 (Windows `py -3`), FastMCP, Node hooks (registrar.js), pytest, PowerShell/CIM + /proc introspection.

## Global Constraints

- **`py -3` for ALL Python** on Windows (python/python3 are broken Store stubs). Subagent subprocesses inherit this.
- **Parity**: `v2_win` ↔ `v2_wsl` byte-identical outside `.mcp.json`. Tasks 1–9 edit `v2_win` only; Task 10 mirrors everything to `v2_wsl` and runs `py -3 tools/check_parity.py`.
- **Tests isolated**: every test uses the `server` fixture (tests/conftest.py) — sets `CC_COMMUNICATE_DATA_DIR` → tmp_path and reloads modules. Never write outside tmp_path.
- **T# records**: every bug found while implementing is recorded as `T#` in `tested&2betest.md` §1 (Method/Result/Confidence) — do not skip this.
- **Commit convention**: `feat/fix/test/docs(scope): subject` + `Co-Authored-By: Claude <noreply@anthropic.com>` trailer.
- **Repo path has spaces + CJK** (`C:\研究生\实习\learn AI\projects\cc-communicate`): quote every path in shell commands.
- **Concurrency discipline**: never run two kernels on one data root; the kernel is single-threaded by design.
- **Do NOT restructure** files beyond what tasks specify; match the existing comment density and idiom (Chinese docstrings are the norm).
- **The envelope rule**: `user_functions.py` branches ONLY on dict fields — no `"failed" in str(...)`, no `str.startswith(...)` control flow. Task 4's grep test enforces this.

---

### Task 1: result.py — full envelope v2

**Files:**
- Modify: `v2_win/cc-communicate/server/result.py`
- Test: `tests/unit/test_result_envelope.py` (new)

**Interfaces:**
- Produces: `Code.NOT_ALIVE`; `ok(data=None) -> {ok, code, message, data, retryable}`; `err(code, message, data=None, retryable=False) -> {ok, code, message, data, retryable}`. All five keys ALWAYS present.

- [ ] **Step 1: Write the failing test**

```python
"""HP-07: envelope v2 - uniform 5-field shape, NOT_ALIVE code, retryable."""
import pytest

from result import Code, ok, err


def test_ok_shape(server):
    r = ok({"a": 1})
    assert r == {"ok": True, "code": None, "message": None,
                 "data": {"a": 1}, "retryable": False}


def test_ok_none_data(server):
    assert ok()["data"] is None and ok()["ok"] is True


def test_err_shape(server):
    r = err(Code.TIMEOUT, "no reply", data={"conn": "x"}, retryable=True)
    assert r == {"ok": False, "code": Code.TIMEOUT, "message": "no reply",
                 "data": {"conn": "x"}, "retryable": True}


def test_err_defaults(server):
    r = err(Code.NOT_FOUND, "gone")
    assert r["code"] == Code.NOT_FOUND and r["data"] is None
    assert r["retryable"] is False


def test_not_alive_code(server):
    assert Code.NOT_ALIVE == "NOT_ALIVE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/unit/test_result_envelope.py -v`
Expected: FAIL — `ok()` lacks `message`/`retryable` keys; `Code.NOT_ALIVE` missing.

- [ ] **Step 3: Implement**

```python
"""Structured result/error codes + response envelope (D7 / HP-07).

The envelope is the MCP API contract: every tool returns
{ok, code, message, data, retryable} built by ok()/err(). code is None on
success; retryable=True only for transient failures where the caller should
retry the same operation. The kernel does NOT use envelopes - it returns raw
structured dicts and user_functions wraps them.
"""
from __future__ import annotations


class Code:
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    PEER_UNREACHABLE = "PEER_UNREACHABLE"
    TIMEOUT = "TIMEOUT"
    CONFLICT = "CONFLICT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    NOT_ALIVE = "NOT_ALIVE"
    INTERNAL = "INTERNAL"


def ok(data=None) -> dict:
    return {"ok": True, "code": None, "message": None,
            "data": data, "retryable": False}


def err(code: str, message: str, data=None, retryable: bool = False) -> dict:
    return {"ok": False, "code": code, "message": message,
            "data": data, "retryable": retryable}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/unit/test_result_envelope.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add v2_win/cc-communicate/server/result.py tests/unit/test_result_envelope.py
git commit -m "feat(HP-07): envelope v2 - uniform 5-field ok/err, NOT_ALIVE code

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Kernel structured returns (kill string returns)

**Files:**
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (send_message, register_conversation, unregister_conversation, withdraw, evoke, create_conversation_folder, kernel_terminate, spawn_cc_resume)
- Modify: `v2_win/cc-communicate/server/kernel.py` (dispatch returns for register/unregister/create_conversation_folder; `_JOURNALED_FUNCTIONS` unchanged)
- Modify: `v2_win/cc-communicate/server/user_functions.py` (BRIDGE: connect's hello_ts line accepts both shapes — removed in Task 5)
- Modify: `tests/unit/test_message_record.py`, `tests/unit/test_message_roundtrip.py`, `tests/unit/test_legacy_format_lock.py`
- Test: `tests/unit/test_kernel_structured_returns.py` (new)

**Interfaces:**
- Consumes: Task 1 envelope (not used in kernel — kernel stays envelope-free).
- Produces: kernel send_message → `{"sent": True, "message_id", "ts", "correlation_id"}` or `{"sent": False, "reason": "connection not registered"}`; withdraw → `{"withdrawn": True, "detail"}` / `{"withdrawn": False, "reason"}`; evoke → `{"evoked": True, "session_id"}` / `{"evoked": False, "reason": "session unknown"}`; register/unregister/create_conversation_folder → `{"ok": True}`; kernel_terminate → `{"terminated": True}`; spawn_cc_resume → `{"spawned": True, "session_id"}`.

- [ ] **Step 1: Write the failing test**

```python
"""HP-07: kernel returns are structured dicts - no string results for control flow."""
from result import Code  # noqa: F401 - keep import for parity of thought


def _seq_state():
    return {"schema_version": 1, "store_id": "store-test", "last_allocated": 0}


def test_send_message_structured(server):
    ka = server.kernel_api
    convs = {}
    ka.register_conversation(convs, "alice", "bob")
    r = ka.send_message(convs, _seq_state(), "store-test", "alice", "bob", "hello")
    assert r["sent"] is True
    assert isinstance(r["ts"], int) and r["message_id"]
    assert r["correlation_id"] is None


def test_send_unregistered_structured(server):
    ka = server.kernel_api
    r = ka.send_message({}, _seq_state(), "store-test", "alice", "bob", "hi")
    assert r == {"sent": False, "reason": "connection not registered"}


def test_withdraw_structured(server):
    ka = server.kernel_api
    convs = {}
    ka.register_conversation(convs, "alice", "bob")
    assert ka.withdraw(convs, "alice", "bob", init_connect=1) == \
        {"withdrawn": True, "detail": "conversation withdrawn"}
    r = ka.withdraw(convs, "alice", "bob", init_connect=1)
    assert r["withdrawn"] is False


def test_evoke_structured(server):
    ka = server.kernel_api
    sessions = {"s1": {"cwd": "/tmp", "session_id": "s1"}}
    assert ka.evoke({}, "nope") == {"evoked": False, "reason": "session unknown"}


def test_register_unregister_structured(server):
    ka = server.kernel_api
    convs = {}
    assert ka.register_conversation(convs, "alice", "bob") == {"ok": True}
    assert (("alice", "bob") in convs)
    assert ka.unregister_conversation(convs, "alice", "bob") == {"ok": True}
    assert (("alice", "bob") not in convs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/unit/test_kernel_structured_returns.py -v`
Expected: FAIL — kernel still returns strings (`"message_sent at ..."`, `"failed, connection not registered"`, `"conversation withdrawn"`, `"evoke spawned (resumed)"`, `"ok"`).

- [ ] **Step 3: Implement** — kernel_api.py edits:

In `send_message`, replace the signature and both return paths:

```python
def send_message(alive_conversations: dict, message_sequence: dict, store_id: str,
                 fromid: str, toid: str, message: str, message_id: str = None,
                 kind: str = None, correlation_id: str = None) -> dict:
    """HP-01: allocate a per-store sequence, wrap the text in a v1 record,
    atomically publish. HP-03 dedup: a retry carrying the same message_id
    returns the ORIGINAL result without publishing a duplicate. Structured
    dict result (HP-07) - callers branch on 'sent', never on text."""
    a, b = sorted([fromid, toid])
    if (a, b) not in alive_conversations:
        return {"sent": False, "reason": "connection not registered"}
    d = conversations.ensure_conv_dir(fromid, toid)
    if message_id:
        found = _find_message_file(d, message_id)
        if found:
            rec = message_record.read_record(found)
            ts = rec.get("created_at_ms", 0) if rec else 0
            return {"sent": True, "message_id": message_id, "ts": ts,
                    "correlation_id": rec.get("correlation_id") if rec else None}
    seq = int(message_sequence.get("last_allocated", 0)) + 1
    message_sequence["last_allocated"] = seq
    message_sequence["store_id"] = store_id
    fileutil.atomic_write_json(MESSAGE_SEQUENCE_FILE, message_sequence)
    rec = message_record.new_record(store_id, seq, fromid, toid, message,
                                    message_id=message_id, kind=kind,
                                    correlation_id=correlation_id)
    message_record.publish(d, rec)
    return {"sent": True, "message_id": rec["message_id"], "ts": rec["created_at_ms"],
            "correlation_id": correlation_id}
```

`register_conversation` / `unregister_conversation` / `create_conversation_folder` — append `return {"ok": True}` and return it:

```python
def register_conversation(alive_conversations: dict, sid_a: str, sid_b: str) -> dict:
    a, b = sorted([sid_a, sid_b])
    alive_conversations[(a, b)] = {"established_at": time.time()}
    return {"ok": True}
```
```python
def unregister_conversation(alive_conversations: dict, sid_a: str, sid_b: str) -> dict:
    a, b = sorted([sid_a, sid_b])
    alive_conversations.pop((a, b), None)
    return {"ok": True}
```
```python
def create_conversation_folder(id1: str, id2: str) -> dict:
    conversations.ensure_conv_dir(id1, id2)
    return {"ok": True}
```

`withdraw` — replace every string return:

```python
def withdraw(alive_conversations: dict, fromid: str, toid: str, init_connect: int = 0,
             message_id: str = None) -> dict:
    if init_connect:
        # HP-06 destructive containment: conv_dir already validated both ids;
        # re-verify the resolved target is strictly under CONVERSATIONS_DIR and
        # IS the canonical pair dir before rmtree.
        d = conversations.conv_dir(fromid, toid)
        target = validation.resolve_under(CONVERSATIONS_DIR, os.path.basename(d))
        if os.path.isdir(target):
            shutil.rmtree(target)
        unregister_conversation(alive_conversations, fromid, toid)
        return {"withdrawn": True, "detail": "conversation withdrawn"}
    if message_id:
        # HP-03: withdraw an EXPLICIT target (retry-safe). The legacy
        # latest-message mode below is non-idempotent by nature and remains
        # for one release only.
        validation.validate_message_id(message_id)
        d = conversations.conv_dir(fromid, toid)
        found = _find_message_file(d, message_id)
        if not found or os.sep + "log" + os.sep in found:
            return {"withdrawn": False,
                    "reason": f"no message {message_id} (already withdrawn or never existed)"}
        try:
            os.remove(found)
        except OSError:
            return {"withdrawn": False,
                    "reason": f"no message {message_id} (already withdrawn or never existed)"}
        return {"withdrawn": True, "detail": f"withdrew message {message_id}",
                "message_id": message_id}
    d = conversations.conv_dir(fromid, toid)
    pipe = os.path.join(d, "pipe")
    try:
        files = os.listdir(pipe)
    except FileNotFoundError:
        return {"withdrawn": False, "reason": "no messages"}
    candidates = []
    for f in files:
        info = conversations.parse_any_pipe_filename(f)
        if not info or info["from_id"] != fromid:
            continue
        # "latest": records order by sequence, legacy by ts; records always
        # postdate legacy, so rank records above any legacy.
        key = (1, info["sequence"]) if info["format"] == "record" else (0, info["ts"])
        candidates.append((key, f))
    if not candidates:
        return {"withdrawn": False, "reason": f"no messages from {fromid}"}
    candidates.sort(key=lambda x: x[0])
    os.remove(os.path.join(pipe, candidates[-1][1]))
    return {"withdrawn": True, "detail": f"withdrew latest message from {fromid}"}
```

`evoke` — return dict (keep the existing default prompt text verbatim):

```python
def evoke(sessions: dict, session_id: str, prompt: str = None) -> dict:
    """Revive a CC session by resuming it (core_plan "内核函数 5"). Uses
    `claude --resume <sid> <prompt>` so the SAME session_id is revived. The
    revived CC fires SessionStart -> process_session_ctrl_event updates
    alive_sessions with the new pid. Returns {'evoked': True, 'session_id'}
    or {'evoked': False, 'reason': 'session unknown'}."""
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
    spawn.spawn_cc_resume(session_id, prompt, cwd)
    return {"evoked": True, "session_id": session_id}
```

`kernel_terminate` and `spawn_cc_resume`:

```python
def kernel_terminate() -> dict:
    """Request the kernel to exit on its next loop iteration (v2.1 §3.5.3).
    Writes a flag file the kernel loop polls. (The kernel runs as __main__, so
    `import kernel; kernel._exit_requested=True` would touch a DIFFERENT module
    object - the flag file sidesteps that.)"""
    from paths import TERMINATE_FLAG, SERVER_DATA_DIR
    try:
        os.makedirs(SERVER_DATA_DIR, exist_ok=True)
        open(TERMINATE_FLAG, "w").close()
        return {"terminated": True}
    except OSError as e:
        return {"terminated": False, "reason": str(e)}
```
```python
def spawn_cc_resume(session_id: str, prompt: str, cwd: str = None) -> dict:
    spawn.spawn_cc_resume(session_id, prompt, cwd)
    return {"spawned": True, "session_id": session_id}
```

kernel.py `_dispatch` — remove the literal `"ok"` returns (kernel_api now returns dicts):

```python
    if function == "register_conversation":
        return kernel_api.register_conversation(alive_conversations, args["sid_a"], args["sid_b"])
    if function == "unregister_conversation":
        return kernel_api.unregister_conversation(alive_conversations, args["sid_a"], args["sid_b"])
```
```python
    if function == "create_conversation_folder":
        return kernel_api.create_conversation_folder(args["id1"], args["id2"])
```

**BRIDGE** in user_functions.py `connect` (step 4.6 of connect, the hello_ts parse — replaced permanently in Task 5):

```python
    try:
        if isinstance(send_res, dict):
            hello_ts = send_res.get("ts") or 0
        else:  # BRIDGE (removed in Task 5): legacy kernel string
            hello_ts = int(str(send_res).rsplit("at ", 1)[1])
    except (ValueError, IndexError, TypeError):
        hello_ts = 0
```

- [ ] **Step 4: Update the three legacy-string test files**

`tests/unit/test_message_record.py:32`:
```python
        r = _send(ka, convs, seq, "alice", "bob", f"m{i}")
        assert r["sent"] is True and isinstance(r["ts"], int)
```

`tests/unit/test_message_roundtrip.py:13`:
```python
    r = ka.send_message(convs, _seq_state(), "store-test", "alice", "bob", "hello")
    assert r["sent"] is True and r["ts"] > 0
```

`tests/unit/test_message_roundtrip.py:35`:
```python
    r = ka.send_message(convs, _seq_state(), "store-test", "alice", "bob", "hi")
    assert r == {"sent": False, "reason": "connection not registered"}
```

`tests/unit/test_legacy_format_lock.py:19`:
```python
    r = ka.send_message(convs, _seq_state(), "store-test", "alice", "bob", "hello")
    assert r["sent"] is True
```

- [ ] **Step 5: Run full suite**

Run: `py -3 -m pytest -q`
Expected: ALL PASS (the only legacy-string assertions were in the four updated spots; `test_send_dedup_by_message_id`'s `r1 == r2` still holds — both returns are now identical dicts).

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/kernel_api.py v2_win/cc-communicate/server/kernel.py v2_win/cc-communicate/server/user_functions.py tests/unit/test_message_record.py tests/unit/test_message_roundtrip.py tests/unit/test_legacy_format_lock.py tests/unit/test_kernel_structured_returns.py
git commit -m "feat(HP-07): kernel structured returns - no string results for control flow

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: conversations.info_path + kernel connection functions

**Files:**
- Modify: `v2_win/cc-communicate/server/conversations.py`
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (add activate_connection / get_connection_info / deactivate_connection)
- Modify: `v2_win/cc-communicate/server/validation.py` (add validate_connection_id)
- Modify: `v2_win/cc-communicate/server/kernel.py` (_ARG_VALIDATORS + _dispatch + _JOURNALED_FUNCTIONS)
- Test: `tests/unit/test_connection_info.py` (new)

**Interfaces:**
- Consumes: Task 2 structured returns.
- Produces: `conversations.info_path(sid_a, sid_b) -> str`; kernel `activate_connection(alive_conversations, sid_a, sid_b, connection_id) -> {"activated": bool, "connection_id", "reused": bool, "established_at_ms"}` (conflict → `{"activated": False, "reason": "conflict", "current_connection_id": str}`); `get_connection_info(sid_a, sid_b) -> dict|None`; `deactivate_connection(alive_conversations, sid_a, sid_b) -> {"closed": True}`; `validation.validate_connection_id(value) -> str` (same charset rule as session_id).

- [ ] **Step 1: Write the failing test**

```python
"""HP-05 (D9): info.json lifecycle - activate / get / deactivate / conflict."""
import json
import os


def _conn_id(n):
    return f"c{n:031d}"  # fits the [A-Za-z0-9-] charset, 32 chars


def test_activate_writes_info_json(server):
    ka = server.kernel_api
    convs = {}
    r = ka.activate_connection(convs, "alice", "bob", _conn_id(1))
    assert r["activated"] is True and r["reused"] is False
    assert ("alice", "bob") in convs  # registered
    info = ka.get_connection_info("alice", "bob")
    assert info["connection_id"] == _conn_id(1)
    assert info["status"] == "active" and info["schema_version"] == 1
    assert info["sid_a"] == "alice" and info["sid_b"] == "bob"
    assert isinstance(info["established_at_ms"], int)
    # order-independent path
    assert ka.get_connection_info("bob", "alice") == info


def test_activate_same_id_reuses(server):
    ka = server.kernel_api
    convs = {}
    ka.activate_connection(convs, "alice", "bob", _conn_id(1))
    r = ka.activate_connection(convs, "alice", "bob", _conn_id(1))
    assert r["activated"] is True and r["reused"] is True


def test_activate_conflict_different_id(server):
    ka = server.kernel_api
    convs = {}
    ka.activate_connection(convs, "alice", "bob", _conn_id(1))
    r = ka.activate_connection(convs, "alice", "bob", _conn_id(2))
    assert r["activated"] is False and r["reason"] == "conflict"
    assert r["current_connection_id"] == _conn_id(1)
    # no double registration / no file overwrite
    assert ka.get_connection_info("alice", "bob")["connection_id"] == _conn_id(1)


def test_deactivate_marks_closed(server):
    ka = server.kernel_api
    convs = {}
    ka.activate_connection(convs, "alice", "bob", _conn_id(1))
    r = ka.deactivate_connection(convs, "alice", "bob")
    assert r == {"closed": True}
    assert ("alice", "bob") not in convs  # unregistered
    info = ka.get_connection_info("alice", "bob")
    assert info["status"] == "closed" and info["connection_id"] == _conn_id(1)
    assert isinstance(info.get("closed_at_ms"), int)


def test_get_connection_info_absent(server):
    ka = server.kernel_api
    assert ka.get_connection_info("alice", "bob") is None


def test_info_path_validates(server):
    d = server.conversations.info_path("alice", "bob")
    assert d.endswith("info.json")
    assert os.path.dirname(d) == server.conversations.conv_dir("alice", "bob")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/unit/test_connection_info.py -v`
Expected: FAIL — `info_path`, `activate_connection`, `validate_connection_id` don't exist.

- [ ] **Step 3: Implement**

conversations.py (append):

```python
def info_path(sid_a: str, sid_b: str) -> str:
    """Path of the conversation's info.json (connection metadata, HP-05)."""
    return os.path.join(conv_dir(sid_a, sid_b), "info.json")
```

validation.py (append):

```python
def validate_connection_id(value) -> str:
    """connection_id: uuid4 hex or any id-charset token (same rule as
    message_id - it doubles as a correlation key)."""
    return _check_id(value, "connection_id")
```

kernel_api.py (append after `create_conversation_folder`, before the control section):

```python
# ---------- connection metadata (HP-05 / D9) ----------
# info.json is the single-active-connection authority: written by
# activate_connection (enforced kernel-side - the kernel is the only writer),
# read by get_connection_info, closed by deactivate_connection. A retry with
# the SAME connection_id reuses; a different id while active is a CONFLICT.

def get_connection_info(sid_a: str, sid_b: str):
    """info.json for the pair, or None when absent/malformed."""
    try:
        with open(conversations.info_path(sid_a, sid_b), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def activate_connection(alive_conversations: dict, sid_a: str, sid_b: str,
                        connection_id: str) -> dict:
    """Register the pair + write info.json (status=active). Same connection_id
    retry -> reuse (no-op). Different active connection_id -> conflict."""
    validation.validate_connection_id(connection_id)
    a, b = sorted([sid_a, sid_b])
    existing = get_connection_info(a, b)
    if existing and existing.get("status") == "active":
        if existing.get("connection_id") == connection_id:
            return {"activated": True, "connection_id": connection_id,
                    "reused": True, "established_at_ms": existing.get("established_at_ms")}
        return {"activated": False, "reason": "conflict",
                "current_connection_id": existing.get("connection_id")}
    register_conversation(alive_conversations, a, b)
    info = {
        "schema_version": 1,
        "connection_id": connection_id,
        "status": "active",
        "established_at_ms": int(time.time() * 1000),
        "sid_a": a,
        "sid_b": b,
    }
    os.makedirs(conversations.conv_dir(a, b), exist_ok=True)
    fileutil.atomic_write_json(conversations.info_path(a, b), info)
    return {"activated": True, "connection_id": connection_id, "reused": False,
            "established_at_ms": info["established_at_ms"]}


def deactivate_connection(alive_conversations: dict, sid_a: str, sid_b: str) -> dict:
    """Unregister + mark info.json status=closed (close_connection, HP-05)."""
    a, b = sorted([sid_a, sid_b])
    unregister_conversation(alive_conversations, a, b)
    info = get_connection_info(a, b)
    if info:
        info["status"] = "closed"
        info["closed_at_ms"] = int(time.time() * 1000)
        fileutil.atomic_write_json(conversations.info_path(a, b), info)
    return {"closed": True}
```

kernel.py — `_ARG_VALIDATORS` additions:

```python
    "activate_connection": {"connection_id": validation.validate_connection_id},
    "deactivate_connection": {},
    "get_connection_info": {},
```

kernel.py — `_dispatch` additions (place near register_conversation):

```python
    if function == "activate_connection":
        return kernel_api.activate_connection(
            alive_conversations, args["sid_a"], args["sid_b"], args["connection_id"])
    if function == "get_connection_info":
        return kernel_api.get_connection_info(args["sid_a"], args["sid_b"])
    if function == "deactivate_connection":
        return kernel_api.deactivate_connection(alive_conversations, args["sid_a"], args["sid_b"])
```

kernel.py — `_JOURNALED_FUNCTIONS` additions:

```python
    "activate_connection", "deactivate_connection",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/unit/test_connection_info.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add v2_win/cc-communicate/server/conversations.py v2_win/cc-communicate/server/kernel_api.py v2_win/cc-communicate/server/validation.py v2_win/cc-communicate/server/kernel.py tests/unit/test_connection_info.py
git commit -m "feat(HP-05): info.json connection metadata - activate/get/deactivate + conflict (D9)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: user_functions + mcp_server envelope migration

**Files:**
- Modify: `v2_win/cc-communicate/server/user_functions.py` (all tools EXCEPT connect / close_connection / create_collaborator — those stay legacy until Tasks 5/6/9)
- Modify: `v2_win/cc-communicate/server/mcp_server.py` (_entry_error → envelope; tools pass through)
- Test: `tests/unit/test_user_functions_envelope.py` (new, incl. the no-string-parsing grep test)

**Interfaces:**
- Consumes: Task 1 envelope (`result.ok/err`, `Code`), Task 2 structured kernel returns.
- Produces: every migrated tool returns `result.ok(data)` / `result.err(code, message, ...)`. Helpers: `_kernel_err(e)` (KernelError → `err(INTERNAL, str(e))`), `_remote_err()` (`err(PEER_UNREACHABLE, "peer machine unreachable")`).

- [ ] **Step 1: Write the failing test**

```python
"""HP-07: every migrated tool returns the envelope; user_functions parses no strings."""
import inspect

import pytest
from result import Code

from rpc_client import KernelError


def _fake_kernel(server, table):
    """Monkeypatch rpc_client.call to dispatch against an in-memory table of
    kernel functions (dicts). call_remote returns None (no peers)."""
    import rpc_client as rc
    def call(fn, args=None, timeout=30.0, operation_id=None):
        args = args or {}
        if fn not in table:
            raise KernelError(f"unknown kernel function: {fn}")
        return table[fn](args)
    monkeypatch = server._m
    monkeypatch.setattr(rc, "call", call)
    monkeypatch.setattr(rc, "call_remote", lambda *a, **k: None)


def test_my_session_id_ok(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"session_by_pid": lambda a: "s1"})
    monkeypatch.setattr(user_functions.os, "getpid", lambda: 123)
    monkeypatch.setattr("proc.resolve_claude", lambda pid: (55, "t"))
    r = user_functions.my_session_id()
    assert r["ok"] is True and r["data"] == "s1"


def test_check_alive_envelope(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"check_alive": lambda a: 1})
    r = user_functions.check_alive("s1")
    assert r == {"ok": True, "code": None, "message": None,
                 "data": 1, "retryable": False}


def test_send_message_envelope(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"send_message": lambda a: {"sent": True,
                     "message_id": "m", "ts": 42, "correlation_id": None},
                     "query_session": lambda a: True})
    r = user_functions.send_message("a", "b", "hi")
    assert r["ok"] is True and r["data"] == {"message_id": "m", "ts": 42}


def test_send_message_not_registered_code(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"send_message": lambda a: {"sent": False,
                     "reason": "connection not registered"},
                     "query_session": lambda a: True})
    r = user_functions.send_message("a", "b", "hi")
    assert r["ok"] is False and r["code"] == Code.NOT_FOUND
    assert r["retryable"] is False


def test_evoke_envelope(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"evoke": lambda a: {"evoked": True, "session_id": "s1"},
                     "query_session": lambda a: "s1"})
    r = user_functions.evoke("s1")
    assert r["ok"] is True and r["data"] == {"evoked": True, "session_id": "s1"}


def test_listen_wrapped_ok(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"listen_scan": lambda a: {"messages": [], "watermark": 0}})
    monkeypatch.setattr(user_functions.time, "time", lambda: 0.0)
    monkeypatch.setattr(user_functions, "_LISTEN_POLL", 0)
    r = user_functions.listen("s1", 0, timeout=1)
    assert r["ok"] is True and r["data"]["watermark"] == 0


def test_query_session_unknown_is_ok_none(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {})
    monkeypatch.setattr(user_functions, "read_machine_info_log", lambda: [])
    r = user_functions.query_session("s1")
    assert r["ok"] is True and r["data"] is None


def test_kernel_error_maps_internal(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    def boom(a):
        raise KernelError("kernel exploded")
    _fake_kernel(server, {"check_alive": boom})
    r = user_functions.check_alive("s1")
    assert r["ok"] is False and r["code"] == Code.INTERNAL


def test_no_string_parsing_for_control_flow(server):
    """The wave's deliverable: user_functions never branches on message text."""
    import user_functions
    src = inspect.getsource(user_functions)
    assert " in str(" not in src
    assert "startswith(" not in src
    assert "'failed' in" not in src
    assert '"failed" in' not in src
```

Note: the monkeypatch lines for `resolve_claude` are belt-and-braces — keep whatever single patch works; the test must pass with `user_functions.my_session_id()` returning `ok("s1")`.

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/unit/test_user_functions_envelope.py -v`
Expected: FAIL — tools return legacy strings/dicts, not envelopes.

- [ ] **Step 3: Implement user_functions.py**

Add at the top (after imports):

```python
from result import Code
from rpc_client import KernelError


def _ok(data=None):
    return {"ok": True, "code": None, "message": None, "data": data, "retryable": False}


def _err(code, message, data=None, retryable=False):
    return {"ok": False, "code": code, "message": message, "data": data, "retryable": retryable}


def _kernel_err(e: KernelError):
    """Local kernel failure -> INTERNAL (entry validation already ran; a kernel
    error here is a bug or a crashed kernel)."""
    return _err(Code.INTERNAL, str(e))


def _remote_err():
    return _err(Code.PEER_UNREACHABLE, "peer machine unreachable")
```

Rewrite each tool (keep all existing routing/loop logic; change only the returns):

```python
def my_session_id() -> dict:
    """Discover this CC's own session_id. Walks the process tree to the claude
    binary ancestor (resolve_claude, Amd1), then looks up the session by pid.
    Returns ok(sid) or err(...)."""
    from proc import resolve_claude
    pid, _ = resolve_claude(os.getpid())
    if pid is None:
        return _err(Code.INTERNAL, "could not find claude ancestor")
    try:
        sid = rpc_client.call("session_by_pid", {"pid": pid})
    except KernelError as e:
        return _kernel_err(e)
    if not sid:
        return _err(Code.NOT_FOUND, f"no session recorded for claude pid {pid}")
    return _ok(sid)
```

```python
def query_session(session_id: str) -> dict:
    """Local first, then each registered peer machine (cross-realm fan-out).
    ok(session_inf) or ok(None) when unknown everywhere."""
    try:
        r = rpc_client.call("query_session", {"session_id": session_id})
        if r:
            return _ok(r)
    except KernelError:
        pass
    for m in read_machine_info_log():
        r = rpc_client.call_remote(m, "query_session", {"session_id": session_id})
        if r:
            return _ok(r)
    return _ok(None)
```

```python
def check_alive(session_id: str) -> dict:
    try:
        if rpc_client.call("check_alive", {"session_id": session_id}) == 1:
            return _ok(1)
    except KernelError:
        pass
    for m in read_machine_info_log():
        if rpc_client.call_remote(m, "check_alive", {"session_id": session_id}) == 1:
            return _ok(1)
    return _ok(0)
```

```python
def query_conversations(session_id: str) -> dict:
    """v2 dict format: {partner_sid: {...info}, ...}. Merges local + peers."""
    out = {}
    try:
        local = rpc_client.call("query_conversations", {"session_id": session_id})
    except KernelError:
        local = None
    if isinstance(local, dict):
        out.update(local)
    for m in read_machine_info_log():
        r = rpc_client.call_remote(m, "query_conversations", {"session_id": session_id})
        if isinstance(r, dict):
            out.update(r)  # sid uniqueness -> drop dups
    return _ok(out)
```

```python
def send_message(fromid: str, toid: str, message: str,
                 correlation_id: str = None, kind: str = None) -> dict:
    """Route by the conversation store (host for cross-machine, else local).
    ok({message_id, ts}) on success; err(NOT_FOUND) when the conversation is
    not registered; err(INTERNAL/PEER_UNREACHABLE) on transport failure."""
    conv_remote = _conv_store(toid)
    try:
        r = _send(fromid, toid, message, conv_remote,
                  correlation_id=correlation_id, kind=kind)
    except KernelError as e:
        return _kernel_err(e)
    if r is None:
        return _remote_err()
    if r.get("sent"):
        return _ok({"message_id": r.get("message_id"), "ts": r.get("ts")})
    return _err(Code.NOT_FOUND, r.get("reason", "send failed"))
```

Update `_send` (signature + args):

```python
def _send(fromid, toid, message, conv_remote, correlation_id=None, kind=None):
    # HP-01/HP-03: one message_id per LOGICAL send, generated here so every
    # funnel (send_message / connect hello / close notice) gets dedup for
    # free. The rpc layer reuses it as the operation_id, so a transport retry
    # replays the journaled result and a domain retry dedups on the filename.
    mid = uuid.uuid4().hex
    args = {"fromid": fromid, "toid": toid, "message": message, "message_id": mid}
    if correlation_id is not None:
        args["correlation_id"] = correlation_id
    if kind is not None:
        args["kind"] = kind
    if conv_remote is None:
        return rpc_client.call("send_message", args, operation_id=mid)
    return rpc_client.call_remote(conv_remote, "send_message", args, operation_id=mid)
```

```python
def evoke(session_id: str) -> dict:
    """Revive a dead CC on whatever machine it lives on (local or remote)."""
    is_local, machine = _find_target_machine(session_id)
    if not is_local and machine is None:
        return _err(Code.NOT_FOUND, "session not exists")
    try:
        if is_local:
            r = rpc_client.call("evoke", {"session_id": session_id})
        else:
            r = rpc_client.call_remote(machine, "evoke", {"session_id": session_id})
    except KernelError as e:
        return _kernel_err(e)
    if r is None:
        return _remote_err()
    if r.get("evoked"):
        return _ok({"evoked": True, "session_id": r.get("session_id")})
    return _err(Code.NOT_FOUND, r.get("reason", "evoke failed"))
```

`listen` — keep the loop; change the two returns to envelopes; the internal per-poll `try/except Exception` stays (transient → empty, retry):

```python
        if messages:
            messages.sort(key=lambda x: x.get("time", 0))
            return _ok({"messages": messages, "watermark": watermark})
        time.sleep(_LISTEN_POLL)
    return _ok({"messages": [], "watermark": acked_ts})
```

`listen_v2` — same:

```python
        if messages:
            # Display-only sort (created_at_ms is NOT a correctness field);
            # per-store order is by sequence, cross-store order is undefined.
            messages.sort(key=lambda m: (m.get("created_at_ms", 0),
                                         m.get("store_id") or "",
                                         m.get("sequence", 0)))
            return _ok({"messages": messages, "next_cursors": next_cursors})
        time.sleep(_LISTEN_POLL)
    return _ok({"messages": [], "next_cursors": cursors})
```

`query_my_cursors` — keep the merge; return `_ok(out)`.
`query_my_ACK_timestamp` — return `_ok(r)` (keep the try/except KernelError → 0).
`register_conversation` / `unregister_conversation` (currently via `rpc_client.call` in mcp_server) — wrap in user_functions:

```python
def register_conversation(sid_a: str, sid_b: str) -> dict:
    """Mark a LOCAL conversation active (low-level; connect handles routing).
    Exposed for bootstrapping/testing."""
    try:
        r = rpc_client.call("register_conversation", {"sid_a": sid_a, "sid_b": sid_b})
    except KernelError as e:
        return _kernel_err(e)
    return _ok(r if isinstance(r, dict) else {"ok": True})


def unregister_conversation(sid_a: str, sid_b: str) -> dict:
    """Mark a LOCAL conversation inactive (low-level)."""
    try:
        r = rpc_client.call("unregister_conversation", {"sid_a": sid_a, "sid_b": sid_b})
    except KernelError as e:
        return _kernel_err(e)
    return _ok(r if isinstance(r, dict) else {"ok": True})


def withdraw(fromid: str, toid: str, init_connect: int = 0,
             message_id: str = None) -> dict:
    """Withdraw a message or whole LOCAL conversation (low-level).
    init_connect=1: remove the whole folder + unregister; =0: default legacy
    mode withdraws fromid's latest undelivered message (non-idempotent).
    message_id: withdraw that EXACT message (retry-safe; preferred)."""
    try:
        r = rpc_client.call("withdraw", {"fromid": fromid, "toid": toid,
                                         "init_connect": init_connect,
                                         "message_id": message_id})
    except KernelError as e:
        return _kernel_err(e)
    if r and r.get("withdrawn"):
        return _ok(r)
    return _err(Code.NOT_FOUND, (r or {}).get("reason", "withdraw failed"))
```

`query_machines` / `help_connect_machines`:

```python
def query_machines() -> dict:
    """Registered peer machines: {id: entry, ...}."""
    return _ok({m.get("id"): m for m in read_machine_info_log()})


def help_connect_machines() -> dict:
    """Return the cross-machine handshake playbook (C4). The CC calls this when
    the user wants to link this machine to a peer, then follows the steps."""
    guide_path = os.path.join(PLUGIN_ROOT, "server", "handshake_guide.md")
    try:
        with open(guide_path, encoding="utf-8") as f:
            return _ok(f.read())
    except OSError as e:
        return _err(Code.NOT_FOUND, f"handshake guide not found at {guide_path}: {e}")
```

`connect`, `close_connection`, `create_collaborator` are NOT touched in this task (still legacy; rewritten in Tasks 5/6/9).

- [ ] **Step 4: Implement mcp_server.py**

```python
from result import Code


def _entry_error(*checks):
    """Run MCP-entry validators (HP-06). `checks` are (validator, value) pairs.
    Returns the INVALID_ARGUMENT envelope, or None when all pass. Kernel
    dispatch validates again - defense in depth, and remote RPC never passes
    through here."""
    try:
        for validator, value in checks:
            validator(value)
    except validation.InvalidArgumentError as e:
        return {"ok": False, "code": Code.INVALID_ARGUMENT,
                "message": str(e), "data": None, "retryable": False}
    return None
```

Each migrated tool becomes a passthrough. Full example (query_session):

```python
@mcp.tool()
def query_session(session_id: str) -> dict:
    """Look up a session by id (local kernel first, then registered peer
    machines). Returns the envelope: ok(session_inf) or ok(null) if unknown
    everywhere; err(INVALID_ARGUMENT) on a bad id."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.query_session(session_id)
```

Apply the identical pattern to: `my_session_id` (no validators), `check_alive`, `query_conversations`, `send_message` (add optional `correlation_id: str = None`, `kind: str = None` params, validated with `validate_message_id` when given), `register_conversation`, `unregister_conversation`, `withdraw`, `evoke`, `listen`, `listen_v2`, `query_my_ACK_timestamp`, `query_my_cursors`, `query_machines`, `help_connect_machines`.

`listen_v2`'s error path becomes a plain envelope return:

```python
    err = _entry_error((validation.validate_session_id, session_id),
                       (validation.validate_cursors, cursors))
    if err:
        return err
    return user_functions.listen_v2(session_id, cursors, timeout)
```

`query_my_cursors`'s error path likewise:

```python
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.query_my_cursors(session_id)
```

The tools `connect`, `close_connection`, `create_collaborator` stay as-is in mcp_server for this task (they return the legacy user_functions result; migrated in Tasks 5/6/9).

- [ ] **Step 5: Run tests**

Run: `py -3 -m pytest -q`
Expected: ALL PASS — but check: `test_no_string_parsing_for_control_flow` fails if `_claim_reply` still contains `str(...)` handling? No — `_claim_reply` uses `info`, not `" in str("`. If any assertion trips on a leftover pattern, fix the source (that is the deliverable).

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/user_functions.py v2_win/cc-communicate/server/mcp_server.py tests/unit/test_user_functions_envelope.py
git commit -m "feat(HP-07): envelope on every migrated tool; mcp_server passthrough; no string parsing

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: connect rewrite — connection_id + correlation_id handshake

**Files:**
- Modify: `v2_win/cc-communicate/server/user_functions.py` (connect, _claim_reply, _poll_reply, _conv_store routing helpers + `_activate_connection`/`_get_connection_info`/`_deactivate_connection` routed wrappers)
- Modify: `v2_win/cc-communicate/server/kernel.py` (`_ARG_VALIDATORS` for send_message: correlation_id)
- Modify: `v2_win/cc-communicate/server/mcp_server.py` (connect tool: connection_id param)
- Test: `tests/unit/test_connection_id.py` (new)

**Interfaces:**
- Consumes: Tasks 2/3/4 (kernel structured send_message with correlation_id; info.json kernel functions; envelope helpers).
- Produces: `connect(caller_sid, target_sid, connection_id=None, hold_time=300) -> envelope` with data `{"connection_id", "reply", "established_at_ms", "reused"}`; errors: `NOT_FOUND` (no target), `NOT_ALIVE` (revive failed), `CONFLICT` (different active connection), `TIMEOUT` (retryable, no reply), `INTERNAL`/`PEER_UNREACHABLE` (transport). `_claim_reply(pipe_dir, caller, target, conv_remote, hello_ts=0, connection_id=None)` — correlation_id match first, single-unambiguous-candidate legacy fallback.

- [ ] **Step 1: Write the failing test**

```python
"""HP-05: connect correlates replies by connection_id; info.json enforces
single active connection; legacy replies (no correlation_id) fall back only
when unambiguous."""
import json
import os
import time

import pytest
from result import Code
from rpc_client import KernelError

import message_record


def _seq_state():
    return {"schema_version": 1, "store_id": "store-test", "last_allocated": 0}


def test_claim_reply_matches_by_correlation_id(server):
    """The deliverable: a reply record whose correlation_id == connection_id is
    claimed even when a foreign newer message sits in the pipe."""
    import user_functions
    d = server.conversations.ensure_conv_dir("alice", "bob")
    pipe = os.path.join(d, "pipe")
    # a FOREIGN message from bob, newer than the hello - must NOT be taken
    foreign = message_record.new_record("store-test", 2, "bob", "alice",
                                        "foreign", correlation_id="other")
    rec = message_record.new_record("store-test", 3, "bob", "alice",
                                    "the reply", correlation_id="conn-1")
    for r in (foreign, rec):
        message_record.publish(d, r)
    got = user_functions._claim_reply(pipe, "alice", "bob", None,
                                      hello_ts=0, connection_id="conn-1")
    assert got == "the reply"
    # only the matched one is archived; foreign stays
    remaining = [f for f in os.listdir(pipe) if f.endswith(".json")]
    assert len(remaining) == 1


def test_claim_reply_legacy_fallback_single_candidate(server):
    """Old worker replies (no correlation_id): accepted only when unambiguous."""
    import user_functions
    d = server.conversations.ensure_conv_dir("alice", "bob")
    pipe = os.path.join(d, "pipe")
    with open(os.path.join(pipe, "0000000000100__bob__alice.md"), "w",
              encoding="utf-8") as f:
        f.write("legacy reply")
    got = user_functions._claim_reply(pipe, "alice", "bob", None,
                                      hello_ts=50, connection_id="conn-1")
    assert got == "legacy reply"


def test_claim_reply_legacy_fallback_refused_when_ambiguous(server):
    import user_functions
    d = server.conversations.ensure_conv_dir("alice", "bob")
    pipe = os.path.join(d, "pipe")
    for i in (100, 101):
        with open(os.path.join(pipe, f"{i:013d}__bob__alice.md"), "w",
                  encoding="utf-8") as f:
            f.write(f"msg{i}")
    got = user_functions._claim_reply(pipe, "alice", "bob", None,
                                      hello_ts=50, connection_id="conn-1")
    assert got is None


def test_connect_conflict_on_second_active(server, monkeypatch):
    """D9: connect with a DIFFERENT connection_id while one is active ->
    CONFLICT before any hello is sent."""
    import user_functions
    ka = server.kernel_api
    ka.activate_connection({}, "alice", "bob", "conn-1")
    calls = []
    monkeypatch.setattr(server.rpc_client, "call", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)
    monkeypatch.setattr(user_functions, "_find_target_machine",
                        lambda sid: (True, None))
    r = user_functions.connect("alice", "bob", connection_id="conn-2",
                               hold_time=1)
    assert r["ok"] is False and r["code"] == Code.CONFLICT
    assert r["data"]["current_connection_id"] == "conn-1"
    assert calls == []  # no kernel mutation attempted


def test_connect_retry_same_id_returns_state(server, monkeypatch):
    """Retry with the SAME connection_id while active -> ok(current state)."""
    import user_functions
    ka = server.kernel_api
    ka.activate_connection({}, "alice", "bob", "conn-1")
    calls = []
    monkeypatch.setattr(server.rpc_client, "call", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)
    monkeypatch.setattr(user_functions, "_find_target_machine",
                        lambda sid: (True, None))
    r = user_functions.connect("alice", "bob", connection_id="conn-1",
                               hold_time=1)
    assert r["ok"] is True and r["data"]["reused"] is True
    assert r["data"]["connection_id"] == "conn-1"
    assert calls == []


def test_connect_hello_carries_kind_and_correlation(server, monkeypatch):
    """Hello record: kind='hello', correlation_id == connection_id; reply
    matched by correlation_id; info.json activated on success."""
    import user_functions
    kernel_ops = {}

    def call(fn, args=None, timeout=30.0, operation_id=None):
        args = args or {}
        kernel_ops[fn] = args
        if fn == "check_alive":
            return 1
        if fn == "query_session":
            return "bob" if args.get("session_id") == "bob" else None
        if fn == "send_message":
            d = server.conversations.ensure_conv_dir(args["fromid"], args["toid"])
            seq = 1
            rec = message_record.new_record(
                "store-test", seq, args["fromid"], args["toid"], args["message"],
                kind=args.get("kind", "text"),
                correlation_id=args.get("correlation_id"),
                message_id=args["message_id"])
            message_record.publish(d, rec)
            return {"sent": True, "message_id": rec["message_id"],
                    "ts": rec["created_at_ms"], "correlation_id": args.get("correlation_id")}
        if fn == "register_conversation":
            return {"ok": True}
        if fn == "activate_connection":
            ka = server.kernel_api
            convs = {}
            return ka.activate_connection(convs, args["sid_a"], args["sid_b"],
                                          args["connection_id"])
        if fn == "unregister_conversation":
            return {"ok": True}
        raise KernelError(f"unknown: {fn}")

    monkeypatch.setattr(server.rpc_client, "call", call)
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)
    monkeypatch.setattr(user_functions, "_find_target_machine",
                        lambda sid: (True, None))

    # the peer (bob) receives the hello and replies with the correlation_id
    def peer_reply():
        d = server.conversations.conv_dir("alice", "bob")
        pipe = os.path.join(d, "pipe")
        seq = 2
        rec = message_record.new_record(
            "store-test", seq, "bob", "alice", "hello bob here",
            kind="text", correlation_id=kernel_ops["send_message"].get("correlation_id"))
        message_record.publish(server.conversations, rec)

    # drive connect in a background thread; bob replies after the hello lands
    import threading
    result = {}

    def do_connect():
        result["r"] = user_functions.connect("alice", "bob", hold_time=15)

    t = threading.Thread(target=do_connect)
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline and "send_message" not in kernel_ops:
        time.sleep(0.05)
    assert "send_message" in kernel_ops, "hello never sent"
    hello_args = kernel_ops["send_message"]
    assert hello_args.get("kind") == "hello"
    conn_id = hello_args.get("correlation_id")
    assert conn_id and conn_id != "conn-1"
    peer_reply()
    t.join(timeout=20)
    r = result["r"]
    assert r["ok"] is True, r
    assert r["data"]["connection_id"] == conn_id
    assert r["data"]["reply"] == "hello bob here"
    assert kernel_ops["activate_connection"]["connection_id"] == conn_id
    # info.json now active
    info = server.kernel_api.get_connection_info("alice", "bob")
    assert info["status"] == "active" and info["connection_id"] == conn_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/unit/test_connection_id.py -v`
Expected: FAIL — `_claim_reply` has no connection_id param; `connect` has no connection_id logic.

- [ ] **Step 3: Implement — routed connection-info wrappers** (user_functions.py, next to `_unregister`):

```python
def _get_connection_info(sid_a, sid_b, conv_remote):
    if conv_remote is None:
        return rpc_client.call("get_connection_info", {"sid_a": sid_a, "sid_b": sid_b})
    return rpc_client.call_remote(conv_remote, "get_connection_info",
                                  {"sid_a": sid_a, "sid_b": sid_b})


def _activate_connection(sid_a, sid_b, connection_id, conv_remote):
    if conv_remote is None:
        return rpc_client.call("activate_connection",
                               {"sid_a": sid_a, "sid_b": sid_b,
                                "connection_id": connection_id})
    return rpc_client.call_remote(conv_remote, "activate_connection",
                                  {"sid_a": sid_a, "sid_b": sid_b,
                                   "connection_id": connection_id})


def _deactivate_connection(sid_a, sid_b, conv_remote):
    if conv_remote is None:
        return rpc_client.call("deactivate_connection",
                               {"sid_a": sid_a, "sid_b": sid_b})
    return rpc_client.submit_remote_noblock(conv_remote, "deactivate_connection",
                                            {"sid_a": sid_a, "sid_b": sid_b})
```

**Implement `_claim_reply`** (replace the whole function):

```python
def _claim_reply(pipe_dir, caller, target, conv_remote, hello_ts=0, connection_id=None):
    """Scan pipe_dir once for target's reply (toid==caller, fromid==target).
    HP-05: a record whose correlation_id == connection_id is the reply -
    foreign messages can never be misread. Legacy fallback (D9, one release):
    when no correlation_id matches and EXACTLY ONE candidate exists (from/to +
    newer than hello_ts), accept it - that is unambiguous. Returns the reply
    content (archiving the file), or None."""
    candidates = []
    for fname, path, info in _scan_pipe(pipe_dir, caller):
        if info["from_id"] != target:
            continue
        if info["format"] == "record":
            rec = message_record.read_record(path)
            if not rec:
                continue
            ts = rec.get("created_at_ms", 0)
            content = (rec.get("payload") or {}).get("text")
            if content is None:
                continue
            corr = rec.get("correlation_id")
        else:
            ts = info["ts"]
            corr = None
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue  # C5: skip malformed/undecodable files
        if ts <= hello_ts:
            continue  # C3: stale (not newer than the hello) - skip
        if connection_id is not None and corr == connection_id:
            _archive_reply(conv_remote, caller, fname, path)
            return content
        candidates.append((fname, path, content))
    if connection_id is not None and len(candidates) == 1:
        fname, path, content = candidates[0]
        _archive_reply(conv_remote, caller, fname, path)
        return content
    return None
```

`_poll_reply` — thread the connection_id through:

```python
def _poll_reply(caller, target, hold_time, conv_remote, hello_ts=0, connection_id=None):
    """Block up to hold_time scanning (in-process) for target's reply (a pipe
    file with toid==caller, fromid==target). Returns the reply content, or None
    on timeout. Reads content BEFORE archiving (Amd2: no false-timeout even if a
    stray listener races us). A final scan after the deadline catches a reply
    that landed in the last poll window. (T15) hello_ts filters stale messages
    (C3); connection_id selects the correlated reply (HP-05)."""
    pipe_dir = _pipe_dir_for(caller, target, conv_remote)
    deadline = time.time() + hold_time
    while time.time() < deadline:
        reply = _claim_reply(pipe_dir, caller, target, conv_remote, hello_ts, connection_id)
        if reply is not None:
            return reply
        time.sleep(0.5)
    # final scan: a reply may have landed in the last 0.5s poll window. (T15)
    return _claim_reply(pipe_dir, caller, target, conv_remote, hello_ts, connection_id)
```

**Implement `connect`** (replace the whole function — the BRIDGE from Task 2 goes away):

```python
def connect(caller_sid: str, target_sid: str, connection_id: str = None,
            hold_time: int = 300) -> dict:
    """Establish a p2p connection to target_sid (Amd2 in-process poll + Phase 2
    routing). HP-05: connection_id (caller-supplied or generated) correlates
    the reply via the hello's correlation_id; info.json enforces ONE active
    connection per pair (D9) - a retry with the same id returns the current
    state, a different id is CONFLICT. Blocks up to hold_time."""
    hold_time = max(hold_time, _MIN_HOLD_TIME)
    conn_id = connection_id or uuid.uuid4().hex
    # 1. locate target
    is_local, target_machine = _find_target_machine(target_sid)
    if not is_local and target_machine is None:
        return _err(Code.NOT_FOUND, "target session not exists")
    # 2. check_alive on target's machine
    try:
        if is_local:
            alive = rpc_client.call("check_alive", {"session_id": target_sid})
        else:
            alive = rpc_client.call_remote(target_machine, "check_alive",
                                           {"session_id": target_sid})
    except KernelError:
        alive = 0
    # 3. revive if dead
    if alive != 1:
        ev = evoke(target_sid)
        if not ev["ok"]:
            return _err(Code.NOT_ALIVE, "evoke: " + str(ev.get("message")))
        deadline = time.time() + _REVIVE_WAIT
        while time.time() < deadline:
            time.sleep(1)
            try:
                if is_local:
                    a = rpc_client.call("check_alive", {"session_id": target_sid})
                else:
                    a = rpc_client.call_remote(target_machine, "check_alive",
                                               {"session_id": target_sid})
            except KernelError:
                a = 0
            if a == 1:
                break
        else:
            return _err(Code.NOT_ALIVE,
                        f"target did not come alive after evoke (waited {_REVIVE_WAIT}s)",
                        retryable=True)
    # 4. conversation store (host for cross-machine, else local) + active check
    conv_remote = _conv_store(target_sid)
    info = _get_connection_info(caller_sid, target_sid, conv_remote)
    if info and info.get("status") == "active":
        if info.get("connection_id") == conn_id:
            return _ok({"connection_id": conn_id, "reply": None,
                        "established_at_ms": info.get("established_at_ms"),
                        "reused": True})
        return _err(Code.CONFLICT, "connection already active",
                    data={"current_connection_id": info.get("connection_id"),
                          "status": "active"})
    init_connect = 0 if _conv_exists(caller_sid, target_sid, conv_remote) else 1
    # 5. register + send hello (kind=hello, correlation_id=connection_id)
    _register(caller_sid, target_sid, conv_remote)
    hello = ("connect hello from " + caller_sid + ". This is a p2p connection "
             "request - reply immediately with send_message(your_session_id, "
             + caller_sid + ", <any message>) to establish the channel.")
    try:
        send_res = _send(caller_sid, target_sid, hello, conv_remote,
                         correlation_id=conn_id, kind="hello")
    except KernelError as e:
        if init_connect:
            _withdraw(caller_sid, target_sid, 1, conv_remote)
        return _kernel_err(e)
    if send_res is None:
        if init_connect:
            _withdraw(caller_sid, target_sid, 1, conv_remote)
        return _remote_err()
    if not send_res.get("sent"):
        if init_connect:
            _withdraw(caller_sid, target_sid, 1, conv_remote)
        return _err(Code.INTERNAL, "send hello: " + str(send_res.get("reason")))
    hello_ts = send_res.get("ts") or 0
    # 6. in-process poll for the correlation-matched reply (HP-05)
    reply = _poll_reply(caller_sid, target_sid, hold_time, conv_remote,
                        hello_ts, conn_id)
    if reply is not None:
        act = _activate_connection(caller_sid, target_sid, conn_id, conv_remote)
        if act and act.get("activated"):
            return _ok({"connection_id": conn_id, "reply": reply,
                        "established_at_ms": act.get("established_at_ms"),
                        "reused": bool(act.get("reused"))})
        # race: another connect activated first - report its state
        info2 = _get_connection_info(caller_sid, target_sid, conv_remote)
        if info2 and info2.get("connection_id") != conn_id:
            return _err(Code.CONFLICT, "connection already active",
                        data={"current_connection_id": info2.get("connection_id"),
                              "status": info2.get("status")})
        return _ok({"connection_id": conn_id, "reply": reply,
                    "established_at_ms": int(time.time() * 1000), "reused": False})
    # 7. timeout -> clean up
    _withdraw(caller_sid, target_sid, init_connect, conv_remote)
    return _err(Code.TIMEOUT, "timeout waiting for reply", retryable=True)
```

**mcp_server.py connect tool** — add the parameter and validators:

```python
@mcp.tool()
def connect(caller_sid: str, target_sid: str, connection_id: str = None,
            hold_time: int = 300) -> dict:
    """Establish a p2p connection to target_sid (local or cross-realm). If the
    target is dead, revives it and waits for it to come alive, sends a hello
    (kind=hello, correlation_id=connection_id), then blocks up to hold_time
    seconds waiting for the correlation-matched reply. connection_id: caller-
    supplied to make retries idempotent; omitted -> server generates one
    (returned in the envelope data). One active connection per pair (D9): a
    retry with the same id returns the current state; a different id while one
    is active returns CONFLICT. Connect BEFORE calling listen (running a
    listener during connect can duplicate the reply). Once connect succeeds the
    channel is ESTABLISHED: you MUST then call listen in a loop (passing the
    watermark each call - see the listen tool) and keep it active until you
    call close_connection."""
    err = _entry_error((validation.validate_session_id, caller_sid),
                       (validation.validate_session_id, target_sid))
    if err:
        return err
    if connection_id is not None:
        err2 = _entry_error((validation.validate_connection_id, connection_id))
        if err2:
            return err2
    return user_functions.connect(caller_sid, target_sid, connection_id, hold_time)
```

**kernel.py `_ARG_VALIDATORS`** for send_message:

```python
    "send_message": {"fromid": validation.validate_session_id,
                     "toid": validation.validate_session_id,
                     "message": validation.validate_message_size,
                     "message_id": validation.validate_message_id,
                     "correlation_id": validation.validate_message_id},
```

(Note: `kind` is an internal constant — "hello" — no validator.)

- [ ] **Step 4: Run tests**

Run: `py -3 -m pytest -q`
Expected: ALL PASS. Watch: `test_send_dedup_by_message_id` (`r1 == r2`) — the dedup path now returns `correlation_id` from the found record, matching the first publish when both calls omit it (None) — still equal.

- [ ] **Step 5: Commit**

```bash
git add v2_win/cc-communicate/server/user_functions.py v2_win/cc-communicate/server/mcp_server.py v2_win/cc-communicate/server/kernel.py tests/unit/test_connection_id.py
git commit -m "feat(HP-05): connect via connection_id + correlation_id-matched reply (D9)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: close_connection deactivation

**Files:**
- Modify: `v2_win/cc-communicate/server/user_functions.py` (close_connection)
- Modify: `v2_win/cc-communicate/server/mcp_server.py` (close_connection tool returns envelope)
- Test: extend `tests/unit/test_connection_info.py` with a close-side test

**Interfaces:**
- Consumes: Task 3 `_deactivate_connection` helper, Task 5 connect envelope.
- Produces: `close_connection(...) -> ok({"closed": True})` that also marks info.json closed.

- [ ] **Step 1: Write the failing test** (append to test_connection_info.py)

```python
def test_close_connection_deactivates(server, monkeypatch):
    """close_connection marks the connection closed via deactivate_connection."""
    import user_functions
    ka = server.kernel_api
    convs = {}
    ka.activate_connection(convs, "alice", "bob", "conn-1")
    ops = {}

    def call(fn, args=None, timeout=30.0, operation_id=None):
        args = args or {}
        ops[fn] = args
        if fn == "upload_ack_timestamp":
            return 0
        if fn == "query_cursors":
            return {}
        if fn == "send_message":
            return {"sent": True, "message_id": "m", "ts": 1,
                    "correlation_id": None}
        if fn == "unregister_conversation":
            return {"ok": True}
        if fn == "deactivate_connection":
            return ka.deactivate_connection(convs, args["sid_a"], args["sid_b"])
        raise KernelError(f"unknown: {fn}")

    monkeypatch.setattr(server.rpc_client, "call", call)
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)
    monkeypatch.setattr(user_functions, "_conv_store", lambda toid: None)
    r = user_functions.close_connection("alice", "bob", acked_ts=5)
    assert r == {"ok": True, "code": None, "message": None,
                 "data": {"closed": True}, "retryable": False}
    assert ops["deactivate_connection"]["sid_a"] == "alice"
    assert ops["deactivate_connection"]["sid_b"] == "bob"
    info = ka.get_connection_info("alice", "bob")
    assert info["status"] == "closed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/unit/test_connection_info.py -v`
Expected: FAIL — close_connection returns `{"closed": True}` without the envelope and never calls deactivate_connection.

- [ ] **Step 3: Implement** — user_functions.py `close_connection`: keep steps 1/1b/2 exactly; insert a deactivate step after the notify/unregister block and change the return:

```python
    # 2b. mark the connection closed (info.json status=closed; HP-05/D9) -
    # routed like unregister (fire-and-forget if the conv is remote)
    try:
        _deactivate_connection(session_id, toid, conv_remote)
    except Exception:
        pass  # best-effort, like the notify above
    return _ok({"closed": True})
```

mcp_server.py close_connection tool — it already passes through; its docstring return mention stays. No code change needed beyond passthrough (already `return user_functions.close_connection(...)`).

- [ ] **Step 4: Run tests**

Run: `py -3 -m pytest -q`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add v2_win/cc-communicate/server/user_functions.py tests/unit/test_connection_info.py
git commit -m "feat(HP-05): close_connection deactivates info.json + envelope return

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: D8 live probe — env → hook → SessionStart chain

> **This task is a LIVE probe: it needs real CC spawns on the user's desktop.
> The SUBAGENT does the code part (registrar.js field). The probe itself is
> executed by the LEAD with the USER driving (a CC window opens briefly).**

**Files:**
- Modify: `v2_win/cc-communicate/scripts/registrar.js` (spawn_token field)
- Modify: `v2_win/cc-communicate/scripts/lib/paths.js` (data dir derives from env — no change expected; verify)

**Interfaces:**
- Produces: start events carry `spawn_token: process.env.CC_COMMUNICATE_SPAWN_TOKEN || null`.

- [ ] **Step 1: Add the field** — registrar.js `start` payload:

```js
    const p = appendEvent('start', {
      event: 'start',
      event_ts: Date.now(),
      session_id: sid,
      pid: r.pid,
      cwd: input.cwd || process.cwd(),
      start_time: r.start,        // claude process creation time — for liveness later
      source: input.source || null,
      spawn_token: process.env.CC_COMMUNICATE_SPAWN_TOKEN || null,
    });
```

- [ ] **Step 2: Verify the hook parses** (no live spawn needed)

Run: `node -e "process.env.CC_COMMUNICATE_SPAWN_TOKEN='tok123'; require('./v2_win/cc-communicate/scripts/registrar.js')" 2>&1 | head -1` (it exits after main(); expect no crash) — or run `node --check` on the file.

Run: `node --check "v2_win/cc-communicate/scripts/registrar.js"` — expect exit 0.

- [ ] **Step 3: LIVE PROBE (lead + user; ~2–3 min)**

Manual steps (documented here; the subagent reports back that the probe is pending):
1. Open a real CC in a scratch dir with the env var set, e.g. `cmd /c "set CC_COMMUNICATE_SPAWN_TOKEN=probe-tok-1 && start claude"` (Windows) or `tmux new-session -d -c <dir> env CC_COMMUNICATE_SPAWN_TOKEN=probe-tok-1 claude` (WSL).
2. Watch `data/session_ctrl/` for the new `start_<ts>_<sid>.json`; open it.
3. **PASS** if `spawn_token: "probe-tok-1"` appears (env → hook → SessionStart chain works → plan A). **FAIL** if null/missing (→ plan B; record the finding as a T# in `tested&2betest.md` §1).
4. Close that CC window.

- [ ] **Step 4: Record the outcome**

- PASS → note "plan A confirmed" in `tested&2betest.md` §1 (T# for the probe; e.g. T36).
- FAIL → note "plan B required" + the observed behavior (T#).
Task 8's env-injection step becomes conditional on this result.

- [ ] **Step 5: Commit**

```bash
git add v2_win/cc-communicate/scripts/registrar.js
git commit -m "feat(HP-04): registrar start event carries spawn_token (D8 probe)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Token chain — pending_spawn + kernel token map

**Files:**
- Modify: `v2_win/cc-communicate/server/paths.py` (PENDING_SPAWN_DIR + ensure_runtime_dirs)
- Modify: `v2_win/cc-communicate/server/spawn.py` (env injection — Windows env param; WSL `env VAR=x` in tmux command)
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (spawn_cc_new token + find_session_by_token / has_pending_spawn / claim_pending_spawn)
- Modify: `v2_win/cc-communicate/server/kernel.py` (spawn_tokens state, _handle_start/_handle_end, _dispatch, _ARG_VALIDATORS)
- Modify: `v2_win/cc-communicate/server/validation.py` (validate_spawn_token)
- Test: `tests/unit/test_spawn_token.py` (new)

**Interfaces:**
- Consumes: Task 7 probe result (whether env injection is plan A or B — both land in the same map).
- Produces: `paths.PENDING_SPAWN_DIR`; kernel state `spawn_tokens: dict = {}` (token→sid); `find_session_by_token(spawn_tokens, token) -> sid|None`; `has_pending_spawn(token) -> bool`; `claim_pending_spawn(spawn_tokens, token, session_id) -> {"claimed": bool, "session_id"?, "reason"?}`; `spawn_cc_new(cwd, prompt, spawn_token=None) -> {"spawned": True, "spawn_token"}`; `spawn.spawn_cc_new(cwd, prompt, spawn_token=None)`; `validation.validate_spawn_token(value)`.

- [ ] **Step 1: Write the failing test**

```python
"""HP-04: token -> sid map; pending marker; claim idempotency; start-event binding."""
import json
import os


def _write_pending(server, token):
    d = server.paths.PENDING_SPAWN_DIR
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, token + ".json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "spawn_token": token}, f)


def test_find_session_by_token(server):
    ka = server.kernel_api
    toks = {"t1": "s1"}
    assert ka.find_session_by_token(toks, "t1") == "s1"
    assert ka.find_session_by_token(toks, "nope") is None


def test_has_pending_spawn(server):
    ka = server.kernel_api
    assert ka.has_pending_spawn("t1") is False
    _write_pending(server, "t1")
    assert ka.has_pending_spawn("t1") is True


def test_claim_pending_spawn(server):
    ka = server.kernel_api
    toks = {}
    _write_pending(server, "t1")
    r = ka.claim_pending_spawn(toks, "t1", "s1")
    assert r == {"claimed": True, "session_id": "s1"}
    assert ka.find_session_by_token(toks, "t1") == "s1"
    assert ka.has_pending_spawn("t1") is False  # claim consumes the marker


def test_claim_pending_spawn_idempotent(server):
    ka = server.kernel_api
    toks = {"t1": "s1"}
    r = ka.claim_pending_spawn(toks, "t1", "s2")
    assert r == {"claimed": True, "session_id": "s1"}  # keeps the FIRST binding


def test_claim_without_pending_rejected(server):
    ka = server.kernel_api
    assert ka.claim_pending_spawn({}, "t1", "s1") == \
        {"claimed": False, "reason": "no pending spawn for token"}


def test_spawn_cc_new_writes_pending_and_token(server, monkeypatch):
    ka = server.kernel_api
    spawned = {}
    monkeypatch.setattr(server.spawn, "spawn_cc_new",
                        lambda cwd, prompt, spawn_token=None:
                        spawned.update(cwd=cwd, token=spawn_token))
    r = ka.spawn_cc_new("/tmp", "prompt", spawn_token="t1")
    assert r == {"spawned": True, "spawn_token": "t1"}
    assert spawned["token"] == "t1"
    assert os.path.isfile(os.path.join(server.paths.PENDING_SPAWN_DIR, "t1.json"))
    # no token -> no pending file, spawn_token None
    r2 = ka.spawn_cc_new("/tmp", "prompt")
    assert r2["spawn_token"] is None
    assert not os.path.exists(os.path.join(server.paths.PENDING_SPAWN_DIR, "None.json"))


def test_handle_start_binds_token(server):
    """Plan A: a start event carrying spawn_token populates the kernel map."""
    k = server.kernel
    k.spawn_tokens.clear()
    k.alive_sessions.clear()
    ev = {"event": "start", "event_ts": 1, "session_id": "s1", "pid": 11,
          "cwd": "/tmp", "start_time": None, "spawn_token": "t1"}
    k._handle_start(ev, "s1")
    assert k.spawn_tokens.get("t1") == "s1"
    # end event releases the token
    k._handle_end(ev, "s1")
    assert k.spawn_tokens.get("t1") is None


def test_handle_start_no_token_no_binding(server):
    k = server.kernel
    k.spawn_tokens.clear()
    k.alive_sessions.clear()
    ev = {"event": "start", "event_ts": 1, "session_id": "s1", "pid": 11,
          "cwd": "/tmp", "start_time": None}
    k._handle_start(ev, "s1")
    assert k.spawn_tokens == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/unit/test_spawn_token.py -v`
Expected: FAIL — PENDING_SPAWN_DIR, spawn_tokens, the three functions, spawn_token param all missing.

- [ ] **Step 3: Implement**

paths.py:

```python
PENDING_SPAWN_DIR   = os.path.join(DATA_DIR, 'pending_spawn')  # HP-04: spawn-token claim markers
```
(add to `ensure_runtime_dirs` tuple: `PENDING_SPAWN_DIR`)

validation.py (append):

```python
def validate_spawn_token(value) -> str:
    """spawn_token: uuid4 hex or any id-charset token (HP-04)."""
    return _check_id(value, "spawn_token")
```

spawn.py:

```python
def _detached_popen(cmd_args, cwd=None, env=None):
    """Windows: detached process independent of parent, survives parent exit.
    `start` opens a new window for the interactive CC (it needs a TTY). cwd is
    set via Popen (not `start /D <path>`) so paths with spaces work, and so the
    spawned/resumed CC's per-project lookup keys on the right cwd (T25). env:
    extra vars for the child (HP-04 spawn_token; inherited by cmd -> claude ->
    SessionStart hook)."""
    subprocess.Popen(
        cmd_args,
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
```

```python
def _tmux_spawn(cwd: str, claude_argv: list, env_token: str = None):
    """WSL: detached tmux session (pty) running claude. Survives parent exit.
    `-c` sets cwd (equivalent to Windows `start /D`). Session name is unique
    (time + pid) to avoid collisions on repeated evoke (C11). env_token (HP-04):
    the spawn token is set INSIDE the session via `env VAR=x claude` so claude
    and its SessionStart hook see it."""
    session_name = f"cc_{int(time.time())}_{os.getpid()}"
    cmd = ["tmux", "new-session", "-d", "-s", session_name]
    if cwd:
        cmd += ["-c", cwd]
    if env_token:
        cmd += ["env", "CC_COMMUNICATE_SPAWN_TOKEN=" + env_token]
    cmd += claude_argv
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
```

```python
def spawn_cc_new(cwd: str, prompt: str, spawn_token: str = None):
    """Spawn a NEW interactive CC in cwd (for create_collaborator /
    spawn_collaborator). `claude <prompt>` (no -p) processes the prompt then
    enters the REPL (stays alive). `--dangerously-skip-permissions` skips the
    workspace-trust dialog (Amd9). cwd is set via Popen (T25). spawn_token
    (HP-04) is injected into the child environment so the SessionStart hook
    can bind the session to its spawn request (plan A, D8)."""
    if os.name == "nt":
        env = None
        if spawn_token:
            env = {**os.environ, "CC_COMMUNICATE_SPAWN_TOKEN": spawn_token}
        _detached_popen(["cmd", "/c", "start", "claude",
                         "--dangerously-skip-permissions", prompt],
                        cwd=cwd, env=env)
    else:
        _tmux_spawn(cwd, [_claude_bin(), "--dangerously-skip-permissions", prompt],
                    env_token=spawn_token)
```

kernel_api.py:

```python
def spawn_cc_new(cwd: str, prompt: str, spawn_token: str = None) -> dict:
    """Kernel function for (cross-machine) create_collaborator / 
    spawn_collaborator (v2.1 §3.4.6): a peer MCP server calls this via
    call_remote so THIS kernel spawns a local CC (it knows its own claude path
    / spawn mechanism). HP-04: writes pending_spawn/<token>.json BEFORE
    spawning - the marker makes same-token retries safe (no double spawn) and
    is the plan B claim record; the child gets the token via env (plan A)."""
    if spawn_token:
        validation.validate_spawn_token(spawn_token)
        os.makedirs(PENDING_SPAWN_DIR, exist_ok=True)
        fileutil.atomic_write_json(
            os.path.join(PENDING_SPAWN_DIR, spawn_token + ".json"),
            {"schema_version": 1, "spawn_token": spawn_token, "cwd": cwd,
             "created_at_ms": int(time.time() * 1000)})
    spawn.spawn_cc_new(cwd, prompt, spawn_token)
    return {"spawned": True, "spawn_token": spawn_token}
```

```python
# ---------- spawn tokens (HP-04 / D8) ----------
# One map per kernel: spawn_token -> session_id. Populated by plan A (start
# events carrying CC_COMMUNICATE_SPAWN_TOKEN) or plan B (claim_pending_spawn).
# Rebuilt on kernel restart by start-event replay. The pending_spawn/<token>
# marker file distinguishes "never spawned" from "spawned, not yet registered"
# so a same-token retry never double-spawns.

def find_session_by_token(spawn_tokens: dict, token: str):
    return spawn_tokens.get(token)


def has_pending_spawn(token: str) -> bool:
    return os.path.isfile(os.path.join(PENDING_SPAWN_DIR, token + ".json"))


def claim_pending_spawn(spawn_tokens: dict, token: str, session_id: str) -> dict:
    """Plan B: the spawned worker claims its token on its first tool call.
    Idempotent: an existing binding is kept (worker retries are no-ops)."""
    validation.validate_spawn_token(token)
    validation.validate_session_id(session_id)
    existing = spawn_tokens.get(token)
    if existing:
        return {"claimed": True, "session_id": existing}
    if not has_pending_spawn(token):
        return {"claimed": False, "reason": "no pending spawn for token"}
    spawn_tokens[token] = session_id
    try:
        os.remove(os.path.join(PENDING_SPAWN_DIR, token + ".json"))
    except OSError:
        pass
    return {"claimed": True, "session_id": session_id}
```

(import `PENDING_SPAWN_DIR` in kernel_api.py's paths import line.)

kernel.py — module state:

```python
spawn_tokens: dict = {}  # HP-04: spawn_token -> session_id (rebuilt from event replay)
```

`_handle_start` — append after the known_pids block:

```python
    # HP-04: a start event carrying CC_COMMUNICATE_SPAWN_TOKEN binds the
    # session to its spawn request (plan A). Rebuilt on kernel restart via
    # event replay, so the map needs no separate persistence.
    tok = ev.get("spawn_token")
    if tok:
        spawn_tokens[tok] = sid
```

`_handle_end` — release bindings for the ended session:

```python
def _handle_end(ev: dict, sid: str):
    alive_sessions.pop(sid, None)
    if sid in sessions:
        sessions[sid]["ended_at"] = ev.get("event_ts")
    for tok, s in list(spawn_tokens.items()):
        if s == sid:
            spawn_tokens.pop(tok, None)
```

`_ARG_VALIDATORS` additions:

```python
    "spawn_cc_new": {"cwd": validation.validate_cwd,
                     "spawn_token": validation.validate_spawn_token},
    "find_session_by_token": {"token": validation.validate_spawn_token},
    "has_pending_spawn": {"token": validation.validate_spawn_token},
    "claim_pending_spawn": {"token": validation.validate_spawn_token,
                            "session_id": validation.validate_session_id},
```

`_dispatch` additions:

```python
    if function == "spawn_cc_new":
        return kernel_api.spawn_cc_new(args["cwd"], args["prompt"], args.get("spawn_token"))
    if function == "find_session_by_token":
        return kernel_api.find_session_by_token(spawn_tokens, args["token"])
    if function == "has_pending_spawn":
        return kernel_api.has_pending_spawn(args["token"])
    if function == "claim_pending_spawn":
        return kernel_api.claim_pending_spawn(spawn_tokens, args["token"], args["session_id"])
```

(If the Task 7 probe FAILED, skip the spawn.py env-injection half (keep `_tmux_spawn`/`_detached_popen` env plumbing as written but note plan B is live; the claim path covers binding. Record the deviation in `tested&2betest.md`.)

- [ ] **Step 4: Run tests**

Run: `py -3 -m pytest tests/unit/test_spawn_token.py -v && py -3 -m pytest -q`
Expected: PASS — all new + existing.

- [ ] **Step 5: Commit**

```bash
git add v2_win/cc-communicate/server/paths.py v2_win/cc-communicate/server/spawn.py v2_win/cc-communicate/server/kernel_api.py v2_win/cc-communicate/server/kernel.py v2_win/cc-communicate/server/validation.py tests/unit/test_spawn_token.py
git commit -m "feat(HP-04): spawn_token chain - pending markers + kernel token map (D8)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: spawn_collaborator + claim_pending_spawn tools + legacy wrapper

**Files:**
- Modify: `v2_win/cc-communicate/server/user_functions.py` (spawn_collaborator, claim_pending_spawn, create_collaborator legacy wrapper)
- Modify: `v2_win/cc-communicate/server/mcp_server.py` (two new tools; create_collaborator passthrough)
- Test: `tests/unit/test_spawn_collaborator.py` (new)

**Interfaces:**
- Consumes: Tasks 4/5/8 (envelope helpers, connect envelope, kernel token functions).
- Produces: `user_functions.spawn_collaborator(caller_sid, cwd, spawn_token=None, machine=None, hold_time=300) -> ok(WorkerHandle)` where WorkerHandle = `{"session_id", "machine_id", "cwd", "spawn_token", "connection_status": "registered"}`; errors `TIMEOUT` (retryable, no registration in 30s), `INTERNAL`/`PEER_UNREACHABLE`; `user_functions.claim_pending_spawn(spawn_token, session_id) -> ok({"claimed": True, "session_id"})`; `create_collaborator(...) -> legacy str` (wrapper: spawn + connect, mapped back to legacy strings).

- [ ] **Step 1: Write the failing test**

```python
"""HP-04: spawn_collaborator WorkerHandle + same-token retry + claim + legacy wrapper."""
import pytest
from result import Code
from rpc_client import KernelError

import user_functions


def test_spawn_collaborator_handle(server, monkeypatch):
    """New token: spawn -> poll find_session_by_token -> registered handle."""
    calls = {}

    def call(fn, args=None, timeout=30.0, operation_id=None):
        args = args or {}
        calls[fn] = args
        if fn == "find_session_by_token":
            return None  # first poll: not registered yet
        if fn == "has_pending_spawn":
            return False
        if fn == "spawn_cc_new":
            return {"spawned": True, "spawn_token": args.get("spawn_token")}
        raise KernelError(f"unknown: {fn}")

    monkeypatch.setattr(server.rpc_client, "call", call)
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)
    monkeypatch.setattr(user_functions, "_find_session_by_token",
                        lambda tok, machine=None: None)
    monkeypatch.setattr(user_functions, "_has_pending_spawn",
                        lambda tok, machine=None: False)
    monkeypatch.setattr(user_functions, "_spawn_new",
                        lambda cwd, prompt, tok, machine=None: {"spawned": True})
    monkeypatch.setattr(user_functions, "_worker_handle",
                        lambda sid, tok, cwd, machine=None:
                        {"session_id": sid, "machine_id": "m1", "cwd": cwd,
                         "spawn_token": tok, "connection_status": "registered"})
    # find resolves on the second poll
    state = {"n": 0}
    def find2(tok, machine=None):
        state["n"] += 1
        return "s9" if state["n"] > 1 else None
    monkeypatch.setattr(user_functions, "_find_session_by_token", find2)
    monkeypatch.setattr(user_functions, "time", server.time)  # no-op guard
    monkeypatch.setattr(user_functions.time, "sleep", lambda s: None)
    r = user_functions.spawn_collaborator("caller", "/tmp", spawn_token="t1")
    assert r["ok"] is True
    assert r["data"]["session_id"] == "s9"
    assert r["data"]["spawn_token"] == "t1"
    assert r["data"]["connection_status"] == "registered"
    assert state["n"] >= 2


def test_spawn_collaborator_same_token_retry_no_respawn(server, monkeypatch):
    """Same-token retry: session already bound -> original handle, NO spawn."""
    spawned = []
    monkeypatch.setattr(server.rpc_client, "call", lambda *a, **k: None)
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)
    monkeypatch.setattr(user_functions, "_find_session_by_token",
                        lambda tok, machine=None: "s9")
    monkeypatch.setattr(user_functions, "_has_pending_spawn",
                        lambda tok, machine=None: True)
    monkeypatch.setattr(user_functions, "_spawn_new",
                        lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(user_functions, "_worker_handle",
                        lambda sid, tok, cwd, machine=None:
                        {"session_id": sid, "machine_id": "m1", "cwd": cwd,
                         "spawn_token": tok, "connection_status": "registered"})
    r = user_functions.spawn_collaborator("caller", "/tmp", spawn_token="t1")
    assert r["ok"] is True and r["data"]["session_id"] == "s9"
    assert spawned == []  # never re-spawned


def test_spawn_collaborator_register_timeout(server, monkeypatch):
    monkeypatch.setattr(server.rpc_client, "call", lambda *a, **k: None)
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)
    monkeypatch.setattr(user_functions, "_find_session_by_token",
                        lambda tok, machine=None: None)
    monkeypatch.setattr(user_functions, "_has_pending_spawn",
                        lambda tok, machine=None: True)
    monkeypatch.setattr(user_functions.time, "sleep", lambda s: None)
    r = user_functions.spawn_collaborator("caller", "/tmp", spawn_token="t1")
    assert r["ok"] is False and r["code"] == Code.TIMEOUT and r["retryable"] is True


def test_claim_pending_spawn_tool(server, monkeypatch):
    monkeypatch.setattr(server.rpc_client, "call",
                        lambda fn, args=None, timeout=30.0, operation_id=None:
                        {"claimed": True, "session_id": args["session_id"]}
                        if fn == "claim_pending_spawn" else None)
    r = user_functions.claim_pending_spawn("t1", "s1")
    assert r["ok"] is True and r["data"] == {"claimed": True, "session_id": "s1"}


def test_create_collaborator_legacy_strings(server, monkeypatch):
    """Legacy wrapper maps the envelope back to today's exact strings."""
    monkeypatch.setattr(user_functions, "spawn_collaborator",
                        lambda *a, **k: {
                            "ok": True, "code": None, "message": None,
                            "data": {"session_id": "s9", "machine_id": "m1",
                                     "cwd": "/tmp", "spawn_token": "t1",
                                     "connection_status": "registered"},
                            "retryable": False})
    monkeypatch.setattr(user_functions, "connect",
                        lambda *a, **k: {
                            "ok": True, "code": None, "message": None,
                            "data": {"connection_id": "c1", "reply": "hello bob",
                                     "established_at_ms": 1, "reused": False},
                            "retryable": False})
    s = user_functions.create_collaborator("caller", "/tmp", hold_time=300)
    assert s == "connect succeed; reply: hello bob"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/unit/test_spawn_collaborator.py -v`
Expected: FAIL — no spawn_collaborator/claim_pending_spawn; create_collaborator still calls connect directly.

- [ ] **Step 3: Implement** — user_functions.py. The new-API worker prompt (replaces the legacy prompt for spawn_collaborator; the legacy create_collaborator prompt stays EXACTLY as today so old replies exercise the legacy fallback):

```python
def _spawn_prompt(token: str) -> str:
    return ("You are a new collaborator spawned by cc-communicate. "
            "First call my_session_id to learn your id. Then call "
            f"claim_pending_spawn('{token}', <your_id>) - one call; it is a "
            "no-op if your session was already claimed. Then call listen "
            "(your_id, acked_ts, timeout) - it blocks and returns "
            "{messages, watermark}. Pass 0 as acked_ts the FIRST time; on "
            "every later listen pass the watermark the previous listen "
            "returned (this lets the kernel archive only what you've "
            "confirmed - never drop or duplicate it). When a peer sends you "
            "a hello (kind=hello, carrying a correlation_id), reply with "
            "send_message(your_id, peer_id, <message>, correlation_id=<the "
            "hello's correlation_id>). KEEP LISTENING: after each listen "
            "returns, process any messages and call listen again (with the "
            "latest watermark), in a loop, until you call close_connection("
            "your_id, peer_id, your_latest_watermark) to end the "
            "conversation. If you ever lose your watermark (compact / long "
            "gap), call query_my_ACK_timestamp(your_id) to recover it. "
            "Never invoke listen.py directly, never write a shell loop, "
            "never nohup a listener - only use the listen tool.")
```

```python
def _find_session_by_token(token: str, machine: dict = None):
    if machine is None:
        return rpc_client.call("find_session_by_token", {"token": token})
    return rpc_client.call_remote(machine, "find_session_by_token", {"token": token})


def _has_pending_spawn(token: str, machine: dict = None):
    if machine is None:
        return rpc_client.call("has_pending_spawn", {"token": token})
    return rpc_client.call_remote(machine, "has_pending_spawn", {"token": token})


def _spawn_new(cwd: str, prompt: str, spawn_token: str, machine: dict = None):
    if machine is None:
        return rpc_client.call("spawn_cc_new",
                               {"cwd": cwd, "prompt": prompt,
                                "spawn_token": spawn_token})
    return rpc_client.call_remote(machine, "spawn_cc_new",
                                  {"cwd": cwd, "prompt": prompt,
                                   "spawn_token": spawn_token})


def _worker_handle(session_id: str, spawn_token: str, cwd: str,
                   machine: dict = None) -> dict:
    machine_id = (machine or {}).get("id")
    if not machine_id:
        machine_id = machine_identity.load_or_create().get("id")
    return {"session_id": session_id, "machine_id": machine_id, "cwd": cwd,
            "spawn_token": spawn_token, "connection_status": "registered"}


def spawn_collaborator(caller_sid: str, cwd: str, spawn_token: str = None,
                       machine: dict = None, hold_time: int = 300) -> dict:
    """Spawn a NEW CC in cwd (on `machine` if given, else local), wait for it
    to register, and return a structured WorkerHandle - NO auto-connect (the
    caller decides when to call connect). spawn_token: caller-supplied (or
    server-generated, returned in the handle); a retry with the SAME token
    returns the original handle instead of spawning again. HP-04."""
    token = spawn_token or uuid.uuid4().hex
    # same-token retry: already registered -> original handle
    try:
        sid = _find_session_by_token(token, machine)
    except KernelError as e:
        return _kernel_err(e)
    if sid:
        return _ok(_worker_handle(sid, token, cwd, machine))
    # in-flight (pending marker) -> don't re-spawn
    try:
        pending = _has_pending_spawn(token, machine)
    except KernelError as e:
        return _kernel_err(e)
    if pending is None:
        return _remote_err()
    if not pending:
        try:
            r = _spawn_new(cwd, _spawn_prompt(token), token, machine)
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
    return _ok(_worker_handle(sid, token, cwd, machine))


def claim_pending_spawn(spawn_token: str, session_id: str) -> dict:
    """Plan B (D8): a spawned worker claims its token on first tool use so
    spawn_collaborator's registration poll can resolve. Idempotent."""
    try:
        r = rpc_client.call("claim_pending_spawn",
                            {"token": spawn_token, "session_id": session_id})
    except KernelError as e:
        return _kernel_err(e)
    if r and r.get("claimed"):
        return _ok({"claimed": True, "session_id": r.get("session_id")})
    return _err(Code.NOT_FOUND, (r or {}).get("reason", "no pending spawn for token"))
```

`create_collaborator` — legacy wrapper (replace the whole function; keep the OLD prompt text exactly as today, keep the 30s register window via the token path):

```python
def create_collaborator(caller_sid: str, cwd: str, hold_time: int = 300,
                        machine=None) -> str:
    """LEGACY wrapper (one release, HP-07): spawn + connect, returns the
    legacy string shape. New code should use spawn_collaborator (structured
    WorkerHandle) + connect. The spawn prompt stays the OLD text so its
    correlation_id-less replies exercise connect's legacy fallback (D9)."""
    hold_time = max(hold_time, _MIN_HOLD_TIME)
    res = spawn_collaborator(caller_sid, cwd, spawn_token=None,
                             machine=machine, hold_time=hold_time)
    if not res["ok"]:
        return "failed, " + str(res.get("message"))
    handle = res["data"]
    cr = connect(caller_sid, handle["session_id"], hold_time=hold_time)
    if cr["ok"]:
        reply = (cr["data"] or {}).get("reply")
        return ("connect succeed; reply: " + reply) if reply else "connect succeed"
    return "connect failed, " + str(cr.get("message"))
```

mcp_server.py — two new tools + the legacy create_collaborator passthrough stays (it returns a str; that is correct for a legacy wrapper):

```python
@mcp.tool()
def spawn_collaborator(caller_sid: str, cwd: str, spawn_token: str = None,
                       permission_mode: str = "bypass", machine: dict = None,
                       hold_time: int = 300) -> dict:
    """Spawn a NEW CC in cwd (on `machine` if given - a query_machines entry -
    else this machine) and wait for it to register. Returns the envelope with
    a structured WorkerHandle in data: {session_id, machine_id, cwd,
    spawn_token, connection_status}. Does NOT auto-connect - call connect when
    you want the channel. spawn_token: caller-supplied to make retries
    idempotent (same token -> same handle, no second spawn); omitted -> server
    generates one (returned in the handle). permission_mode: accepted now
    (default 'bypass' = current behavior); Wave 3 HP-10 flips the default to
    'standard' per D4 - the parameter surface never changes."""
    err = validation.validate_spawn_entry(caller_sid, cwd, machine)
    if err:
        return {"ok": False, "code": Code.INVALID_ARGUMENT,
                "message": err, "data": None, "retryable": False}
    if spawn_token is not None:
        err2 = _entry_error((validation.validate_spawn_token, spawn_token))
        if err2:
            return err2
    return user_functions.spawn_collaborator(caller_sid, cwd, spawn_token,
                                             machine, hold_time)


@mcp.tool()
def claim_pending_spawn(spawn_token: str, session_id: str) -> dict:
    """Claim a pending spawn token (plan B, HP-04): a spawned worker calls
    this on its FIRST tool use so the spawner's registration poll can resolve.
    Idempotent - safe to call more than once. Returns ok({claimed, session_id})
    or err(NOT_FOUND) when no pending spawn matches the token."""
    err = _entry_error((validation.validate_spawn_token, spawn_token),
                       (validation.validate_session_id, session_id))
    if err:
        return err
    return user_functions.claim_pending_spawn(spawn_token, session_id)
```

- [ ] **Step 4: Run tests**

Run: `py -3 -m pytest tests/unit/test_spawn_collaborator.py -v && py -3 -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2_win/cc-communicate/server/user_functions.py v2_win/cc-communicate/server/mcp_server.py tests/unit/test_spawn_collaborator.py
git commit -m "feat(HP-04): spawn_collaborator WorkerHandle + claim_pending_spawn + legacy wrapper

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 10: SKILL.md, parity sync, full gate

**Files:**
- Modify: `v2_win/cc-communicate/skills/cc-communicate/SKILL.md` (+ its v2_wsl twin after sync)
- Sync: ALL touched server/scripts files to `v2_wsl/cc-communicate/` (byte-identical)
- Run: `py -3 tools/check_parity.py`, `py -3 tools/run_regression.py --tier auto`, full live gate L1–L6 (lead + user)

**Interfaces:**
- Consumes: all Tasks 1–9.
- Produces: SKILL.md documents the new envelope returns, spawn_collaborator, claim_pending_spawn, connect connection_id, send_message correlation_id; both trees parity-identical; gate GREEN.

- [ ] **Step 1: Rewrite SKILL.md tool docs** (v2_win)

For every tool, document the envelope return and new params. Key sections:
- A preamble: "Every tool returns `{ok, code, message, data, retryable}`. Branch on `code` (`INVALID_ARGUMENT/NOT_FOUND/PEER_UNREACHABLE/TIMEOUT/CONFLICT/NOT_ALIVE/RESOURCE_EXHAUSTED/INTERNAL`) and `retryable` - NEVER parse message text."
- `spawn_collaborator`: WorkerHandle shape, same-token retry, permission_mode note (default bypass; Wave 3 flips).
- `claim_pending_spawn`: first-tool-call claim for plan B.
- `connect`: connection_id semantics (idempotent retry, CONFLICT on second active, correlation_id-matched reply), still "connect before listen".
- `send_message`: optional correlation_id + kind.
- `listen`/`listen_v2`: data now wraps {messages, watermark}/{messages, next_cursors}.
- The worker-playbook section: "on a hello (kind=hello with correlation_id), reply send_message(..., correlation_id=...)".

- [ ] **Step 2: Mirror every changed file to v2_wsl**

```bash
for f in server/result.py server/kernel_api.py server/kernel.py server/user_functions.py server/mcp_server.py server/conversations.py server/validation.py server/paths.py server/spawn.py scripts/registrar.js scripts/lib/proc.js scripts/lib/paths.js skills/cc-communicate/SKILL.md; do
  cp "v2_win/cc-communicate/$f" "v2_wsl/cc-communicate/$f"
done
```

(Only copy files that actually changed; proc.js may be unchanged. The `.mcp.json` files differ by design — do NOT copy those.)

- [ ] **Step 3: Verify parity + auto tiers**

Run: `py -3 tools/check_parity.py`
Expected: `PARITY OK`.

Run: `py -3 tools/run_regression.py --tier auto`
Expected: `GATE PASS` (T0 syntax, T1 pytest all green, T2 parity).

- [ ] **Step 4: LIVE gates (lead + user; real CCs)**

Re-run L1–L4 from the standing gate (`tools/run_regression.py` prints the checklists), plus the Wave 2 additions:
- **L5** same-cwd concurrent spawns: two `spawn_collaborator` calls in one cwd → two distinct WorkerHandles/sessions, no cross-talk (verify via `data/session_ctrl/` start events each carrying a different spawn_token).
- **L6** correlated connect: `connect` with an explicit `connection_id`; the spawned worker replies with the correlation_id; verify the reply record's `correlation_id` field in `data/conversations/<pair>/log/` matches.

Any RED → fix + record T# in `tested&2betest.md` §1, re-run that tier only.

- [ ] **Step 5: Record + commit**

Record the wave outcome (one T# entry, e.g. T36+) in `tested&2betest.md` §1. Then:

```bash
git add v2_win v2_wsl tested\&2betest.md
git commit -m "docs(W2): SKILL.md envelope docs + v2_wsl parity sync + wave records

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage**: §2.1/2.2 → Task 1; §2.4 → Task 2; §4.3 info.json → Task 3; §2.3 most tools + grep test → Task 4; §4.1/4.2/4.4 → Task 5; §4.3 close → Task 6; §3.2 D8 probe → Task 7; §3.2/3.3 chain → Task 8; §3.1/3.4 → Task 9; §5.3 sync + SKILL → Task 10. §6 out-of-scope items are untouched by design (permission_mode default stays "bypass").
- **Type consistency**: envelope keys `ok/code/message/data/retryable` everywhere; kernel dict keys `sent/withdrawn/evoked/activated/claimed` + `reason`; WorkerHandle keys `session_id/machine_id/cwd/spawn_token/connection_status`; `_claim_reply` signature `(pipe_dir, caller, target, conv_remote, hello_ts=0, connection_id=None)` used identically in Task 5 tests and code.
- **Dependencies**: Task 5's test relies on Task 3's `activate_connection` (available since Task 3). Task 9's legacy wrapper relies on Task 5's envelope connect. Tasks 1–6 complete HP-07+HP-05 before HP-04 starts (probe first, per D8).
- **Deferred minors riding along**: known_pids trim (`kernel.py` `_handle_start`) is touched by Task 8's edit — the `sorted(known, key=known.get)` None-start_time risk is NOT in scope; leave as-is (recorded in `tested&2betest.md` §2). `_pid_live`/`_match` dedup: NOT in scope (deferred minors list).
