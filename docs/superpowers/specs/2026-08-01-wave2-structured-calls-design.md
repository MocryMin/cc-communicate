# Wave 2 — Parallel Workers + Structured Calls: Design

> **Status**: design approved 2026-08-01 (brainstorming session, sections 1–4).
> Next step: writing-plans → implementation plan → execute (SDD subagents).
> **Context**: `plans/2026-07-24-cc-communicate-hardening-master-plan.md` §3
> Wave 2 = HP-07 (structured Result/Error envelope) → HP-04 (spawn_token +
> WorkerHandle) → HP-05 (connection_id + correlated handshake). Decisions D7
> (code enum, landed in Wave 1), D8 (env injection + probe-first + plan B),
> D9 (single active connection + info.json) are locked. Wave 1 exit gate is
> GREEN at `1af8799`; this design defines what Wave 2 builds.
> **Deliverables (master plan §3)**: 同 cwd 并发 spawn 不串 session · WorkerHandle
> 结构化返回 · connect reply 经 correlation_id 匹配 · 内部不再解析字符串控流程.

---

## 1. Architecture: the envelope boundary

The envelope is the **API contract**, not the transport.

| Layer | Envelope? | Why |
|---|---|---|
| `kernel.py` / `kernel_api.py` | **No** — raw *structured* returns | Kernel is transport-internal with a single consumer. Its string returns become structured dicts (e.g. `send_message` → `{"sent": true, "message_id", "ts"}`) so nothing above parses strings |
| `user_functions.py` | **Yes** — builds envelopes via `result.ok()/err()`, every tool | This is where "内部不再解析字符串控流程" happens; all `"failed" in str(...)`, `"message_sent at <ts>"`, `"connect succeed; reply: "` parsing is deleted |
| `mcp_server.py` | **Yes** — passthrough + entry-validation errors as `err(INVALID_ARGUMENT)` | Thin shell; tools return what user_functions built |
| `rpc_client` | Unchanged | Moves dict args; results stay raw |

Boundary invariants (test-enforced where cheap):
- `user_functions.py` contains **no string-prefix parsing for control flow** (no `"failed" in`, no `str(x).startswith(...)` branch on message text). A unit test greps the removed patterns.
- Kernel functions keep raw return values; only `user_functions` wraps.

## 2. HP-07 — Result/Error envelope

### 2.1 Shape

Extends the existing `server/result.py` (Code enum + `ok()`/`err()` from Wave 1 / D7):

```
{ok: bool, code: str|null, message: str|null, data: any, retryable: bool}
```

- `ok(data)` → `{ok: True, code: None, message: None, data: data, retryable: False}`
- `err(code, message, data=None, retryable=False)`

### 2.2 Codes and retryable

| Code | Meaning | retryable |
|---|---|---|
| `INVALID_ARGUMENT` | entry validation failed (HP-06) | False |
| `NOT_FOUND` | session / conversation / token unknown | False |
| `PEER_UNREACHABLE` | target machine/kernel unreachable | True |
| `TIMEOUT` | reply not received within hold_time | True |
| `CONFLICT` | single-active-connection violated (D9) | False |
| `RESOURCE_EXHAUSTED` | limits (reserved for HP-09) | False |
| `NOT_ALIVE` | **added this wave**: session not alive / revive failed (connect/evoke paths) | True |
| `INTERNAL` | unexpected failure | False |

### 2.3 Tool-by-tool migration

Every MCP tool returns the envelope; `data` carries today's payload:

| Tool | Today's return | `data` after |
|---|---|---|
| `my_session_id` | `str` sid / `"failed, ..."` | sid |
| `query_session` | dict / None | session_inf or null |
| `check_alive` | 0/1 | int |
| `query_conversations` | dict | dict |
| `send_message` | `"message_sent at <ts>"` | `{message_id, ts}` |
| `register_conversation` / `unregister_conversation` / `withdraw` | kernel string | kernel structured result |
| `evoke` | `"evoke spawned (resumed)"` / `"failed, ..."` | `{evoked: true, session_id}` |
| `listen` / `listen_v2` | `{messages, watermark}` / `{messages, next_cursors}` | same dict |
| `connect` | `"connect succeed; reply: ..."` / `"failed, ..."` | `{connection_id, reply, established_at_ms}` |
| `close_connection` | `{closed: true}` | `{closed: true}` |
| `query_my_ACK_timestamp` | int | int |
| `query_my_cursors` | dict | dict |
| `create_collaborator` | **legacy wrapper** | maps envelope back to today's exact strings |
| `query_machines` / `help_connect_machines` | dict / str | dict / str |
| `spawn_collaborator` (**new**) | — | WorkerHandle (see §3) |
| `claim_pending_spawn` (**new**, plan B) | — | `{claimed: true, session_id}` |

