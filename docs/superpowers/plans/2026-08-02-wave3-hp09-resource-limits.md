# HP-09 Resource Limits + Backpressure + artifact_refs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution for THIS wave (user mandate, durable): INLINE — no context-heavy subagents.** Execute task-by-task in the session (executing-plans style), `py -3 tools/run_regression.py --tier auto` as the exit gate. Live gates (full L1–L6) run at WAVE 3 exit (after HP-09/10/11).

**Goal:** Activate the dormant RESOURCE_EXHAUSTED code (over-limit inline messages), add artifact_refs to send_message (D5), add a per-pair unacked backpressure cap, and expose backlog stats as a kernel function.

**Architecture:** `ResourceExhaustedError(InvalidArgumentError)` carries `code=RESOURCE_EXHAUSTED` + structured bytes data; `_entry_error` maps by exception code. artifact_refs ride in the record payload (`{text, artifact_refs}` — additive, no schema bump), validated at both trust boundaries, delivered to listen_v2 (raw record, automatic) and legacy listen (new field). The backlog cap is enforced store-side in `kernel_api.send_message` before publish; `user_functions.send_message` maps it by key presence to `err(RESOURCE_EXHAUSTED, retryable=True)`. `backlog_stats` is a read-only kernel function (not an MCP tool).

**Tech Stack:** Python 3 (`py -3`), pytest. No new dependencies.

## Global Constraints

- **`py -3` for ALL Python** on Windows (git-bash; quote paths with spaces/CJK).
- **Tests isolated**: conftest `server` fixture sets `CC_COMMUNICATE_DATA_DIR` → tmp_path and reloads modules per test. `mcp_server` is NOT in the reload list but can be imported in tests (`mcp` SDK is installed); its `validation` reference resolves the per-test reloaded module object.
- **Parity**: v2_win ↔ v2_wsl byte-identical outside `.mcp.json`; sync modified files (incl. SKILL.md) before any parity run (Task 6); verify with `py -3 tools/check_parity.py`.
- **Commit format**: `feat/fix/test/docs(scope): subject` + `Co-Authored-By: Claude <noreply@anthropic.com>`; work on main (user consent).
- **Records**: every bug found during implementation gets a T# entry in `tested&2betest.md` §1 (Method/Result/Confidence).
- **Run from repo root** `C:\研究生\实习\learn AI\projects\cc-communicate`; per-task test: `py -3 -m pytest tests/unit/test_X.py -v`; full: `py -3 -m pytest -q`.
- **Design spec**: `docs/superpowers/specs/2026-08-02-wave3-hp09-resource-limits-design.md` (user-approved). Deviations require user approval.
- **No string parsing for control flow** (Wave-2 standing rule): envelope branching on structured keys only.

---

### Task 1: `ResourceExhaustedError` + entry-code mapping (activates the dormant code)

**Files:**
- Modify: `v2_win/cc-communicate/server/validation.py` (exception class + `validate_message_size`)
- Modify: `v2_win/cc-communicate/server/mcp_server.py` (`_entry_error`)
- Create: `tests/unit/test_resource_limits.py` (entry/validator tests)

**Interfaces:**
- Produces: `validation.ResourceExhaustedError(InvalidArgumentError)` with `.code = Code.RESOURCE_EXHAUSTED` and `.data` attribute; `validate_message_size` raises it over the cap with `data={"limit_bytes", "actual_bytes"}`; `mcp_server._entry_error` returns envelopes keyed by `getattr(e, "code", Code.INVALID_ARGUMENT)`.
- Consumes: `result.Code.RESOURCE_EXHAUSTED` (exists), `validation.MAX_INLINE_BYTES` (exists).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_resource_limits.py`:

```python
"""HP-09: RESOURCE_EXHAUSTED activation (inline cap) - entry envelope + validator."""
import pytest
from result import Code

import mcp_server


def test_over_limit_entry_is_resource_exhausted(server):
    big = "x" * (server.validation.MAX_INLINE_BYTES + 1)
    r = mcp_server.send_message("a", "b", big)
    assert r["ok"] is False
    assert r["code"] == Code.RESOURCE_EXHAUSTED
    assert r["retryable"] is False
    assert r["data"] == {"limit_bytes": server.validation.MAX_INLINE_BYTES,
                         "actual_bytes": len(big.encode("utf-8"))}


def test_validate_message_size_raises_resource_exhausted(server):
    v = server.validation
    with pytest.raises(v.ResourceExhaustedError) as ei:
        v.validate_message_size("x" * (v.MAX_INLINE_BYTES + 1))
    assert ei.value.code == Code.RESOURCE_EXHAUSTED
    assert ei.value.data == {"limit_bytes": v.MAX_INLINE_BYTES,
                             "actual_bytes": v.MAX_INLINE_BYTES + 1}


def test_validate_message_size_inline_ok(server):
    v = server.validation
    assert v.validate_message_size("hi" * 100) == "hi" * 100


