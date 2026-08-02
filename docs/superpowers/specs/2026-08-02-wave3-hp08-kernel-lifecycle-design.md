# Wave 3 — HP-08 Kernel Lifecycle + Safe GC: Design

> **Status**: design approved 2026-08-02 (brainstorming session, sections 1–6,
> user-approved per section; 4 scoping decisions locked via Q&A).
> Next step: writing-plans → implementation plan → **inline execution** (durable
> user mandate: no context-heavy subagents).
> **Context**: `plans/2026-07-24-cc-communicate-hardening-master-plan.md` §3
> Wave 3 = HP-08 (kernel lifecycle + safe GC, **D10**) → HP-09 (resource limits +
> artifact_refs, D5) → HP-10 (spawn permission default flip, D4) → HP-11(余)
> (migration tools). Wave 2 closed at `a8ce5a6` (external review kimi-k3 PASSED;
> review carry-overs folded in below). This design defines what HP-08 builds.
> **Deliverables (master plan §3)**: registered-but-idle 可退出 · GC 不碰 unacked.

---

## 0. Review carry-overs folded into this wave (kimi-k3, 2026-08-01)

1. **T38 env issue → CODE-LEVEL FIX** (this wave): kernel spawns inherit
   `CLAUDE_CODE_CHILD_SESSION` → spawned CCs have transcript saving off →
   non-resumable → `evoke` path dead. Fixed in `spawn.py` (§4.1) — the
   clean-env kernel restart workaround (T38) becomes unnecessary for new spawns.
2. **L3 + L4 MUST be re-run at the Wave 3 exit gate** — extended by user
   decision to **full L1–L6 re-run** (HP-08 changes kernel lifecycle itself;
   L1/L2 exercise spawn/evoke through kernel restarts).
3. **`_pid_live`/`_match` duplication** — ride-along dedup (§4.2).
4. close_connection envelope — reviewer self-resolved, no action.

**Execution constraint**: this wave executes INLINE (design spec → implementation
plan → inline execution, `py -3 tools/run_regression.py` as exit gate).

---

## 1. Architecture: lifecycle decoupling (D10)

The kernel is already lazy-started (`check_core.ensure_core`) and state-recovering
(`_load_sessions`/`_load_alive_convs`/`_load_ack_timestamps`/`_load_message_sequence`/
`_load_cursors`/`_load_operation_journal` + session_ctrl replay). Registration is
**persistent data** (`alive_conversations.json`), not a process lease.

### 1.1 Exit predicate (`kernel.py::_should_exit`)

```
CURRENT (kernel.py:482):
    _exit_requested / TERMINATE_FLAG  -> True
    alive_conversations non-empty     -> False   <-- registration blocks exit FOREVER
    idle < _IDLE_TIMEOUT              -> False
    queue pending                     -> False
    else                              -> True

NEW:
    _exit_requested / TERMINATE_FLAG  -> True
    queue pending                     -> False   (queue: in-flight request)
    idle < _IDLE_TIMEOUT              -> False   (activity: recency)
    else                              -> True
```

- **lease/mutation**: no explicit lease exists. Spawn freshness is covered by
  `_last_activity` (spawn_cc_new runs through the queue → activity). In-flight
  mutations are covered by the queue check (drain_queue is synchronous: queue
  empty ⇒ nothing in flight). Registration is NOT a factor.
- **Second queue scan** (R4 optimization): before `break` in the main loop,
  re-check `_queue_has_pending()` — if a request landed in the exit window,
  `continue` instead of exiting. Correctness does not depend on it: missed
  requests are covered by `rpc_client.call` retry (restarts the kernel, same
  `operation_id` → journal replay) and `call_remote` + `_wake_remote` backstop —
  both already exist.
- **Exit path already persists** state (kernel.py:568-574: sessions,
  alive_convs, ack_timestamps, message_sequence, cursors). A restarted kernel
  reloads registered conversations — a conversation that is registered but idle
  survives kernel exit/restart with zero data loss.
