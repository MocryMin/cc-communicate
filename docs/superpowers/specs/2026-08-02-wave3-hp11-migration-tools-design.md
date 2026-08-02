# Wave 3 — HP-11(余) Migration Tools + Schema Validation: Design

> **Status**: design approved 2026-08-02 (brainstorming session, sections 1–5,
> user-approved per section; 3 scoping decisions locked via Q&A).
> Next step: writing-plans → implementation plan → **inline execution** (durable
> user mandate: no context-heavy subagents).
> **Context**: `plans/2026-07-24-cc-communicate-hardening-master-plan.md` §3
> Wave 3 = HP-08 (done) → HP-09 (done) → HP-10 (done, `f743306`) → HP-11(余)
> (this design). **D2 is locked**: `CC_COMMUNICATE_DATA_DIR` override exists
> (paths.py + paths.js, Gate 0); the DEFAULT-directory switch stays deferred;
> this wave delivers the migration tools + schema_version convention.
> **Deliverables (master plan §3)**: 自定义 data root 可恢复 (custom data root
> recoverable).

---

## 0. Decisions locked (Q&A, 2026-08-02)

| # | Decision | Value |
|---|---|---|
| D2-a | Tool scope | **Validate + migrate v1**: layout check, per-file schema_version check (newer → refuse loudly, never touch), migrate the v1 registries to the versioned convention. NO content repair (healing already exists at kernel load — message_sequence). **Finding + amendment (2026-08-02)**: three registries (sessions / alive_conversations / ack_timestamps) cannot be stamped IN PLACE — sessions/ack_timestamps are flat dicts (a `schema_version` key would be misread as a session/ack entry) and alive_conversations is a bare list. They **wrap-migrate** to `{schema_version: 1, <key>: <payload>}` (dual-read loaders, HP-01 legacy-.md precedent); `machine_identity.json` stamps in place (extra key ignored) |
| D2-b | Load guard | Kernel loaders **skip + loud log** state files whose `schema_version` is an int > supported (file untouched; the tool/newer plugin handles it). Missing schema_version stays tolerated |
| D2-c | Tool form | `tools/migrate_data.py` CLI (standalone; sets `CC_COMMUNICATE_DATA_DIR` before importing server modules) + shared `server/schema.py` (version constant + check/stamp/layout logic, single source of truth used by the kernel guard too) |

---

## 1. Shared `server/schema.py` (single source of truth)

- `SUPPORTED_SCHEMA = 1`
- `schema_too_new(data, supported=SUPPORTED_SCHEMA) -> bool` — True only when
  `data` is a dict with an **int** `schema_version > supported`. Missing /
  non-int / ≤ supported → False (unstamped v1 files stay loadable).
- `unwrap(data, key)` — the dual-read helper: a wrapped dict
  (`schema_version` + `<key>`) → `data[key]`; anything else → `data`
  unchanged (legacy flat/list shapes pass through). Loaders call it after
  the too-new guard, so their existing bodies work unchanged.
- `stamp_v1(path) -> bool` — add-key stamp for self-describing dict files
  (machine_identity): reads the JSON; if it is a dict without
  `schema_version`, rewrites atomically (fileutil) with `schema_version: 1`;
  already-stamped or non-dict → no-op. Returns whether it stamped.
- `wrap_v1(path, key) -> bool` — wrap-migration for the flat registries
  (sessions / alive_conversations / ack_timestamps): reads the JSON; if it
  is NOT already wrapped (no `schema_version`), rewrites atomically as
  `{"schema_version": 1, <key>: <data>}`; already wrapped → no-op. Returns
  whether it wrapped. (Flat dicts/lists cannot be stamped in place — a
  `schema_version` key would be misread as a session/ack entry, and a list
  cannot carry one.)
- `validate_layout(root) -> (errors, warnings)` — errors = files whose schema
  is newer than supported; warnings = missing runtime dirs (fresh roots are
  valid — the kernel creates them; advisory only).

---

## 2. `tools/migrate_data.py` CLI

- `py -3 tools/migrate_data.py --data-root <dir> [--dry-run]`
- Sets `CC_COMMUNICATE_DATA_DIR` BEFORE importing the server modules (a broken
  root must not require a working kernel; standalone like check_parity.py).