def test_validate_message_size_non_str_still_invalid(server):
    v = server.validation
    with pytest.raises(v.InvalidArgumentError):
        v.validate_message_size(42)
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_resource_limits.py -v`
Expected: FAIL — `AttributeError: module 'validation' has no attribute 'ResourceExhaustedError'`; `test_over_limit_entry_is_resource_exhausted` gets INVALID_ARGUMENT

- [ ] **Step 3: Implement in `validation.py`**

Add after `class InvalidArgumentError` (line 36-42):

```python
class ResourceExhaustedError(InvalidArgumentError):
    """Over a resource budget (D5): maps to code RESOURCE_EXHAUSTED at the
    entry boundary; carries structured bytes data for the caller."""
    code = Code.RESOURCE_EXHAUSTED

    def __init__(self, message: str, data: dict = None):
        super().__init__(message)
        self.data = data
```

Replace the over-limit branch of `validate_message_size` (lines 86-89):

```python
    n = len(message.encode("utf-8"))
    if n > MAX_INLINE_BYTES:
        raise ResourceExhaustedError(
            f"message is {n} bytes, over the {MAX_INLINE_BYTES}-byte inline cap "
            f"(CC_COMMUNICATE_MAX_INLINE_BYTES); use artifact_refs instead",
            data={"limit_bytes": MAX_INLINE_BYTES, "actual_bytes": n})
    return message
```

- [ ] **Step 4: Implement the code mapping in `mcp_server._entry_error`**

Replace `_entry_error` (mcp_server.py:26-37):

```python
def _entry_error(*checks):
    """Run MCP-entry validators (HP-06). `checks` are (validator, value) pairs.
    Returns the error envelope (code from the exception - INVALID_ARGUMENT by
    default, RESOURCE_EXHAUSTED from ResourceExhaustedError), or None when all
    pass. Kernel dispatch validates again - defense in depth, and remote RPC
    never passes through here."""
    try:
        for validator, value in checks:
            validator(value)
    except validation.InvalidArgumentError as e:
        return {"ok": False, "code": getattr(e, "code", Code.INVALID_ARGUMENT),
                "message": str(e), "data": getattr(e, "data", None),
                "retryable": False}
    return None
```

- [ ] **Step 5: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_resource_limits.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/validation.py v2_win/cc-communicate/server/mcp_server.py tests/unit/test_resource_limits.py
git commit -m "feat(HP-09): RESOURCE_EXHAUSTED activated - over-limit inline messages return code + bytes data"
```

---

### Task 2: artifact_refs — schema validation + send path + record payload

**Files:**
- Modify: `v2_win/cc-communicate/server/validation.py` (`MAX_ARTIFACT_REFS`, `validate_artifact_refs`)
- Modify: `v2_win/cc-communicate/server/mcp_server.py` (`send_message` entry)
- Modify: `v2_win/cc-communicate/server/user_functions.py` (`_send` pass-through)
- Modify: `v2_win/cc-communicate/server/kernel.py` (`_ARG_VALIDATORS`)
- Modify: `v2_win/cc-communicate/server/message_record.py` (`new_record`)
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (`send_message` pass-through)
- Create: `tests/unit/test_artifact_refs.py` (validation matrix + payload tests)

**Interfaces:**
- Produces: `validation.validate_artifact_refs(value) -> list` (None → []; canonical 4-field dicts); `message_record.new_record(..., artifact_refs=None)`; `kernel_api.send_message(..., artifact_refs=None)`; `user_functions._send(..., artifact_refs=None)`; `mcp_server.send_message(..., artifact_refs=None)`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_artifact_refs.py`:

```python
"""HP-09: artifact_refs schema + record payload (delivery in Task 3)."""
import json
import os

import pytest
from result import Code

import mcp_server


def _conv_pair(server):
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    return k


def _pipe_records(server):
    d = server.conversations.conv_dir("a", "b")
    pipe = os.path.join(d, "pipe")
    out = []
    for fname in os.listdir(pipe):
        with open(os.path.join(pipe, fname), encoding="utf-8") as f:
            out.append(json.load(f))
    return out


def test_validate_artifact_refs_matrix(server):
    v = server.validation
    assert v.validate_artifact_refs(None) == []
    ok = v.validate_artifact_refs([{"path": "/tmp/x", "size": 10,
                                    "sha256": "a" * 64,
                                    "media_type": "text/plain"}])
    assert ok == [{"path": "/tmp/x", "size": 10, "sha256": "a" * 64,
                   "media_type": "text/plain"}]
    ok_uri = v.validate_artifact_refs([{"uri": "file:///tmp/x", "size": 1,
                                        "sha256": "b" * 64,
                                        "media_type": "x/y"}])
    assert ok_uri[0]["uri"] == "file:///tmp/x"
    bads = [
        [{"size": 10, "sha256": "a" * 64, "media_type": "t"}],           # neither
        [{"path": "/x", "uri": "file:///x", "size": 10, "sha256": "a" * 64,
          "media_type": "t"}],                                           # both
        [{"path": "", "size": 10, "sha256": "a" * 64, "media_type": "t"}],
        [{"path": "/x", "size": -1, "sha256": "a" * 64, "media_type": "t"}],
        [{"path": "/x", "size": "10", "sha256": "a" * 64, "media_type": "t"}],
        [{"path": "/x", "size": 10, "sha256": "A" * 64, "media_type": "t"}],
        [{"path": "/x", "size": 10, "sha256": "a" * 63, "media_type": "t"}],
        [{"path": "/x", "size": 10, "sha256": "a" * 64, "media_type": ""}],
        ["not-a-dict"],
    ]
    for refs in bads:
        with pytest.raises(v.InvalidArgumentError):
            v.validate_artifact_refs(refs)