- **Idle timeout unchanged** (600s default, `CC_MONITOR_IDLE_TIMEOUT`): an
  actively-listening conversation keeps the kernel alive (each listen_scan is
  queue activity → `_last_activity` refresh).

### 1.2 Why this is safe

| Risk | Mitigation |
|---|---|
| Exit while a remote request is in flight | Client retry + `_wake_remote` (exists); second queue scan (added, optimization) |
| State loss on exit | finally-block persists all six state files |
| Restart cost | lazy-start, ~instant; handshake timeout 15s is worst-case polling bound |
| Registered convs forgotten | `_load_alive_convs` rebuilds from `alive_conversations.json` |

---

## 2. Safe GC: new module `server/cleanup.py`

### 2.1 Whitelist (the ONLY things GC may touch — hard-excluded: `pipe/`, `log/`)

| Kind | Root | Age threshold | Why safe |
|---|---|---|---|
| `session_ctrl` | `data/session_ctrl/*.json` | ≥ 7 days | Replay of a >7d-old start/end is a no-op (session long dead; events sort by ts and replay deterministically; sessions.json already holds `ended_at`); 7d is far above the spawn/claim/retry window that token replay needs |
| `pending_spawn` | `data/pending_spawn/*.json` | > TTL (default 1h, §3) | Expired marker is definitionally poisoned; physical deletion is the un-poisoning |
| `responses` | `data/queue/responses/*.json` | ≥ 7 days | Request ids are uuid4, never re-polled (each retry generates a fresh rid); a 7d-old response is dead residue |

- `collect_candidates() -> {kind: [abs paths]}` — pure function; scans exactly
  the three whitelisted roots, filters by file mtime age. Testable without a
  kernel process.
- `run_gc(dry_run: bool) -> {"deleted": int, "dry_run": bool, "details": [...]}`
  — deletes candidates (dry-run: logs, deletes nothing), returns structured
  summary. Plain dict (kernel-layer conventions; no envelope inside the kernel).
- **Hard invariant**: every candidate path must resolve strictly under one of
  the three whitelisted roots; any path whose component chain contains `pipe`
  or `log` is skipped and recorded as a violation (guardrail — should be
  impossible; makes "never touch" structural, not convention).
- **Best-effort deletion**: per-file `try/except OSError`, failures collected
  into `details`; GC never raises into the kernel loop.
- **Minimum age is the race guard**: out-of-process writers (registrar.js hook
  writes session_ctrl) are protected by the age filter — nothing younger than
  the threshold is ever touched. GC runs in the kernel's single thread, so no
  intra-kernel races.

### 2.2 Trigger

- `run_gc` becomes a **kernel function** (dispatched in kernel.py; NOT
  journaled — it is idempotent cleanup, not an HP-03 mutation):
  - at kernel start, before READY (no live traffic yet)
  - once per 24h of uptime, timestamp-tracked (`gc_state.json`:
    `{"schema_version": 1, "last_run_at": epoch}`); due-ness checked on a slow
    cadence in the loop
  - manual: RPC `run_gc(dry_run)` — tests / live gates (script-import via
    rpc_client)
- **API surface**: kernel function ONLY — **not** an MCP tool. The upper layer
  never needs it; no SKILL.md tool docs.

---

## 3. pending_spawn TTL (fixes Wave-2 deferred minor)

**Problem**: `pending_spawn/<token>.json` has no TTL. Kernel crash in the
write-window (marker written, child never spawned, no start event) poisons the
token forever — `has_pending_spawn` returns True, same-token retries never
re-spawn.

**Change** — marker age becomes part of the truth:

- TTL: env `CC_COMMUNICATE_PENDING_SPAWN_TTL_SECONDS`, default **3600** (1h).
  Spawn→claim real window is seconds-to-minutes; 1h is a 2-3 orders-of-magnitude
  safety margin. Freshness read from the marker's existing
  `created_at_ms` field — no schema change.
- `has_pending_spawn(token)`: marker older than TTL counts as **absent**.
- `claim_pending_spawn(token, sid)`: expired marker →
  `{"claimed": False, "reason": "no pending spawn for token"}` (same as missing).