- **Checks** (newer → error, refuse; file untouched): the six
  `server/*.json` state files (sessions, alive_conversations, ack_timestamps,
  message_sequence, cursors, operation_journal) + machine_identity +
  gc_state + `pending_spawn/*.json` markers + `conversations/*/info.json`.
  **Records excluded** (documented: transport items, additive-key compatible
  by design).
- **Migrates** (missing → v1): `sessions.json`/`alive_conversations.json`/
  `ack_timestamps.json` **wrap** to `{schema_version: 1, <key>: <payload>}`;
  `machine_identity.json` **stamps** in place (add-key).
  (`core_status.json` is ephemeral — rewritten every boot — skipped.)
- Output: per-file status + summary; **exit 0** = no errors (warnings ok),
  **exit 1** = any newer-schema file.
- `--dry-run`: reports what would be stamped, writes nothing.
- Documented: run while the kernel is stopped (atomic writes make a
  concurrent stamp-vs-write safe, but a stopped kernel is the clean story).

---

## 3. Kernel load-time schema guard + dual-read loaders

- The six `kernel.py` loaders (`_load_sessions`, `_load_alive_convs`,
  `_load_ack_timestamps`, `_load_message_sequence`, `_load_cursors`,
  `_load_operation_journal`) apply `schema.schema_too_new(data)` after
  `_read_json`: too-new → `log.warning(...schema %s > supported 1 -
  skipping...)` + treat as absent (skip; the file is untouched). Missing /
  valid schema → current behavior unchanged.
- `_load_sessions`/`_load_alive_convs`/`_load_ack_timestamps` additionally
  pass the read through `schema.unwrap(data, <key>)` — the wrapped v1 shape
  and the legacy flat/list shapes both load (HP-01 dual-reader precedent).
- **Writers emit the wrapped shape**: `_save_sessions`,
  `_save_alive_convs`, `_save_ack_timestamps` (kernel.py) and
  `kernel_api.upload_ack_timestamp` write `{schema_version: 1, <key>:
  <payload>}`.
- **`machine_identity` excluded** (documented): its loader already
  regenerates on invalid input, which would DESTROY a too-new file — the
  exclusion prevents that destroy path; a too-new identity is a
  future-plugin scenario the tool catches.
- **Records excluded** (documented): additive-key compatible by design; a
  per-record guard would scan every pipe/log file on every listen.

---

## 4. Edge cases

- `schema_version` non-int / 0 / negative → tolerated (not "newer").
- Tool on a fresh/empty root → no state files → informational + exit 0
  (the kernel creates everything).
- Tool idempotent: a second run finds everything stamped → no-op.
- Dry-run with newer files → exit 1, nothing written.
- The tool is repo-tools level (not parity-synced — like check_parity.py);
  `schema.py` IS server-level → parity-synced to v2_wsl.

---

## 5. Testing

- `tests/unit/test_schema.py` — `schema_too_new` matrix (missing/non-int/1/
  2/int-0/string); `unwrap` (wrapped → payload, legacy flat/list → passthrough,
  wrapped-missing-key → None); `stamp_v1` + `wrap_v1` (migrate, no-op when
  already versioned, atomic); `validate_layout` (missing dirs → warnings,
  present → clean, newer file → error).
- `tests/unit/test_kernel_schema_guard.py` — write `sessions.json` with
  `schema_version: 2` → `_load_sessions` skips + caplog warning; v1 and
  unstamped → loads; **dual-read**: legacy flat `sessions.json` loads,
  wrapped `{schema_version: 1, sessions: ...}` loads, and `_save_sessions`
  emits the wrapped shape (roundtrip via `test_kernel_restart`).
- `tests/unit/test_migrate_data.py` — **subprocess** runs of the real CLI
  against a tmp root (in-process imports would clash with the conftest
  module state): unstamped → stamped + exit 0; dry-run → nothing written;
  newer file → exit 1 + file untouched; second run → no-op.
- Gate: `py -3 tools/run_regression.py --tier auto`; v2_wsl sync (schema.py
  only); T44 record in `tested&2betest.md`.

## 6. Out of scope (deferred, documented)

- DEFAULT data-root switch to platform user-state (D2 — separate release
  after the override is battle-tested live).
- Content repair beyond the existing kernel-load self-healing.
- Per-record schema guards.
- Wave 3 exit live gate (full L1–L6 re-run, incl. the mandated L3/L4) —
  scheduled after this wave's last commit.