def test_validate_artifact_refs_cap(server):
    v = server.validation
    v.MAX_ARTIFACT_REFS = 2
    refs = [{"path": f"/x{i}", "size": 1, "sha256": "a" * 64,
             "media_type": "t"} for i in range(3)]
    with pytest.raises(v.InvalidArgumentError):
        v.validate_artifact_refs(refs)


def test_send_with_refs_stores_payload(server):
    ka = server.kernel_api
    _conv_pair(server)
    refs = [{"path": "/tmp/build.log", "size": 2048, "sha256": "c" * 64,
             "media_type": "text/plain"}]
    r = ka.send_message(server.kernel.alive_conversations, {}, "store",
                        "a", "b", "build attached", artifact_refs=refs)
    assert r["sent"] is True
    recs = _pipe_records(server)
    assert len(recs) == 1
    assert recs[0]["payload"]["text"] == "build attached"
    assert recs[0]["payload"]["artifact_refs"] == refs


def test_send_without_refs_payload_unchanged(server):
    ka = server.kernel_api
    _conv_pair(server)
    ka.send_message(server.kernel.alive_conversations, {}, "store",
                    "a", "b", "plain")
    rec = _pipe_records(server)[0]
    assert "artifact_refs" not in rec["payload"]


def test_over_limit_text_with_refs_still_rejected(server):
    v = server.validation
    big = "x" * (v.MAX_INLINE_BYTES + 1)
    r = mcp_server.send_message("a", "b", big, artifact_refs=[
        {"path": "/tmp/x", "size": 1, "sha256": "a" * 64, "media_type": "t"}])
    assert r["ok"] is False and r["code"] == Code.RESOURCE_EXHAUSTED


