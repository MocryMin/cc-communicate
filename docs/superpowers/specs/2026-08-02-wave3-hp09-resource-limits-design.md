# Wave 3 — HP-09 Resource Limits + Backpressure + artifact_refs: Design

> **Status**: design approved 2026-08-02 (brainstorming session, sections 1–6,
> user-approved per section; 4 scoping decisions locked via Q&A).
> Next step: writing-plans → implementation plan → **inline execution** (durable
> user mandate: no context-heavy subagents).
> **Context**: `plans/2026-07-24-cc-communicate-hardening-master-plan.md` §3
> Wave 3 = HP-08 (done, `900111a`) → HP-09 (this design) → HP-10 (permission
> default flip) → HP-11(余) (migration tools). D5 is locked: 1 MiB inline cap
> (already enforced, `CC_COMMUNICATE_MAX_INLINE_BYTES`); over-limit →
> artifact_refs (path/URI + size + sha256 + media_type).
> **Deliverables (master plan §3)**: 超限结构化报错 (structured over-limit error)
> · 资源上限 + 背压 (limits + backpressure) · artifact_refs.

---

## 0. Decisions locked (Q&A, 2026-08-02)

| # | Decision | Value |
|---|---|---|
| D5-a | Over-limit error code | **RESOURCE_EXHAUSTED** (activates the dormant code) + `data={limit_bytes, actual_bytes}`, retryable False |
| D5-b | Backpressure | Per-pair unacked **count cap** `CC_COMMUNICATE_MAX_BACKLOG` (default 1000) → send blocked with RESOURCE_EXHAUSTED **retryable=True** + `data={unacked, cap}` |
| D5-c | artifact_refs schema | `[{path\|uri (exactly one), size int>=0, sha256 64-hex, media_type str}]`, max **16** (`CC_COMMUNICATE_MAX_ARTIFACT_REFS`); stored in record payload `{text, artifact_refs}`; delivered to BOTH listen_v2 (raw record) and legacy listen (new field) |
| D5-d | Backlog observability | `backlog_stats(sid)` **kernel function only** (not an MCP tool; HP-12 `diagnose_transport` surfaces observability later) |

---

## 1. Over-limit → RESOURCE_EXHAUSTED (activates the dormant code)

**Current**: `validate_message_size` (validation.py:81-90) raises
`InvalidArgumentError` over the `MAX_INLINE_BYTES` cap (1 MiB default,
`CC_COMMUNICATE_MAX_INLINE_BYTES`); `_entry_error` (mcp_server.py:26-37) maps
ALL `InvalidArgumentError` → INVALID_ARGUMENT envelope. `Code.RESOURCE_EXHAUSTED`
exists (result.py) but is unused.

**Change**:

- `validation.py`: new exception
  `ResourceExhaustedError(InvalidArgumentError)` with `code = Code.RESOURCE_EXHAUSTED`
  and a `data` attribute (default None). `validate_message_size` raises it when
  over-limit, with `data={"limit_bytes": cap, "actual_bytes": n}` (recomputed at
  the raise — no string parsing).
- `mcp_server._entry_error`: map by exception code —
  `code = getattr(e, "code", Code.INVALID_ARGUMENT)`,
  `data = getattr(e, "data", None)`. ResourceExhaustedError flows through as:
  `{ok: False, code: "RESOURCE_EXHAUSTED", message: "...over the inline cap; use artifact_refs", data: {limit_bytes, actual_bytes}, retryable: False}`.
- Kernel dispatch re-validation (defense in depth for direct-RPC/script callers)
  surfaces `ResourceExhaustedError` in the RPC error channel; the MCP entry stays
  the authoritative gate (caller-side entry validates before routing, including
  cross-machine — an over-limit remote send never reaches the remote kernel).

**Why retryable=False**: re-sending the same oversized text cannot succeed; the
caller must switch to artifact_refs.

---

## 2. artifact_refs (D5)

### 2.1 Schema and validation

`send_message(fromid, toid, message, correlation_id, kind, artifact_refs=None)`:

```
artifact_refs: [{
  path | uri: str (EXACTLY ONE, non-empty; no absolute-path requirement —
              refs are peer-perspective for cross-machine artifacts),
  size:       int >= 0,
  sha256:     ^[0-9a-f]{64}$,
  media_type: non-empty str
}]
```

- New `validation.validate_artifact_refs(value) -> list`:
  - None → [] (absent)
  - must be a list, each entry a dict; per-entry checks as above; violations
    raise `InvalidArgumentError` (INVALID_ARGUMENT — schema violation ≠
    resource pressure)
  - length ≤ `MAX_ARTIFACT_REFS` (env `CC_COMMUNICATE_MAX_ARTIFACT_REFS`,
    default 16)
- Runs at BOTH boundaries: MCP entry (`mcp_server.send_message` check list) and
  kernel dispatch (`_ARG_VALIDATORS["send_message"]`).

### 2.2 Record + delivery

- `message_record.new_record(...)` gains `artifact_refs=None`; payload becomes
  `{"text": text, "artifact_refs": [...]}` — the `artifact_refs` key is present
  ONLY when refs are passed (zero change for existing records; no schema_version
  bump — additive key).