- `spawn_cc_new` retry path (spawn_collaborator same-token): expired marker →
  treated as not-pending → fresh spawn (new marker, fresh `created_at_ms`).
- GC (§2) uses the same TTL as the pending_spawn age threshold and physically
  deletes the expired file.

**Documented behavior note**: a worker starting >1h after spawn that relies on
plan-B claim gets "no pending spawn" — but plan-A binding (env token → start
event) is unaffected, and the worker's `my_session_id` self-discovery still
works. A same-token coordinator retry after expiry spawns a second worker — the
accepted cost of un-poisoning tokens (same as any TTL).

---

## 4. Ride-alongs (review carry-overs)

### 4.1 T38 code-level fix: spawn env sanitization (`spawn.py`)

When building the child env (`_detached_popen` / `_tmux_spawn`):

```python
child_env = dict(os.environ)
child_env.pop("CLAUDE_CODE_CHILD_SESSION", None)   # CC-internal; breaks resume
child_env["CC_COMMUNICATE_SPAWN_TOKEN"] = token
```

- Whitelist-extensible: only evidence-based additions later (other
  `CLAUDE_CODE_*` internals stay until a concrete failure proves they matter).
- Effect: CC-spawned workers are normal resumable sessions (transcript on) even
  when the kernel itself was spawned by a CC. The T38 clean-env-restart
  workaround becomes unnecessary for new spawns.

### 4.2 `_pid_live`/`_match` dedup (`kernel_api.py`)

`check_alive._match` (lines 57-64) and `session_by_pid._pid_live` (lines
547-556) are byte-identical liveness logic (pid exists + start-time within 1s).
Factor into one shared helper `proc.pid_matches(pid, recorded)` in `proc.py`
(next to `proc_start_time`); both call sites use it. Pure refactor, behavior
unchanged, tests confirm.

---

## 5. Error handling & edge cases (summary)

- GC vs concurrent writers: age filter is the race guard (§2.1).
- Deletion failures: best-effort, collected, never raised (§2.1).
- Exit during GC: GC is a plain function call; `_should_exit` re-evaluated next
  iteration; no blocking state.
- Exit-vs-request race: second queue scan + existing retry/wake backstop (§1.1).
- Malformed whitelisted files (unreadable/partial): skipped by age/parse checks,
  never a crash.

---

## 6. Testing

Unit (new/extended files):
- `tests/unit/test_gc.py` — whitelist boundary (pipe/log never enumerated),
  age thresholds (frozen time), dry_run deletes nothing, expired-marker removal,
  result shape, violation guardrail
- `tests/unit/test_kernel_exit.py` — **registered-but-idle now exits** after
  idle timeout (the behavior change); queue-pending blocks; fresh activity
  blocks; request landing in the exit window → second scan continues the loop
- `tests/unit/test_spawn_token.py` (extend) — TTL: expired marker →
  `has_pending_spawn` False / claim → no-pending / same-token retry re-spawns
- `tests/unit/test_spawn_env.py` (extend) — child env excludes
  `CLAUDE_CODE_CHILD_SESSION`, includes `CC_COMMUNICATE_SPAWN_TOKEN`
- `tests/unit/test_kernel_restart.py` (extend) — **acceptance**: registered-but-
  idle kernel exits; `ensure_core` restarts; alive_convs reloaded;
  `send_message` still works
- `tests/unit/test_check_alive_fallback.py` (extend) — `proc.pid_matches`
  shared helper, behavior unchanged

Exit gate: `py -3 tools/run_regression.py` (auto tiers + parity) → **full live
L1–L6 re-run** (user decision; L3/L4 mandated by kimi-k3) → records T40+ in
`tested&2betest.md` → v2_wsl parity sync → push with user approval.

---

## 7. Out of scope (deferred, documented)

- sessions.json dead-session pruning (small growth; `evoke` needs it)
- empty conversation pair-dir cleanup (touches the conversations tree)
- `diagnose_transport` (HP-12, later wave)
- HP-09/HP-10/HP-11(余) — separate designs, same wave