`create_collaborator` as a legacy wrapper: calls the new spawn flow + connect
internally, returns the exact legacy strings (`"connect succeed; reply: ..."`,
`"failed, ..."`) so existing consumers and already-spawned workers keep working.
`find_new_session(cwd, since_ts)` stays for it.

### 2.4 Kernel structured returns

The kernel's string returns are the *only* internal string parsing source. This
wave converts them to structured dicts (internal contract, no external consumer):
- `send_message` → `{"sent": True, "message_id": ..., "ts": ...}`
- `withdraw` / `register_conversation` / `unregister_conversation` → `{"ok": True, ...}` style dicts
- `evoke` → `{"evoked": True, "session_id": ...}` (same shape as the tool-level `data`)
- No envelope in kernel — plain dicts.

## 3. HP-04 — spawn_token + WorkerHandle

### 3.1 New tool

```
spawn_collaborator(caller_sid: str, cwd: str, spawn_token: str = None,
                   permission_mode: str = "bypass", machine: dict = None,
                   hold_time: int = 300) -> envelope
```

- `data` = **WorkerHandle** `{session_id, machine_id, cwd, spawn_token, connection_status}`
- `connection_status` ∈ `"spawned"` → `"registered"` — the tool waits up to 30s
  for registration but does **not** auto-connect (master plan §4.3: caller
  decides when to `connect`).
- `spawn_token`: caller-supplied (uuid4 hex); if omitted the server generates
  one and returns it in the handle. **Same-token retry returns the original
  handle** — no second spawn, no cross-session.