- `kernel_api.send_message` passes the refs through.
- Delivery:
  - `listen_v2` returns raw records → refs flow automatically.
  - Legacy `listen`/`collect_messages` (`_read_pipe_message` in kernel_api.py):
    message dict gains `"artifact_refs"` when the record payload has them
    (deprecation-window parity — refs must not be invisible to legacy listeners).

### 2.3 Hard rule

Over-limit text is STILL rejected even when refs accompany it — `validate_message_size`
runs unconditionally. Refs are the sanctioned alternative, not a bypass.

---

## 3. Backpressure cap (per-pair unacked)

- `kernel_api.MAX_BACKLOG = int(os.environ.get("CC_COMMUNICATE_MAX_BACKLOG", "1000"))`
  (module constant; tests set `server.kernel_api.MAX_BACKLOG` directly).
- `kernel_api.send_message`: after the registration + dedup checks, BEFORE
  publish — count pipe files for the pair (records + legacy .md; everything in
  pipe/ is unacked by definition). If `count >= MAX_BACKLOG`:
  `{"sent": False, "reason": "backlog full", "backlog": {"unacked": count, "cap": MAX_BACKLOG}}`.
- `user_functions.send_message` maps by **key presence** (`r.get("backlog")` is
  not None — machine-readable, no string parsing) → `err(RESOURCE_EXHAUSTED,
  retryable=True, data={"unacked", "cap"})`. Retryable because draining (peer
  acks → archive → pipe shrinks) + retry resolves it. All other `sent: False`
  reasons keep today's mapping (NOT_FOUND).
- **Store-side enforcement**: cross-machine sends execute in the store's kernel
  (host for host↔WSL), so the cap applies where the pipe physically lives.
  Single-threaded kernel → no check/publish race; the cap is exact (each send
  re-scans after the previous publish; pipe maxes at exactly the cap).
- **Config surface**: `CC_COMMUNICATE_MAX_BACKLOG` (count, default 1000). No
  byte cap this wave (user decision).

---

## 4. `backlog_stats` kernel function (observability)

- `kernel_api.backlog_stats(sid) -> {partner_sid: {"unacked": n, "bytes": m}}`:
  scan conversation dirs containing sid; count pipe files addressed TO sid
  (`parse_any_pipe_filename` `to_id == sid`, both formats); bytes = sum of
  `os.path.getsize` of those files. Read-only.
- Dispatch route + `_ARG_VALIDATORS["backlog_stats"] = {"session_id":
  validate_session_id}`; NOT journaled (read-only); **NOT an MCP tool** (HP-12
  surfaces observability as a tool; this wave it is reachable via script-import
  for live gates/tests).

---

## 5. Error handling & edge cases (summary)

- Refs never bypass the text cap (§2.3).
- Backlog counts both formats; cap enforced per publish; exact under the
  single-threaded kernel.
- Remote sends: caller-side entry validates (incl. artifact_refs schema); the
  store-side kernel re-validates at dispatch AND enforces the backlog cap where
  the pipe lives. A stale peer's over-limit remote call → error channel →
  PEER_UNREACHABLE mapping (unreachable-in-practice; the authoritative gate
  already fired caller-side).
- Legacy records carry no refs (old format) — readers return refs only when the
  record has them.
- Cursor/ordering/ACK semantics untouched — refs ride in the record payload;
  archive rules unchanged.
- Malformed refs → INVALID_ARGUMENT (schema violation ≠ resource pressure).

---

## 6. Testing & docs

Unit (new files):
- `tests/unit/test_artifact_refs.py` — validation matrix (path+uri both,
  neither, bad sha256 case/length, negative/non-int size, non-dict entry,
  >16 refs → INVALID_ARGUMENT); send with refs → record payload carries
  `artifact_refs`; listen_v2 delivers refs in the raw record; legacy listen
  delivers the `artifact_refs` field; over-limit text WITH refs still
  RESOURCE_EXHAUSTED.
- `tests/unit/test_resource_limits.py` — over-limit at MCP entry →
  RESOURCE_EXHAUSTED envelope with `{limit_bytes, actual_bytes}`, retryable
  False; `ResourceExhaustedError.code == RESOURCE_EXHAUSTED`; backlog:
  `MAX_BACKLOG=0` → send rejected `{"sent": False, "backlog": {...}}`;
  user_functions mapping → RESOURCE_EXHAUSTED retryable True + data; drain →
  send succeeds (backpressure release); `backlog_stats` per-partner counts +
  bytes; dispatch routes `backlog_stats`.
- Full suite + parity stay green (payload key additive — no record-format
  breakage).

Docs:
- SKILL.md: `send_message` doc gains `artifact_refs`; codes table
  `RESOURCE_EXHAUSTED` updated from "reserved for HP-09" to "inline cap
  exceeded (retryable False) / backlog full (retryable True)".
- Exit gate: `py -3 tools/run_regression.py --tier auto` (Wave 3 exit adds the
  full live L1-L6 re-run per the user's locked decision); v2_wsl parity sync;
  T42+ records in `tested&2betest.md`.

## 7. Out of scope (deferred, documented)

- Byte-based backlog cap (count-only per D5-b decision).
- `diagnose_transport` tool (HP-12) — `backlog_stats` stays kernel-function-only
  until then.
- Artifact lifecycle (retention/GC of referenced files) — belongs to the upper
  layer's Evidence Store (master plan §4.3 item 4).
- HP-10/HP-11(余) — separate designs, same wave.
