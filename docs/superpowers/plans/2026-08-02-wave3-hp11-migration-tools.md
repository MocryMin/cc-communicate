# HP-11(余) Migration Tools + Schema Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution for THIS wave (user mandate, durable): INLINE — no context-heavy subagents.** Execute task-by-task in the session (executing-plans style), `py -3 tools/run_regression.py --tier auto` as the exit gate. Live gates (full L1–L6) run at the WAVE 3 exit (this is the last code wave item).

**Goal:** Make a custom data root recoverable (D2): a migration/validation CLI (`tools/migrate_data.py`) + a shared `server/schema.py` (version constant, check/stamp/wrap/layout logic) + kernel loaders that refuse newer-format state files and dual-read the wrapped v1 registries.

**Architecture:** `schema.py` is the single source of truth (no `paths` import — it works off an arbitrary data root). Three flat registries (sessions / alive_conversations / ack_timestamps) cannot be stamped in place (a `schema_version` key would be misread as a session/ack entry; a list can't carry one) — they **wrap-migrate** to `{schema_version: 1, <key>: <payload>}`; loaders dual-read (HP-01 legacy-.md precedent); writers emit the wrapped shape. `machine_identity.json` stamps in place (extra key ignored). The CLI sets `CC_COMMUNICATE_DATA_DIR` before importing server modules and refuses (exit 1, untouched) any newer-schema file.

**Tech Stack:** Python 3 (`py -3`), pytest (incl. subprocess tests for the CLI). No new dependencies.

## Global Constraints

- **`py -3` for ALL Python** on Windows (git-bash; quote paths with spaces/CJK).
- **Tests isolated**: conftest `server` fixture sets `CC_COMMUNICATE_DATA_DIR` → tmp_path and reloads modules per test. `"schema"` MUST be added to the reload list (Task 2, before `kernel`).
- **Parity**: v2_win ↔ v2_wsl byte-identical outside `.mcp.json`; `server/schema.py` is server-level → synced; `tools/migrate_data.py` is repo-tools level (NOT synced, like check_parity.py).
- **Commit format**: `feat/fix/test/docs(scope): subject` + `Co-Authored-By: Claude <noreply@anthropic.com>`; work on main (user consent).
- **Records**: every bug found during implementation gets a T# entry in `tested&2betest.md` §1.
- **Run from repo root** `C:\研究生\实习\learn AI\projects\cc-communicate`; per-task test: `py -3 -m pytest tests/unit/test_X.py -v`; full: `py -3 -m pytest -q`.
- **Design spec**: `docs/superpowers/specs/2026-08-02-wave3-hp11-migration-tools-design.md` (user-approved, incl. the wrap-migration amendment). Deviations require user approval.
- **Dual-read discipline** (HP-01 precedent): the new wrapped shapes are ADDITIVE — legacy flat/list files must keep loading until the tool migrates them.

---

### Task 1: `server/schema.py` — version constant + check/stamp/wrap/layout

**Files:**
- Create: `v2_win/cc-communicate/server/schema.py`
- Create: `tests/unit/test_schema.py`

**Interfaces:**
- Produces: `schema.SUPPORTED_SCHEMA = 1`; `schema.schema_too_new(data, supported=1) -> bool`; `schema.unwrap(data, key)`; `schema.stamp_v1(path) -> bool`; `schema.needs_stamp(path) -> bool`; `schema.wrap_v1(path, key) -> bool`; `schema.needs_wrap(path, key) -> bool`; `schema.validate_layout(root) -> (errors, warnings)`; constants `WRAP_TARGETS` / `STAMP_TARGETS`.
- Consumes: `fileutil.atomic_write_json` only (NO `paths` import — root-parametric).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_schema.py`:

```python
"""HP-11: data-root schema conventions - check/stamp/wrap/layout (D2)."""
import json
import os


def test_schema_too_new_matrix(server):
    s = server.schema
    assert s.schema_too_new(None) is False
    assert s.schema_too_new("x") is False
    assert s.schema_too_new({}) is False
    assert s.schema_too_new({"schema_version": 1}) is False
    assert s.schema_too_new({"schema_version": 2}) is True
    assert s.schema_too_new({"schema_version": 0}) is False
    assert s.schema_too_new({"schema_version": -1}) is False
    assert s.schema_too_new({"schema_version": "2"}) is False   # non-int tolerated
    assert s.schema_too_new({"schema_version": True}) is False  # bool is not a version


def test_unwrap(server):
    s = server.schema
    assert s.unwrap({"schema_version": 1, "sessions": {"s1": {}}},
                    "sessions") == {"s1": {}}
    assert s.unwrap({"schema_version": 1,
                     "conversations": [["a", "b", {}]]},
                    "conversations") == [["a", "b", {}]]
    assert s.unwrap({"schema_version": 1}, "sessions") is None  # wrapped, key missing
    assert s.unwrap({"s1": {}}, "sessions") == {"s1": {}}       # legacy passthrough
    assert s.unwrap(["a"], "conversations") == ["a"]            # legacy list passthrough
    assert s.unwrap(None, "sessions") is None


def test_stamp_and_wrap(server):
    s = server.schema
    os.makedirs(server.paths.SERVER_DATA_DIR, exist_ok=True)
    # stamp: machine_identity-style flat dict (extra key ignored by its loader)
    p = os.path.join(server.paths.SERVER_DATA_DIR, "machine_identity.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"type": "win-host"}, f)
    assert s.needs_stamp(p) is True
    assert s.stamp_v1(p) is True
    with open(p, encoding="utf-8") as f:
        assert json.load(f)["schema_version"] == 1
    assert s.stamp_v1(p) is False          # already stamped -> no-op
    assert s.needs_stamp(p) is False
    # wrap: flat sessions dict
    p2 = os.path.join(server.paths.SERVER_DATA_DIR, "sessions.json")
    with open(p2, "w", encoding="utf-8") as f:
        json.dump({"s1": {"pid": 1}}, f)
    assert s.needs_wrap(p2, "sessions") is True
    assert s.wrap_v1(p2, "sessions") is True
    with open(p2, encoding="utf-8") as f:
        assert json.load(f) == {"schema_version": 1,
                                "sessions": {"s1": {"pid": 1}}}
    assert s.wrap_v1(p2, "sessions") is False  # already wrapped -> no-op
    assert s.needs_wrap(p2, "sessions") is False
    # wrap: bare list (alive_conversations)
    p3 = os.path.join(server.paths.SERVER_DATA_DIR, "alive_conversations.json")
    with open(p3, "w", encoding="utf-8") as f:
        json.dump([["a", "b", {}]], f)
    assert s.wrap_v1(p3, "conversations") is True
    with open(p3, encoding="utf-8") as f:
        assert json.load(f)["conversations"] == [["a", "b", {}]]


def test_validate_layout(server):
    s = server.schema
    root = str(server.data_root)
    # fresh root: dir warnings, no errors
    errors, warnings = s.validate_layout(root)
    assert errors == []
    assert any("missing runtime dir" in w for w in warnings)
    # newer file -> error (REFUSED)
    os.makedirs(server.paths.SERVER_DATA_DIR, exist_ok=True)
    with open(os.path.join(server.paths.SERVER_DATA_DIR, "sessions.json"),
              "w", encoding="utf-8") as f:
        json.dump({"schema_version": 2}, f)
    errors, warnings = s.validate_layout(root)
    assert any("REFUSED" in e for e in errors)
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'schema'` (the conftest reload list does not include it yet — `server.schema` AttributeError)

- [ ] **Step 3: Implement `server/schema.py`**

Create `v2_win/cc-communicate/server/schema.py`:

```python
"""Data-root schema conventions + migration helpers (HP-11, D2).

Single source of truth for the supported schema_version and the
check/stamp/wrap/layout logic shared by the kernel loaders and
tools/migrate_data.py. Deliberately does NOT import paths - it works off an
arbitrary data root.

Rules:
  - schema_too_new(): an int schema_version > SUPPORTED_SCHEMA means a NEWER
    plugin wrote the file - refuse to interpret it (skip + loud log). The
    file is NEVER touched by this wave's code.
  - Missing / non-int schema_version is TOLERATED: today's v1 files were
    written before the convention was universal.
  - wrap_v1(): the flat registries (sessions / alive_conversations /
    ack_timestamps) CANNOT be stamped in place - a schema_version key would
    be misread as a session/ack entry, and a bare list cannot carry one.
    They wrap to {schema_version: 1, <key>: <payload>}; loaders dual-read
    (HP-01 legacy-.md precedent).
  - stamp_v1(): add-key stamp for self-describing dict files
    (machine_identity - its loader ignores unknown keys).
"""
from __future__ import annotations

import json
import os

import fileutil

SUPPORTED_SCHEMA = 1

# Persistent state files checked for schema_version (relative to the data root).
STATE_FILES = (
    "server/sessions.json", "server/alive_conversations.json",
    "server/ack_timestamps.json", "server/message_sequence.json",
    "server/cursors.json", "server/operation_journal.json",
    "server/machine_identity.json", "server/gc_state.json",
)
# Flat registries that predate the universal stamp: wrap to the versioned
# shape (cannot be stamped in place - see module docstring).
WRAP_TARGETS = (("server/sessions.json", "sessions"),
                ("server/alive_conversations.json", "conversations"),
                ("server/ack_timestamps.json", "ack_timestamps"))
# Self-describing dict files: add-key stamp.
STAMP_TARGETS = ("server/machine_identity.json",)

REQUIRED_DIRS = ("server", "session_ctrl", "queue", "queue/responses",
                 "conversations", "pending_spawn", "machine_info_log")


def schema_too_new(data, supported: int = SUPPORTED_SCHEMA) -> bool:
    """True only for an int schema_version > supported (a newer plugin wrote
    the file). Missing / non-int / bool / <= supported -> False."""
    if not isinstance(data, dict):
        return False
    v = data.get("schema_version")
    return isinstance(v, int) and not isinstance(v, bool) and v > supported


def unwrap(data, key: str):
    """Dual-read helper (HP-01 precedent): a wrapped dict
    {schema_version, <key>: payload} -> payload; anything else passes
    through unchanged (legacy flat shapes). Wrapped-but-missing-key -> None."""
    if isinstance(data, dict) and "schema_version" in data:
        payload = data.get(key)
        return payload if isinstance(payload, (dict, list)) else None
    return data


def needs_stamp(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "schema_version" not in data


def stamp_v1(path: str) -> bool:
    """Add schema_version: 1 to an unstamped dict file (atomic). Returns
    True when it stamped; False for absent/non-dict/already-stamped."""
    if not needs_stamp(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    data["schema_version"] = SUPPORTED_SCHEMA
    fileutil.atomic_write_json(path, data)
    return True


def needs_wrap(path: str, key: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    return not (isinstance(data, dict) and "schema_version" in data)


def wrap_v1(path: str, key: str) -> bool:
    """Wrap a flat v1 registry into {schema_version: 1, <key>: <payload>}
    (atomic). Returns True when it wrapped; False for absent/non-JSON or
    already-wrapped files."""
    if not needs_wrap(path, key):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    fileutil.atomic_write_json(
        path, {"schema_version": SUPPORTED_SCHEMA, key: data})
    return True


def validate_layout(root: str):
    """(errors, warnings): errors = state files with a NEWER schema_version
    (REFUSED - never touched); warnings = missing runtime dirs (advisory -
    the kernel creates them) + unreadable state files."""
    errors, warnings = [], []
    for rel in REQUIRED_DIRS:
        if not os.path.isdir(os.path.join(root, rel)):
            warnings.append(f"missing runtime dir: {rel} (kernel creates it)")
    for rel in STATE_FILES:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            warnings.append(f"unreadable state file: {rel}")
            continue
        if schema_too_new(data):
            errors.append(
                f"{rel}: schema_version {data.get('schema_version')} > "
                f"supported {SUPPORTED_SCHEMA} - REFUSED (untouched)")
    # connection info files + pending markers
    cdir = os.path.join(root, "conversations")
    if os.path.isdir(cdir):
        for name in os.listdir(cdir):
            info = os.path.join(cdir, name, "info.json")
            if os.path.isfile(info):
                try:
                    with open(info, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    continue
                if schema_too_new(data):
                    errors.append(
                        f"conversations/{name}/info.json: schema_version "
                        f"{data.get('schema_version')} > supported "
                        f"{SUPPORTED_SCHEMA} - REFUSED (untouched)")
    pdir = os.path.join(root, "pending_spawn")
    if os.path.isdir(pdir):
        for name in os.listdir(pdir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(pdir, name)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if schema_too_new(data):
                errors.append(
                    f"pending_spawn/{name}: schema_version "
                    f"{data.get('schema_version')} > supported "
                    f"{SUPPORTED_SCHEMA} - REFUSED (untouched)")
    return errors, warnings
```

- [ ] **Step 4: Add `"schema"` to the conftest reload list**

In `tests/conftest.py`, after `"cleanup"`:

```python
    for name in ("paths", "result", "validation", "proc", "conversations",
                 "spawn", "cleanup", "schema", "machine_identity",
                 "check_core", "rpc_client", "kernel_api", "kernel"):
```

- [ ] **Step 5: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_schema.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/schema.py tests/conftest.py tests/unit/test_schema.py
git commit -m "feat(HP-11): schema.py - version constant + check/stamp/wrap/layout helpers (single source of truth)"
```

---

### Task 2: Kernel loaders — schema guard + dual-read + wrapped writers

**Files:**
- Modify: `v2_win/cc-communicate/server/kernel.py` (6 loaders + 3 savers)
- Modify: `v2_win/cc-communicate/server/kernel_api.py` (`upload_ack_timestamp`)
- Modify: `v2_win/cc-communicate/server/operation_journal.py` (`load` gains a pre-read `data` param)
- Create: `tests/unit/test_kernel_schema_guard.py`

**Interfaces:**
- Produces: `_load_sessions`/`_load_alive_convs`/`_load_ack_timestamps` dual-read (legacy + wrapped v1) with the too-new guard; the other three loaders get the guard; `_save_sessions`/`_save_alive_convs`/`_save_ack_timestamps` + `kernel_api.upload_ack_timestamp` emit `{schema_version: 1, <key>: <payload>}`; `operation_journal.load(path, data=None)`.
- Consumes: `schema.schema_too_new`, `schema.unwrap` (Task 1).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_kernel_schema_guard.py`:

```python
"""HP-11: kernel loaders refuse newer-format state files + dual-read the
wrapped v1 registries (HP-01 dual-reader precedent)."""
import json
import logging
import os


def test_load_sessions_skips_newer_schema(server, caplog):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    with open(server.paths.SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 2, "sessions": {"s1": {}}}, f)
    with caplog.at_level(logging.WARNING, logger="cc-communicate.kernel"):
        k._load_sessions()
    assert k.sessions == {}
    assert any("schema_version 2" in r.message for r in caplog.records)
    with open(server.paths.SESSIONS_FILE, encoding="utf-8") as f:
        assert json.load(f)["schema_version"] == 2   # file untouched


def test_load_sessions_dual_read_legacy_and_wrapped(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    with open(server.paths.SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump({"s1": {"pid": 1}}, f)             # legacy flat
    k._load_sessions()
    assert "s1" in k.sessions
    k.sessions.clear()
    with open(server.paths.SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "sessions": {"s2": {"pid": 2}}}, f)
    k._load_sessions()
    assert "s2" in k.sessions


def test_save_sessions_emits_wrapped(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k.sessions.update({"s1": {"pid": 1}})
    k._save_sessions()
    with open(server.paths.SESSIONS_FILE, encoding="utf-8") as f:
        assert json.load(f) == {"schema_version": 1,
                                "sessions": {"s1": {"pid": 1}}}


def test_load_alive_convs_dual_read(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    with open(server.paths.ALIVE_CONVS_FILE, "w", encoding="utf-8") as f:
        json.dump([["a", "b", {"established_at": 1.0}]], f)
    k._load_alive_convs()
    assert ("a", "b") in k.alive_conversations
    k.alive_conversations.clear()
    with open(server.paths.ALIVE_CONVS_FILE, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1,
                   "conversations": [["c", "d", {}]]}, f)
    k._load_alive_convs()
    assert ("c", "d") in k.alive_conversations


def test_load_ack_timestamps_dual_read(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    with open(server.paths.ACK_TIMESTAMPS_FILE, "w", encoding="utf-8") as f:
        json.dump({"s1": 42}, f)
    k._load_ack_timestamps()
    assert k.acked_timestamps["s1"] == 42
    k.acked_timestamps.clear()
    with open(server.paths.ACK_TIMESTAMPS_FILE, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "ack_timestamps": {"s2": 7}}, f)
    k._load_ack_timestamps()
    assert k.acked_timestamps["s2"] == 7


def test_save_ack_timestamps_emits_wrapped(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k.acked_timestamps["s1"] = 42
    k._save_ack_timestamps()
    with open(server.paths.ACK_TIMESTAMPS_FILE, encoding="utf-8") as f:
        assert json.load(f) == {"schema_version": 1,
                                "ack_timestamps": {"s1": 42}}


def test_upload_ack_timestamp_emits_wrapped(server):
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    ka.upload_ack_timestamp({}, "s1", 42)
    with open(server.paths.ACK_TIMESTAMPS_FILE, encoding="utf-8") as f:
        assert json.load(f) == {"schema_version": 1,
                                "ack_timestamps": {"s1": 42}}


def test_load_message_sequence_skips_newer(server, caplog):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    with open(server.paths.MESSAGE_SEQUENCE_FILE, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 2, "last_allocated": 99}, f)
    with caplog.at_level(logging.WARNING, logger="cc-communicate.kernel"):
        k._load_message_sequence()
    assert k.message_sequence["last_allocated"] == 0   # default, not 99
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_kernel_schema_guard.py -v`
Expected: FAIL — newer-schema files load today (no guard); wrapped shapes fail to load (no unwrap); savers emit flat shapes

- [ ] **Step 3: Implement in `kernel.py`**

1. Add `import schema` to the imports.
2. `_load_sessions` (lines 85-89):

```python
def _load_sessions():
    data = _read_json(SESSIONS_FILE)
    if schema.schema_too_new(data):
        log.warning("sessions.json schema_version %s > supported %s - "
                    "skipping (file untouched)", data.get("schema_version"),
                    schema.SUPPORTED_SCHEMA)
        return
    data = schema.unwrap(data, "sessions")
    if isinstance(data, dict):
        sessions.update(data)
        log.info("loaded sessions.json: %d sessions", len(sessions))
```

3. `_load_alive_convs` (lines 96-110):

```python
def _load_alive_convs():
    """Reload registered conversations from disk (R2). alive_conversations is
    otherwise in-memory and would be lost on every kernel restart (crash / idle
    exit / terminate), breaking all in-flight send_message calls. Persisted as
    {schema_version: 1, conversations: [[a, b, info], ...]} (legacy bare list
    still read - HP-11 dual-read); the pair is already canonical (sorted) when
    stored."""
    data = _read_json(ALIVE_CONVS_FILE)
    if schema.schema_too_new(data):
        log.warning("alive_conversations.json schema_version %s > supported "
                    "%s - skipping (file untouched)", data.get("schema_version"),
                    schema.SUPPORTED_SCHEMA)
        return
    data = schema.unwrap(data, "conversations")
    if not isinstance(data, list):
        return
    for entry in data:
        if isinstance(entry, list) and len(entry) >= 2:
            a, b = entry[0], entry[1]
            info = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
            alive_conversations[(a, b)] = info
    log.info("loaded alive_conversations.json: %d convs", len(alive_conversations))
```

4. `_load_ack_timestamps` (lines 118-129):

```python
def _load_ack_timestamps():
    """Reload per-sid ACK watermarks from disk (T24). acked_timestamps is
    otherwise in-memory; listen_scan updates it in memory (frequent, no I/O) and
    upload_ack_timestamp persists immediately (on close). Persisted as
    {schema_version: 1, ack_timestamps: {sid: ts}} (legacy flat dict still
    read - HP-11 dual-read). This load catches the case where the kernel
    restarts mid-conversation - the CC can recover its ts via
    query_my_ACK_timestamp."""
    data = _read_json(ACK_TIMESTAMPS_FILE)
    if schema.schema_too_new(data):
        log.warning("ack_timestamps.json schema_version %s > supported %s - "
                    "skipping (file untouched)", data.get("schema_version"),
                    schema.SUPPORTED_SCHEMA)
        return
    data = schema.unwrap(data, "ack_timestamps")
    if isinstance(data, dict):
        for sid, ts in data.items():
            if isinstance(ts, (int, float)):
                acked_timestamps[sid] = int(ts)
        log.info("loaded ack_timestamps.json: %d sids", len(acked_timestamps))
```

5. `_load_message_sequence` — guard after the read (line 141: `data = _read_json(...)`), insert before the `state = ...` line:

```python
    if schema.schema_too_new(data):
        log.warning("message_sequence.json schema_version %s > supported %s - "
                    "skipping (file untouched; sequence heals from files)",
                    data.get("schema_version"), schema.SUPPORTED_SCHEMA)
        data = None
```

6. `_load_cursors` — guard after the read (line ~173):

```python
    data = _read_json(CURSORS_FILE)
    if schema.schema_too_new(data):
        log.warning("cursors.json schema_version %s > supported %s - "
                    "skipping (file untouched)", data.get("schema_version"),
                    schema.SUPPORTED_SCHEMA)
        return
```

7. `_load_operation_journal` — guard + pass the pre-read data (lines 196-199):

```python
def _load_operation_journal():
    data = _read_json(OPERATION_JOURNAL_FILE)
    if schema.schema_too_new(data):
        log.warning("operation_journal.json schema_version %s > supported %s - "
                    "skipping (file untouched)", data.get("schema_version"),
                    schema.SUPPORTED_SCHEMA)
        data = None
    operation_journal.clear()
    if data is not None:
        operation_journal.update(operation_journal_mod.load(OPERATION_JOURNAL_FILE, data))
    log.info("loaded operation journal: %d entries", len(operation_journal))
```

8. Writers — wrapped shapes (`_save_sessions`, `_save_alive_convs`, `_save_ack_timestamps`):

```python
def _save_sessions():
    _atomic_write_json(SESSIONS_FILE,
                       {"schema_version": 1, "sessions": sessions})


def _save_alive_convs():
    data = [[a, b, info] for (a, b), info in alive_conversations.items()]
    _atomic_write_json(ALIVE_CONVS_FILE,
                       {"schema_version": 1, "conversations": data})


def _save_ack_timestamps():
    _atomic_write_json(ACK_TIMESTAMPS_FILE,
                       {"schema_version": 1, "ack_timestamps": acked_timestamps})
```

- [ ] **Step 4: Implement in `kernel_api.py` + `operation_journal.py`**

1. `kernel_api.upload_ack_timestamp` (the `_atomic_write_json(ACK_TIMESTAMPS_FILE, acked_timestamps)` call, line ~458):

```python
    try:
        _atomic_write_json(ACK_TIMESTAMPS_FILE,
                           {"schema_version": 1, "ack_timestamps": acked_timestamps})
    except OSError:
        pass
```

2. `operation_journal.py` `load` (lines 32-41) — accept pre-read data (the guard already ran kernel-side); the parsing/validation logic is unchanged:

```python
def load(path: str, data=None) -> dict:
    """-> {operation_id: entry}. Tolerant: any read problem -> empty journal.
    `data` may be passed pre-read (the kernel applies the HP-11 schema guard
    before calling); None -> read the file."""
    if data is None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
    if isinstance(data, dict) and isinstance(data.get("operations"), dict):
        return data["operations"]
    return {}
```

- [ ] **Step 5: Run the full suite**

Run: `py -3 -m pytest -q`
Expected: PASS — new guard tests + `test_kernel_restart.py` roundtrip (wrapped write → dual-read) + everything else

- [ ] **Step 6: Commit**

```bash
git add v2_win/cc-communicate/server/kernel.py v2_win/cc-communicate/server/kernel_api.py v2_win/cc-communicate/server/operation_journal.py tests/unit/test_kernel_schema_guard.py
git commit -m "feat(HP-11): kernel loaders refuse newer schemas + dual-read wrapped v1 registries; writers emit versioned shapes"
```

---

### Task 3: `tools/migrate_data.py` CLI

**Files:**
- Create: `tools/migrate_data.py`
- Create: `tests/unit/test_migrate_data.py` (subprocess tests)

**Interfaces:**
- Produces: `migrate_data.main(argv=None) -> int`; CLI `py -3 tools/migrate_data.py --data-root <dir> [--dry-run]`; exit 0 = no errors (warnings ok), exit 1 = any newer-schema file.
- Consumes: `schema.validate_layout`, `schema.WRAP_TARGETS`, `schema.STAMP_TARGETS`, `schema.wrap_v1`/`stamp_v1`/`needs_wrap`/`needs_stamp` (Tasks 1).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_migrate_data.py`:

```python
"""HP-11: migrate_data.py CLI via SUBPROCESS (in-process imports would clash
with the conftest module state - the CLI sets CC_COMMUNICATE_DATA_DIR itself)."""
import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, "tools", "migrate_data.py")


def _run(root, *extra):
    return subprocess.run(
        [sys.executable, TOOL, "--data-root", str(root)] + list(extra),
        capture_output=True, text=True, cwd=REPO)


def test_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "data"
    server = root / "server"
    server.mkdir(parents=True)
    (server / "sessions.json").write_text('{"s1": {"pid": 1}}', encoding="utf-8")
    r = _run(root, "--dry-run")
    assert r.returncode == 0
    assert "OK" in r.stdout
    assert "WOULD" in r.stdout
    assert json.loads((server / "sessions.json").read_text(encoding="utf-8")) == \
        {"s1": {"pid": 1}}   # untouched


def test_migrates_flat_registries(tmp_path):
    root = tmp_path / "data"
    server = root / "server"
    server.mkdir(parents=True)
    (server / "sessions.json").write_text('{"s1": {"pid": 1}}', encoding="utf-8")
    (server / "alive_conversations.json").write_text(
        '[["a", "b", {}]]', encoding="utf-8")
    (server / "ack_timestamps.json").write_text('{"s1": 42}', encoding="utf-8")
    (server / "machine_identity.json").write_text(
        '{"type": "win-host"}', encoding="utf-8")
    r = _run(root)
    assert r.returncode == 0 and "OK" in r.stdout
    assert "migrated 4 file(s)" in r.stdout
    assert json.loads((server / "sessions.json").read_text(encoding="utf-8")) == \
        {"schema_version": 1, "sessions": {"s1": {"pid": 1}}}
    assert json.loads((server / "alive_conversations.json").read_text(encoding="utf-8")) == \
        {"schema_version": 1, "conversations": [["a", "b", {}]]}
    assert json.loads((server / "ack_timestamps.json").read_text(encoding="utf-8")) == \
        {"schema_version": 1, "ack_timestamps": {"s1": 42}}
    assert json.loads((server / "machine_identity.json").read_text(encoding="utf-8"))["schema_version"] == 1
    # idempotent: second run is a no-op
    r2 = _run(root)
    assert r2.returncode == 0 and "migrated 0 file(s)" in r2.stdout


def test_newer_schema_refused_file_untouched(tmp_path):
    root = tmp_path / "data"
    server = root / "server"
    server.mkdir(parents=True)
    (server / "sessions.json").write_text(
        '{"schema_version": 2, "sessions": {"s1": {}}}', encoding="utf-8")
    r = _run(root)
    assert r.returncode == 1
    assert "REFUSED" in r.stdout
    assert json.loads((server / "sessions.json").read_text(encoding="utf-8"))["schema_version"] == 2
    # dry-run also refuses (exit 1, nothing written)
    r2 = _run(root, "--dry-run")
    assert r2.returncode == 1
    assert json.loads((server / "sessions.json").read_text(encoding="utf-8"))["schema_version"] == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_migrate_data.py -v`
Expected: FAIL — `FileNotFoundError` (tool does not exist)

- [ ] **Step 3: Implement `tools/migrate_data.py`**

Create `tools/migrate_data.py`:

```python
"""cc-communicate data-root migration/validation tool (HP-11, D2).

Validates a data root's layout + schema_version conventions and migrates the
v1 registries to the versioned convention (wrap the flat registries, stamp
machine_identity). REFUSES (exit 1, file untouched) when any state file's
schema_version is NEWER than the supported one - an older plugin must never
silently misread a newer data root.

Usage:
  py -3 tools/migrate_data.py --data-root <dir> [--dry-run]

Run with the kernel stopped. Idempotent: a second run is a no-op.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "v2_win", "cc-communicate", "server")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", required=True,
                    help="data root to validate/migrate")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing")
    args = ap.parse_args(argv)

    # The server modules bind their paths at import from
    # CC_COMMUNICATE_DATA_DIR - set it BEFORE importing them.
    os.environ["CC_COMMUNICATE_DATA_DIR"] = os.path.abspath(args.data_root)
    if SERVER not in sys.path:
        sys.path.insert(0, SERVER)
    import schema

    errors, warnings = schema.validate_layout(args.data_root)
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")

    migrated = 0
    for rel, key in schema.WRAP_TARGETS:
        path = os.path.join(args.data_root, rel)
        if not os.path.isfile(path):
            continue
        if args.dry_run:
            if schema.needs_wrap(path, key):
                print(f"WOULD {rel} -> wrap schema_version 1")
                migrated += 1
        elif schema.wrap_v1(path, key):
            print(f"WRAP  {rel} -> schema_version 1")
            migrated += 1
    for rel in schema.STAMP_TARGETS:
        path = os.path.join(args.data_root, rel)
        if not os.path.isfile(path):
            continue
        if args.dry_run:
            if schema.needs_stamp(path):
                print(f"WOULD {rel} -> stamp schema_version 1")
                migrated += 1
        elif schema.stamp_v1(path):
            print(f"STAMP {rel} -> schema_version 1")
            migrated += 1
    if args.dry_run:
        print(f"dry-run: {migrated} file(s) would be migrated; nothing written")
    else:
        print(f"migrated {migrated} file(s)")
    print("REFUSED (newer schema files present)" if errors else "OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `py -3 -m pytest tests/unit/test_migrate_data.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Manual smoke (evidence for the record)**

Run: `py -3 tools/migrate_data.py --help`
Expected: usage text, exit 0

- [ ] **Step 6: Commit**

```bash
git add tools/migrate_data.py tests/unit/test_migrate_data.py
git commit -m "feat(HP-11): migrate_data.py CLI - validate layout + migrate v1 registries, refuse newer schemas (D2)"
```

---

### Task 4: Gate + parity sync + records

**Files:**
- Sync: `v2_wsl/cc-communicate/server/schema.py`
- Modify: `tested&2betest.md` (T44 record)

**Interfaces:**
- Consumes: all Tasks 1-3 outputs.

- [ ] **Step 1: Sync v2_wsl + full suite + parity**

```bash
cp v2_win/cc-communicate/server/{kernel,kernel_api,operation_journal,schema}.py v2_wsl/cc-communicate/server/
py -3 -m pytest -q
py -3 tools/check_parity.py
```

Expected: full suite PASS; `PARITY OK (31 files compared, allowlist=['.mcp.json'])`

- [ ] **Step 2: Run the full auto gate**

Run: `py -3 tools/run_regression.py --tier auto`
Expected: `GATE PASS`

- [ ] **Step 3: Record T44 in `tested&2betest.md` §1**

Append:

```markdown
### T44 — HP-11 unit acceptance: schema validation + migration tool (D2, custom data root recoverable)

- **Method**: unit (test_schema.py / test_kernel_schema_guard.py /
  test_migrate_data.py): schema_too_new matrix (int>supported only);
  unwrap dual-read (wrapped v1 + legacy flat/list); stamp_v1/wrap_v1
  migrate + idempotent no-op; validate_layout (dirs advisory, newer file
  REFUSED); kernel loaders skip newer-format state files with a loud log
  (file untouched); dual-read of sessions/alive_convs/ack_timestamps;
  writers emit {schema_version: 1, <key>: <payload>} (incl.
  upload_ack_timestamp); migrate_data.py CLI via subprocess: dry-run writes
  nothing, migration wraps/stamps the 4 registries, second run no-op,
  newer schema -> exit 1 + untouched. Full auto gate
  `py -3 tools/run_regression.py --tier auto` -> GATE PASS.
- **Result**: PASS (unit + auto gate; parity OK). Live gates (full L1-L6,
  incl. the mandated L3/L4) run NEXT at the Wave 3 exit gate per the user's
  locked decision.
- **Confidence**: high for unit semantics + CLI behavior; live gate pending.
```

- [ ] **Step 4: Commit**

```bash
git add v2_wsl/cc-communicate/server tested&2betest.md
git commit -m "docs(W3/HP-11): auto gate GATE PASS + v2_wsl parity sync + T44 record"
```

Wave 3 code is complete. **Next: the full live L1–L6 gate at the Wave 3 exit** (per the user's locked decision — includes the kimi-k3-mandated L3/L4 re-run), then push with user approval.