- `permission_mode` accepted now with default `"bypass"` (today's behavior);
  Wave 3 HP-10 flips the default to `"standard"` per D4. Signature never changes.
- `machine` keeps the cross-realm spawn path (spawn on peer via remote RPC, as today).
- Errors: `INVALID_ARGUMENT` (validation), `TIMEOUT`/`NOT_FOUND` (no registration
  within 30s), `INTERNAL`.

### 3.2 Token chain (plan A — env injection, D8)

One token→sid association point in the kernel:

1. `spawn.py` injects `CC_COMMUNICATE_SPAWN_TOKEN` into the child env:
   - Windows: `_detached_popen(env={**os.environ, "CC_COMMUNICATE_SPAWN_TOKEN": token})`
   - WSL: `env VAR=x <claude>` inside the tmux command (tmux `set-environment` if needed)
2. `registrar.js` start event adds `spawn_token: process.env.CC_COMMUNICATE_SPAWN_TOKEN || null`.
3. Kernel `_handle_start` records `spawn_token → session_id` in the in-memory
   status table; replay from the event files rebuilds it (no new persistence).
4. New kernel API `find_session_by_token(token)`; `spawn_collaborator` polls it
   (replacing `find_new_session(cwd, since_ts)` for the new API) — the same-cwd
   race disappears by construction.

### 3.3 Plan B — pending_spawn claim (if the probe fails)

Same token→sid map, different delivery:

1. Spawn side writes `data/pending_spawn/<token>.json` before spawning.
2. The worker's prompt embeds its token; new tool `claim_pending_spawn(spawn_token)`
   (worker calls on first tool use) → kernel associates sid→token.
3. `find_session_by_token` resolves either way.

Both paths land in the same map, so the rest of the code is identical. The D8
live probe (§5.2) during HP-04 decides which delivery is live.

### 3.4 Legacy

`create_collaborator` = spawn + connect, envelope mapped back to today's strings.
`find_new_session` stays for it only.

## 4. HP-05 — connection_id + correlated handshake

### 4.1 New connect signature

```
connect(caller_sid: str, target_sid: str, connection_id: str = None,
        hold_time: int = 300) -> envelope
```

- `connection_id`: caller-supplied (uuid4 hex); omitted → server generates.
  **Retry with the same connection_id is idempotent** (returns current state
  instead of re-handshaking) — same pattern as spawn_token and Wave 1's message_id.

### 4.2 Handshake

1. Hello is a structured record: `new_record(..., kind="hello", correlation_id=connection_id)`
   — the dormant `kind`/`correlation_id` fields get their first real use.
2. `send_message` gains optional `correlation_id` (+`kind`) params; the peer
   replies `send_message(peer_id, my_id, "<text>", correlation_id=<connection_id>)`.
3. `connect`'s reply poll matches on `correlation_id == connection_id` (+ from/to),
   replacing the ts-based heuristic. Stale/foreign messages can't be misread as
   the reply. Deliverable met: **connect reply 经 correlation_id 匹配**.

### 4.3 info.json (per conv dir, already reserved in `conversations.py` layout)

```
{schema_version, connection_id, status, established_at_ms, sid_a, sid_b}
```

- Written atomically by new kernel call `activate_connection(sid_a, sid_b, connection_id)`
  at successful handshake, invoked by the **initiating side's** `connect` and
  routed exactly like `_register`/`_send` (local when the conv store is local,
  remote RPC to the host kernel when the conv lives on the host).
- `close_connection` marks `status: "closed"` alongside today's unregister.
- **Single active connection (D9)**: on connect, if an active `info.json` exists:
  - same `connection_id` → `ok` with current state (retry);
  - different → `err(CONFLICT, data: {current_connection_id, status})`.

### 4.4 Legacy fallback (one release, telemetry-gated)

A reply *without* correlation_id (old workers running today's prompts) is
accepted only when it is the **single unambiguous candidate** (from/to + newer
than hello_ts) and no correlation_id match exists; the fallback logs a diag
note. New prompts instruct correlation_id replies, so new workers never hit it.

### 4.5 No new reply tools

`accept_connection`/`reply_message` (master plan §4.4's "possible" list) are
dropped — `send_message`'s optional params suffice; the handoff contract text
(§4.3 of the master plan) is updated accordingly when delivering.

## 5. Error handling + testing

### 5.1 Unit tests (tmp-data-dir isolated, per project convention)

- **Envelope**: every tool returns the 5-field shape; error paths carry the
  right code/retryable; `result.ok/err` constructors.
- **Kernel structured returns**: send_message dict shape, withdraw/evoke dicts.
- **No-string-parsing**: user_functions.py contains no `"failed" in` /
  message-text branch for control flow (grep-style test).
- **spawn_token**: start-event replay rebuilds token→sid; same-token retry
  returns same handle; concurrent same-cwd spawns resolve to distinct sessions;
  claim_pending_spawn path; token validation.
- **connection_id**: hello record carries kind/correlation_id; reply matched by
  correlation_id; CONFLICT on second active connection; same-id retry returns
  state; legacy unambiguous fallback works; ambiguous fallback refused.
- **info.json**: written on activate, closed on close, absent → connect proceeds.

### 5.2 Live probes/gates (real CCs; user drives, assistant bails out)

- **D8 probe (during HP-04, before the token chain is written)**: spawn one real
  CC with `CC_COMMUNICATE_SPAWN_TOKEN` set; verify the SessionStart hook event
  carries it (env → hook → SessionStart chain). ~1–2 windows, ~2–3 min.
  Pass → plan A; fail → plan B.
- **End-of-wave gate**: existing `tools/run_regression.py` (T0/T1/T2 + L1–L4)
  re-run, plus:
  - **L5**: `spawn_collaborator` twice in the same cwd concurrently → two
    distinct handles/sessions, no cross-talk.
  - **L6**: connect with explicit `connection_id`; reply matched via
    correlation_id (verified from `data/conversations/` records).

### 5.3 Sync obligations

- v2_win ↔ v2_wsl byte-identical outside `.mcp.json` (parity gate stays green).
- `SKILL.md` rewritten for new shapes (envelope, spawn_collaborator,
  claim_pending_spawn, connect/send_message params).
- Every bug found → T# in `tested&2betest.md` §1.
- Deferred minors fixed where they intersect touched files:
  - `_pid_live`/`_match` dedup (kernel_api.py) — rides along HP-07 result work.
  - known_pids bound-trim TypeError on None start_time (kernel.py) — rides along HP-04 kernel work.
  - marketplace tree sync (T30/T31/T32/T35) stays a release-checklist item (out of scope).

## 6. Out of scope (this wave)

- HP-08 kernel lifecycle/GC, HP-09 limits/artifact_refs, HP-10 permission
  default flip (parameter exists, default unchanged), HP-11 migration, HP-13-A
  single-source — all later waves per the master plan.
- `permission_mode="standard"` behavior itself (only the parameter surface ships).

## 7. Success criteria (restated from the master plan)

1. 同 cwd 并发 spawn 不串 session — L5 live + unit tests.
2. WorkerHandle 结构化返回 — spawn_collaborator envelope + SKILL doc.
3. connect reply 经 correlation_id 匹配 — §4.2 + L6 live + unit tests.
4. 内部不再解析字符串控流程 — grep test + code review.
5. Full regression GREEN (T0/T1/T2 + L1–L6) after the wave, parity OK, both trees synced.