def test_bad_refs_at_entry_rejected(server):
    v = server.validation
    r = mcp_server.send_message("a", "b", "hi", artifact_refs=[
        {"path": "/x", "size": -1, "sha256": "a" * 64, "media_type": "t"}])
    assert r["ok"] is False and r["code"] == Code.INVALID_ARGUMENT
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_artifact_refs.py -v`
Expected: FAIL — `AttributeError: module 'validation' has no attribute 'validate_artifact_refs'` (and mcp_server.send_message TypeError on unexpected kwarg)

- [ ] **Step 3: Implement `validate_artifact_refs` in `validation.py`**

Add next to `MAX_INLINE_BYTES` (line 32-33):

```python
# Max artifact_refs per message (D5; bounds the worst-case record size).
MAX_ARTIFACT_REFS = int(os.environ.get("CC_COMMUNICATE_MAX_ARTIFACT_REFS", "16"))
```

Add after `validate_message_size`:

```python
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def validate_artifact_refs(value) -> list:
    """artifact_refs (D5): [{path|uri (EXACTLY one), size int>=0, sha256
    64-hex, media_type non-empty str}], at most MAX_ARTIFACT_REFS entries.
    None -> []. Any violation raises InvalidArgumentError (schema error, not
    resource pressure). Returns canonical 4-field dicts (unknown keys
    dropped)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidArgumentError(
            f"artifact_refs must be a list; got {type(value).__name__}")
    if len(value) > MAX_ARTIFACT_REFS:
        raise InvalidArgumentError(
            f"artifact_refs has {len(value)} entries, over the "
            f"{MAX_ARTIFACT_REFS} cap (CC_COMMUNICATE_MAX_ARTIFACT_REFS)")
    out = []
    for i, ref in enumerate(value):
        if not isinstance(ref, dict):
            raise InvalidArgumentError(
                f"artifact_refs[{i}] must be a dict; got {type(ref).__name__}")
        loc = None
        for key in ("path", "uri"):
            if ref.get(key) is not None:
                loc = key
                break
        if loc is None:
            raise InvalidArgumentError(
                f"artifact_refs[{i}] needs exactly one of 'path'/'uri'")
        other = "uri" if loc == "path" else "path"
        if ref.get(other) is not None:
            raise InvalidArgumentError(
                f"artifact_refs[{i}] must have exactly one of 'path'/'uri' "
                f"(both present)")
        if not isinstance(ref[loc], str) or not ref[loc]:
            raise InvalidArgumentError(
                f"artifact_refs[{i}].{loc} must be a non-empty str")
        size = ref.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise InvalidArgumentError(
                f"artifact_refs[{i}].size must be an int >= 0; got {size!r}")
        sha = ref.get("sha256")
        if not isinstance(sha, str) or not _SHA256_RE.match(sha):
            raise InvalidArgumentError(
                f"artifact_refs[{i}].sha256 must be 64 lowercase hex chars")
        mt = ref.get("media_type")
        if not isinstance(mt, str) or not mt:
            raise InvalidArgumentError(
                f"artifact_refs[{i}].media_type must be a non-empty str")
        out.append({loc: ref[loc], "size": size, "sha256": sha,
                    "media_type": mt})
    return out
```

- [ ] **Step 4: Wire the send path (5 files)**

1. `mcp_server.py` `send_message` (lines 81-98): add the param + entry check:

```python
def send_message(fromid: str, toid: str, message: str,
                 correlation_id: str = None, kind: str = None,
                 artifact_refs: list = None) -> dict:
    """Send a message to a peer's pipe. Routes to the conversation store (host
    for cross-machine, else local). The conversation must be registered
    (normally via connect) first. Returns the envelope: ok({message_id, ts});
    err(NOT_FOUND) when not registered; err(INVALID_ARGUMENT) on a bad id;
    err(RESOURCE_EXHAUSTED) when the inline text exceeds
    CC_COMMUNICATE_MAX_INLINE_BYTES (retryable False - switch to
    artifact_refs). artifact_refs (D5): [{path|uri, size, sha256,
    media_type}] describing out-of-band content; rides in the record payload
    and is delivered to listeners."""
    checks = [(validation.validate_session_id, fromid),
              (validation.validate_session_id, toid),
              (validation.validate_message_size, message)]
    if correlation_id is not None:
        checks.append((validation.validate_message_id, correlation_id))
    if kind is not None:
        checks.append((validation.validate_message_id, kind))
    if artifact_refs is not None:
        checks.append((validation.validate_artifact_refs, artifact_refs))
    err = _entry_error(*checks)
    if err:
        return err
    return user_functions.send_message(fromid, toid, message,
                                       correlation_id=correlation_id, kind=kind,
                                       artifact_refs=artifact_refs)
```

2. `user_functions.py` `_send` (lines 133-146): add the param:

```python
def _send(fromid, toid, message, conv_remote, correlation_id=None, kind=None,
          artifact_refs=None):
    mid = uuid.uuid4().hex
    args = {"fromid": fromid, "toid": toid, "message": message, "message_id": mid}
    if correlation_id is not None:
        args["correlation_id"] = correlation_id
    if kind is not None:
        args["kind"] = kind
    if artifact_refs is not None:
        args["artifact_refs"] = artifact_refs
    if conv_remote is None:
        return rpc_client.call("send_message", args, operation_id=mid)
    return rpc_client.call_remote(conv_remote, "send_message", args, operation_id=mid)
```

3. `user_functions.py` `send_message` (lines 366-381): add the param + pass through (the backlog mapping lands in Task 4; keep NOT_FOUND mapping untouched here):

```python
def send_message(fromid: str, toid: str, message: str,
                 correlation_id: str = None, kind: str = None,
                 artifact_refs: list = None) -> dict:
    """Route by the conversation store (host for cross-machine, else local).
    ok({message_id, ts}) on success; err(NOT_FOUND) when the conversation is
    not registered; err(INTERNAL/PEER_UNREACHABLE) on transport failure."""
    conv_remote = _conv_store(toid)
    try:
        r = _send(fromid, toid, message, conv_remote,
                  correlation_id=correlation_id, kind=kind,
                  artifact_refs=artifact_refs)
    except KernelError as e:
        return _kernel_err(e)
    if r is None:
        return _remote_err()
    if r.get("sent"):
        return _ok({"message_id": r.get("message_id"), "ts": r.get("ts")})
    return _err(Code.NOT_FOUND, r.get("reason", "send failed"))
```

4. `kernel.py` `_ARG_VALIDATORS["send_message"]`: add the entry:

```python
    "send_message": {"fromid": validation.validate_session_id,
                     "toid": validation.validate_session_id,
                     "message": validation.validate_message_size,
                     "message_id": validation.validate_message_id,
                     "correlation_id": validation.validate_message_id,
                     "artifact_refs": validation.validate_artifact_refs},
```

5. `message_record.py` `new_record` (lines 37-52): add the param + payload key:

```python
def new_record(store_id: str, sequence: int, from_session: str, to_session: str,
               text: str, kind: str = "text", correlation_id=None,
               causation_id=None, message_id: str = None,
               artifact_refs: list = None) -> dict:
    payload = {"text": text}
    if artifact_refs:
        payload["artifact_refs"] = artifact_refs
    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": message_id or uuid.uuid4().hex,
        "store_id": store_id,
        "sequence": int(sequence),
        "from_session": from_session,
        "to_session": to_session,
        "kind": kind,
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "created_at_ms": int(time.time() * 1000),
        "payload": payload,
    }
```

6. `kernel_api.py` `send_message` (lines 105-132): add the param + pass through (docstring mentions artifact_refs):

```python
def send_message(alive_conversations: dict, message_sequence: dict, store_id: str,
                 fromid: str, toid: str, message: str, message_id: str = None,
                 kind: str = None, correlation_id: str = None,
                 artifact_refs: list = None) -> dict:
    """HP-01: allocate a per-store sequence, wrap the text in a v1 record,
    atomically publish. HP-03 dedup: a retry carrying the same message_id
    returns the ORIGINAL result without publishing a duplicate. HP-09:
    artifact_refs (D5) ride in the record payload (Task 3 delivers them).
    Structured dict result (HP-07) - callers branch on 'sent', never on text."""
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
                                    message_id=message_id, kind=kind or "text",
                                    correlation_id=correlation_id,
                                    artifact_refs=artifact_refs)
    message_record.publish(d, rec)
    return {"sent": True, "message_id": rec["message_id"], "ts": rec["created_at_ms"],
            "correlation_id": correlation_id}
```

- [ ] **Step 5: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_artifact_refs.py -v`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/validation.py v2_win/cc-communicate/server/mcp_server.py v2_win/cc-communicate/server/user_functions.py v2_win/cc-communicate/server/kernel.py v2_win/cc-communicate/server/message_record.py v2_win/cc-communicate/server/kernel_api.py tests/unit/test_artifact_refs.py
git commit -m "feat(HP-09): artifact_refs - schema validation (both boundaries) + record payload + send path (D5)"
```

---

### Task 3: artifact_refs delivery to legacy listeners

**Files:**
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (`_read_pipe_message`)
- Modify: `tests/unit/test_artifact_refs.py` (delivery tests)

**Interfaces:**
- Produces: legacy `listen_scan`/`collect_messages` message dicts carry `"artifact_refs"` when the record payload has them (absent otherwise — zero-change for ref-less records). `listen_v2` already returns raw records (no code change).

- [ ] **Step 1: Write the failing tests (append to `test_artifact_refs.py`)**

```python
def test_listen_v2_delivers_refs(server):
    ka = server.kernel_api
    _conv_pair(server)
    refs = [{"uri": "file:///x", "size": 5, "sha256": "d" * 64,
             "media_type": "text/plain"}]
    ka.send_message(server.kernel.alive_conversations, {}, "store",
                    "a", "b", "hi", artifact_refs=refs)
    res = ka.listen_scan_v2({}, "store", "b", 0)
    assert len(res["messages"]) == 1
    assert res["messages"][0]["payload"]["artifact_refs"] == refs


def test_legacy_listen_delivers_refs(server):
    ka = server.kernel_api
    _conv_pair(server)
    refs = [{"path": "/tmp/x", "size": 1, "sha256": "e" * 64,
             "media_type": "t"}]
    ka.send_message(server.kernel.alive_conversations, {}, "store",
                    "a", "b", "hi", artifact_refs=refs)
    res = ka.listen_scan({}, "b", 0)
    assert res["messages"][0]["artifact_refs"] == refs
    # ref-less records carry NO artifact_refs key (zero-change for old readers)
    ka.send_message(server.kernel.alive_conversations, {}, "store",
                    "a", "b", "plain")
    res2 = ka.listen_scan({}, "b", 0)
    assert "artifact_refs" not in res2["messages"][0]


def test_collect_messages_delivers_refs(server):
    ka = server.kernel_api
    _conv_pair(server)
    refs = [{"path": "/tmp/x", "size": 1, "sha256": "f" * 64,
             "media_type": "t"}]
    ka.send_message(server.kernel.alive_conversations, {}, "store",
                    "a", "b", "hi", artifact_refs=refs)
    msgs = ka.collect_messages("b")
    assert msgs[0]["artifact_refs"] == refs
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_artifact_refs.py -v`
Expected: FAIL — `test_legacy_listen_delivers_refs` and `test_collect_messages_delivers_refs` (KeyError/assert on missing `artifact_refs`); `test_listen_v2_delivers_refs` already passes (raw record)

- [ ] **Step 3: Implement in `kernel_api._read_pipe_message`**

In the record branch (kernel_api.py ~lines 325-334), add the refs key only when present:

```python
    if info["format"] == "record":
        rec = message_record.read_record(src)
        if not rec:
            return None
        payload = rec.get("payload") or {}
        out = {"time": rec.get("created_at_ms", 0),
               "from_id": rec.get("from_session"),
               "message": payload.get("text"),
               "message_id": rec.get("message_id"),
               "sequence": rec.get("sequence"),
               "store_id": rec.get("store_id"),
               "_sort": (1, 0, rec.get("sequence") or 0)}
        if payload.get("artifact_refs"):
            out["artifact_refs"] = payload["artifact_refs"]  # HP-09 (D5)
        return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_artifact_refs.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add v2_win/cc-communicate/server/kernel_api.py tests/unit/test_artifact_refs.py
git commit -m "feat(HP-09): artifact_refs delivered to legacy listen/collect_messages (v2 raw records already carry them)"
```

---

### Task 4: Backpressure cap (per-pair unacked) + user_functions mapping

**Files:**
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (`MAX_BACKLOG` + `send_message` check)
- Modify: `v2_win/cc-communicate/server/user_functions.py` (`send_message` mapping)
- Modify: `tests/unit/test_resource_limits.py` (backlog tests)

**Interfaces:**
- Produces: `kernel_api.MAX_BACKLOG` (int, env `CC_COMMUNICATE_MAX_BACKLOG`, default 1000); `send_message` returns `{"sent": False, "reason": "backlog full", "backlog": {"unacked", "cap"}}` when the pair's pipe count ≥ cap; `user_functions.send_message` maps key-presence `r.get("backlog")` → `err(RESOURCE_EXHAUSTED, retryable=True, data={"unacked", "cap"})`.

- [ ] **Step 1: Write the failing tests (append to `test_resource_limits.py`)**

```python
def test_send_backlog_cap_blocks(server):
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    ka.MAX_BACKLOG = 0
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    r = ka.send_message(k.alive_conversations, {}, "store", "a", "b", "hi")
    assert r == {"sent": False, "reason": "backlog full",
                 "backlog": {"unacked": 0, "cap": 0}}


def test_send_backlog_cap_releases_after_drain(server):
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    ka.MAX_BACKLOG = 1
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    r1 = ka.send_message(k.alive_conversations, {}, "store", "a", "b", "m1")
    assert r1["sent"] is True                      # pipe 0 -> 1 (exactly at cap)
    r2 = ka.send_message(k.alive_conversations, {}, "store", "a", "b", "m2")
    assert r2["sent"] is False and r2["backlog"]["unacked"] == 1
    # drain: bob confirms -> listen_scan archives what he's acked
    res = ka.listen_scan({}, "b", r1["ts"])
    assert res["messages"] == []                  # archived, not re-delivered
    r3 = ka.send_message(k.alive_conversations, {}, "store", "a", "b", "m3")
    assert r3["sent"] is True                     # backpressure released


def test_user_functions_backlog_maps_to_resource_exhausted(server, monkeypatch):
    import user_functions
    monkeypatch.setattr(user_functions, "_conv_store", lambda toid: None)
    monkeypatch.setattr(user_functions, "_send", lambda *a, **kw: {
        "sent": False, "reason": "backlog full",
        "backlog": {"unacked": 1000, "cap": 1000}})
    r = user_functions.send_message("a", "b", "hi")
    assert r["ok"] is False and r["code"] == Code.RESOURCE_EXHAUSTED
    assert r["retryable"] is True
    assert r["data"] == {"unacked": 1000, "cap": 1000}


def test_user_functions_not_registered_still_not_found(server, monkeypatch):
    import user_functions
    monkeypatch.setattr(user_functions, "_conv_store", lambda toid: None)
    monkeypatch.setattr(user_functions, "_send", lambda *a, **kw: {
        "sent": False, "reason": "connection not registered"})
    r = user_functions.send_message("a", "b", "hi")
    assert r["ok"] is False and r["code"] == Code.NOT_FOUND
    assert r["retryable"] is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_resource_limits.py -v`
Expected: FAIL — `test_send_backlog_cap_blocks` (no cap check yet), `test_user_functions_backlog_maps_to_resource_exhausted` (mapping missing → NOT_FOUND)

- [ ] **Step 3: Implement the cap in `kernel_api.py`**

Add the constant near the top (after the imports):

```python
# HP-09 (D5): per-pair unacked pipe cap - when the receiver's pipe holds this
# many undelivered messages, further sends are rejected (backpressure).
MAX_BACKLOG = int(os.environ.get("CC_COMMUNICATE_MAX_BACKLOG", "1000"))
```

In `send_message`, after the `_find_message_file` dedup block and BEFORE the sequence allocation (kernel_api.py ~line 122), insert:

```python
    # HP-09: backpressure - everything in pipe/ is unacked by definition.
    # Check BEFORE publish; single-threaded kernel => exact cap (each send
    # re-scans after the previous publish).
    pipe_dir = os.path.join(d, "pipe")
    try:
        unacked = len([n for n in os.listdir(pipe_dir)
                       if n.endswith((".json", ".md"))])
    except FileNotFoundError:
        unacked = 0
    if unacked >= MAX_BACKLOG:
        return {"sent": False, "reason": "backlog full",
                "backlog": {"unacked": unacked, "cap": MAX_BACKLOG}}
```

- [ ] **Step 4: Implement the mapping in `user_functions.py`**

Replace the tail of `send_message` (lines 379-381):

```python
    if r.get("sent"):
        return _ok({"message_id": r.get("message_id"), "ts": r.get("ts")})
    if r.get("backlog") is not None:
        # HP-09: peer hasn't acked enough - retry after it drains
        return _err(Code.RESOURCE_EXHAUSTED,
                    r.get("reason", "backlog full"),
                    data=r.get("backlog"), retryable=True)
    return _err(Code.NOT_FOUND, r.get("reason", "send failed"))
```

- [ ] **Step 5: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_resource_limits.py tests/unit/test_artifact_refs.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/kernel_api.py v2_win/cc-communicate/server/user_functions.py tests/unit/test_resource_limits.py
git commit -m "feat(HP-09): backpressure - per-pair unacked cap (CC_COMMUNICATE_MAX_BACKLOG) -> RESOURCE_EXHAUSTED retryable"
```

---

### Task 5: `backlog_stats` kernel function (observability)

**Files:**
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (`backlog_stats`)
- Modify: `v2_win/cc-communicate/server/kernel.py` (`_ARG_VALIDATORS` + `_dispatch`)
- Modify: `tests/unit/test_resource_limits.py` (stats tests)

**Interfaces:**
- Produces: `kernel_api.backlog_stats(session_id) -> {partner_sid: {"unacked": n, "bytes": m}}`; kernel dispatch route `backlog_stats` (NOT journaled — read-only).

- [ ] **Step 1: Write the failing tests (append to `test_resource_limits.py`)**

```python
def test_backlog_stats_counts_per_partner(server):
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    k.alive_conversations[("a", "c")] = {"established_at": 1.0}
    ka.send_message(k.alive_conversations, {}, "store", "b", "a", "to-a-1")
    ka.send_message(k.alive_conversations, {}, "store", "b", "a", "to-a-2")
    ka.send_message(k.alive_conversations, {}, "store", "c", "a", "to-a-3")
    ka.send_message(k.alive_conversations, {}, "store", "a", "b", "to-b-1")
    stats = ka.backlog_stats("a")
    assert stats["b"]["unacked"] == 2      # to-a-1, to-a-2 (to-a-3 is from c)
    assert stats["c"]["unacked"] == 1
    assert stats["b"]["bytes"] > 0
    assert ka.backlog_stats("zzz") == {}


def test_backlog_stats_direction_only(server):
    """Only messages ADDRESSED to sid count - sid's own outgoing messages do
    not inflate its backlog."""
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    ka.send_message(k.alive_conversations, {}, "store", "a", "b", "from-a")
    assert ka.backlog_stats("a")["b"]["unacked"] == 0
    assert ka.backlog_stats("b")["a"]["unacked"] == 1


def test_dispatch_routes_backlog_stats(server):
    k = server.kernel
    res = k._dispatch("backlog_stats", {"session_id": "a"})
    assert isinstance(res, dict)
    with pytest.raises(server.validation.InvalidArgumentError):
        k._dispatch("backlog_stats", {"session_id": "../evil"})
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_resource_limits.py -v`
Expected: FAIL — `AttributeError: module 'kernel_api' has no attribute 'backlog_stats'`; dispatch raises ValueError unknown function

- [ ] **Step 3: Implement in `kernel_api.py`**

Add at the end (after `run_gc`):

```python
def backlog_stats(session_id: str) -> dict:
    """HP-09: per-partner unacked backlog addressed TO sid (pipe files, both
    formats) + bytes. Read-only; kernel function ONLY (HP-12 surfaces
    observability as a tool)."""
    validation.validate_session_id(session_id)
    result = {}
    try:
        entries = os.listdir(CONVERSATIONS_DIR)
    except FileNotFoundError:
        return result
    for name in entries:
        parts = name.split(conversations.SEP)
        if len(parts) != 2 or session_id not in parts:
            continue
        partner = parts[1] if parts[0] == session_id else parts[0]
        pipe = os.path.join(CONVERSATIONS_DIR, name, "pipe")
        if not os.path.isdir(pipe):
            continue
        unacked = 0
        total_bytes = 0
        try:
            fnames = os.listdir(pipe)
        except OSError:
            fnames = []
        for fname in fnames:
            info = conversations.parse_any_pipe_filename(fname)
            if not info or info["to_id"] != session_id:
                continue
            unacked += 1
            try:
                total_bytes += os.path.getsize(os.path.join(pipe, fname))
            except OSError:
                pass
        result[partner] = {"unacked": unacked, "bytes": total_bytes}
    return result
```

- [ ] **Step 4: Wire dispatch in `kernel.py`**

In `_ARG_VALIDATORS` (after `run_gc`):

```python
    "backlog_stats": {"session_id": validation.validate_session_id},
```

In `_dispatch` (after the `run_gc` branch):

```python
    if function == "backlog_stats":
        return kernel_api.backlog_stats(args["session_id"])
```

- [ ] **Step 5: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_resource_limits.py -v`
Expected: PASS (all 11 tests in the file)

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/kernel_api.py v2_win/cc-communicate/server/kernel.py tests/unit/test_resource_limits.py
git commit -m "feat(HP-09): backlog_stats kernel function - per-partner unacked + bytes (observability, not an MCP tool)"
```

---

### Task 6: SKILL.md + parity sync + gate + records

**Files:**
- Modify: `v2_win/cc-communicate/skills/cc-communicate/SKILL.md`
- Sync: `v2_wsl/cc-communicate/` ← modified server files + SKILL.md
- Modify: `tested&2betest.md` (T42 record)

**Interfaces:**
- Consumes: all Tasks 1-5 outputs.

- [ ] **Step 1: Update SKILL.md**

1. Codes paragraph (line ~31-32), replace:

```markdown
  `NOT_ALIVE` (the target could not be revived), `RESOURCE_EXHAUSTED`
  (reserved for the Wave 3 resource policy; no tool returns it yet),
  `INTERNAL` (kernel / transport failure).
```

with:

```markdown
  `NOT_ALIVE` (the target could not be revived), `RESOURCE_EXHAUSTED`
  (inline cap exceeded - retryable False, switch to artifact_refs; or the
  peer's unacked backlog is full - retryable True, retry after it drains),
  `INTERNAL` (kernel / transport failure).
```

2. `send_message` doc (line ~188-198), replace the tail:

```markdown
  `kind`: free-form tag, defaults to `"text"` (`"hello"` for connect's
  handshake). Messages are capped at 1 MiB inline
  (`CC_COMMUNICATE_MAX_INLINE_BYTES`); over the cap ->
  `err(INVALID_ARGUMENT)`.
```

with:

```markdown
  `kind`: free-form tag, defaults to `"text"` (`"hello"` for connect's
  handshake). Messages are capped at 1 MiB inline
  (`CC_COMMUNICATE_MAX_INLINE_BYTES`); over the cap ->
  `err(RESOURCE_EXHAUSTED)` with `data = {limit_bytes, actual_bytes}` -
  attach the content out-of-band instead: `artifact_refs=[{path|uri, size,
  sha256, media_type}]` (max 16, exactly one of path/uri, sha256 = 64
  lowercase hex); refs ride in the record payload and are delivered to
  listeners. When the peer's unacked backlog is full
  (`CC_COMMUNICATE_MAX_BACKLOG`, default 1000) the send fails with
  `err(RESOURCE_EXHAUSTED, retryable=True)` and
  `data = {unacked, cap}` - the peer acks and you retry.
```

- [ ] **Step 2: Sync v2_wsl + full suite + parity**

```bash
cp v2_win/cc-communicate/server/{kernel,kernel_api,message_record,mcp_server,user_functions,validation}.py v2_wsl/cc-communicate/server/
cp v2_win/cc-communicate/skills/cc-communicate/SKILL.md v2_wsl/cc-communicate/skills/cc-communicate/SKILL.md
py -3 -m pytest -q
py -3 tools/check_parity.py
```

Expected: full suite PASS; `PARITY OK (30 files compared, allowlist=['.mcp.json'])`

- [ ] **Step 3: Run the full auto gate**

Run: `py -3 tools/run_regression.py --tier auto`
Expected: `GATE PASS` (T0 syntax, T1 pytest, T2 parity)

- [ ] **Step 4: Record T42 in `tested&2betest.md` §1**

Append:

```markdown
### T42 — HP-09 unit acceptance: RESOURCE_EXHAUSTED activated + artifact_refs + backpressure

- **Method**: unit (test_resource_limits.py / test_artifact_refs.py):
  over-limit inline text -> RESOURCE_EXHAUSTED envelope with
  {limit_bytes, actual_bytes} (dormant code activated); artifact_refs schema
  validation matrix (both trust boundaries), record payload carries refs,
  delivered via listen_v2 (raw record) + legacy listen/collect_messages;
  over-limit text WITH refs still rejected; per-pair unacked cap
  (CC_COMMUNICATE_MAX_BACKLOG) -> RESOURCE_EXHAUSTED retryable, releases
  after drain; backlog_stats kernel function per-partner counts+bytes.
  Full auto gate `py -3 tools/run_regression.py --tier auto` -> GATE PASS.
- **Result**: PASS (unit + auto gate). Live gates (full L1-L6, incl. the
  mandated L3/L4) deferred to the Wave 3 exit gate per the user's locked
  decision.
- **Confidence**: high for unit semantics; live verification at Wave 3 exit.
```

- [ ] **Step 5: Commit**

```bash
git add v2_win/cc-communicate/skills/cc-communicate/SKILL.md v2_wsl/cc-communicate v2_wsl/cc-communicate/skills/cc-communicate/SKILL.md tested&2betest.md
git commit -m "docs(W3/HP-09): SKILL.md RESOURCE_EXHAUSTED + artifact_refs docs, parity sync, auto gate PASS, T42 record"
```

HP-09 is done. Wave 3 continues with HP-10 (next design), then HP-11(余); the full live L1-L6 gate runs at the Wave 3 exit.
