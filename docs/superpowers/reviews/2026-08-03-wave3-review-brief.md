# Wave 3 Review Brief (for kimi-k3)

> Prepared 2026-08-03 for the external review of Wave 3 of the cc-communicate
> hardening program. Wave 3 = master plan §3 items HP-08 → HP-09 → HP-10 →
> HP-11(余), executed INLINE on main (durable user mandate: no subagents) and
> PUSHED (`6a6ad6b`..`d572fd8`, 34 commits, origin/main).
> Gate state at brief time: **GATE PASS** — T0 syntax (44 .py + 2 .js), T1
> pytest (193 tests), T2 parity (32 files, allowlist `.mcp.json`).

---

## 0. What the reviewer should know

- Project: cc-communicate — a Claude Code p2p plugin (message pipes, connection
  lifecycle, structured envelopes, spawn/revive). Two byte-identical trees:
  `v2_win/` (Windows) and `v2_wsl/` (WSL2), parity-gated. Python server in
  `server/`, JS hooks in `scripts/`.
- The hardening program's decisions D1–D10 are locked in
  `plans/2026-07-24-cc-communicate-hardening-master-plan.md`. Wave 3 implements
  **D10 (HP-08), D5 (HP-09), D4 (HP-10), D2 (HP-11)**.
- Every Wave-3 item followed: brainstorming → spec (user-approved per section)
  → implementation plan (inline execution) → unit tests → auto gate. Specs and
  plans are committed under `docs/superpowers/specs/` and
  `docs/superpowers/plans/` (2026-08-02 dates). Test records T40–T47 in
  `tested&2betest.md` §1.
- Wave 2's external review (kimi-k3, 2026-08-01) PASSED with 4 carry-overs,
  all addressed: T38 code-level fix (HP-08), L3/L4 re-run at Wave 3 exit
  (done — full L1–L6), `_pid_live`/`_match` dedup (ride-along in HP-08),
  close_connection envelope (self-resolved).

## 1. HP-08 — kernel lifecycle decoupling + safe GC (D10)

Spec `docs/superpowers/specs/2026-08-02-wave3-hp08-kernel-lifecycle-design.md`,
plan `docs/superpowers/plans/2026-08-02-wave3-hp08-kernel-lifecycle.md`.

- **Exit predicate** (`server/kernel.py::_should_exit/_exit_decision`):
  registration no longer blocks exit (D10) — exit looks only at
  queue/activity/terminate-flag; a second queue scan guards the exit window
  (R4 optimization; client retry + `_wake_remote` is the correctness
  backstop). Restart+reload is the safety net (all state persists on exit).
- **Safe GC** (`server/cleanup.py`): whitelist = session_ctrl events ≥7d,
  pending_spawn markers >TTL (1h), orphaned queue responses ≥7d; **pipe/
  (unacked) and log/ (conversation records) are structurally untouchable**
  (enumerated roots + path-component guardrail, `violations` reported).
  Min-age is the race guard; deletions best-effort, never raised. Triggered at
  kernel start + daily + on-demand RPC (`run_gc` kernel function, NOT an MCP
  tool). **Live evidence** (T45): the real data root's start sweep deleted 128
  stale session_ctrl events + 3 orphaned responses, `violations: []`.
- **pending_spawn TTL**: expired markers count as absent (un-poisons
  same-token retries) — `CC_COMMUNICATE_PENDING_SPAWN_TTL_SECONDS` (3600).
- **T38 code-level fix**: spawn env sanitization strips
  `CLAUDE_CODE_CHILD_SESSION` (`server/spawn.py::_child_env`) — spawned CCs are
  normal resumable sessions. **Live evidence** (T45/T47): resumed workers have
  saved transcripts.
- Ride-along: `proc.pid_matches` dedup (`check_alive._match` /
  `session_by_pid._pid_live`).

## 2. HP-09 — resource limits + backpressure + artifact_refs (D5)

Spec `docs/superpowers/specs/2026-08-02-wave3-hp09-resource-limits-design.md`,
plan `docs/superpowers/plans/2026-08-02-wave3-hp09-resource-limits.md`.

- **RESOURCE_EXHAUSTED activated**: over-limit inline text (>1 MiB,
  `CC_COMMUNICATE_MAX_INLINE_BYTES`) returns `RESOURCE_EXHAUSTED` +
  `data={limit_bytes, actual_bytes}` (new `ResourceExhaustedError` maps by
  exception code at the MCP entry boundary).
- **artifact_refs (D5)**: `send_message(..., artifact_refs=[{path|uri, size,
  sha256, media_type}])` — exactly one of path/uri, sha256 = 64 lowercase hex,
  max 16 (`CC_COMMUNICATE_MAX_ARTIFACT_REFS`); validated at both trust
  boundaries; rides in the record payload (`{text, artifact_refs}`, additive —
  no schema bump); delivered to listen_v2 (raw record) AND legacy
  listen/collect_messages. Over-limit text is still rejected even WITH refs.
- **Backpressure**: per-pair unacked cap `CC_COMMUNICATE_MAX_BACKLOG` (1000) —
  send blocked → `RESOURCE_EXHAUSTED` retryable=True + `{unacked, cap}`;
  releases after the peer drains. Enforced store-side (cross-machine sends hit
  the store kernel).
- **backlog_stats** kernel function (per-partner unacked+bytes; NOT an MCP
  tool — HP-12 surfaces observability later).

## 3. HP-10 — spawn permission policy + identity boundary + threat model (D4)

Spec `docs/superpowers/specs/2026-08-02-wave3-hp10-permission-policy-design.md`,
plan `docs/superpowers/plans/2026-08-02-wave3-hp10-permission-policy.md`.

- **Default flip**: `spawn_collaborator`/`spawn_cc_new` default
  `permission_mode="standard"` (the param existed but was dropped — now wired
  end-to-end: MCP entry → user_functions → kernel dispatch → `spawn.py` argv).
  `"bypass"` = explicit opt-in for unattended automation.
- **Resume stays bypass** (documented deviation, user-approved): evoke /
  `spawn_cc_resume` default `"bypass"` — resume of an established session is
  not a new trust decision (R8); `evoke` gains an override param.
- **WorkerHandle carries `permission_mode`** (live evidence T45).
- **Legacy marking**: `create_collaborator` keeps bypass EXPLICITLY; return
  strings gain `" ; permission_mode=bypass (legacy)"` (prefixes byte-exact) +
  a kernel-log line for bypass spawns.
- **Threat model README**: `v2_win/cc-communicate/README.md` (plugin root,
  parity-synced): trusted single-user / trusted registered peer realm / NOT
  safe against a malicious local process with data-dir access; permission_mode
  semantics table; config knobs.

## 4. HP-11(余) — migration tools + schema validation (D2)

Spec `docs/superpowers/specs/2026-08-02-wave3-hp11-migration-tools-design.md`
(incl. the wrap-migration amendment), plan
`docs/superpowers/plans/2026-08-02-wave3-hp11-migration-tools.md`.

- **`server/schema.py`** (single source of truth, no `paths` import):
  `SUPPORTED_SCHEMA=1`, `schema_too_new`, `unwrap` (dual-read), `stamp_v1` /
  `wrap_v1`, `validate_layout`.
- **`tools/migrate_data.py` CLI**: `--data-root <dir> [--dry-run]`; validates
  layout + per-file schema_version (newer → REFUSE, exit 1, file untouched);
  migrates the flat registries — sessions / alive_conversations /
  ack_timestamps **wrap** to `{schema_version: 1, <key>: <payload>}` (they
  cannot be stamped in place: a version key would be misread as a
  session/ack entry), machine_identity stamps in place. Idempotent.
- **Kernel loaders**: refuse newer-format state files (skip + loud log, file
  untouched); dual-read the wrapped v1 shapes (HP-01 dual-reader precedent);
  writers emit the wrapped shape (incl. `upload_ack_timestamp`).
- Design amendment (user-approved, committed `33d331c`): the approved
  "stamp the four registries" could not stand — three of the four are flat
  shapes that cannot carry a version key; wrap-migration was the fix.

## 5. Live gates (full L1–L6 re-run, kimi-k3 mandate) — T45/T46/T47

Fresh kernel with the new code (start-GC live evidence); script-import
coordinator + real spawned CC windows; then a real WSL2 session for L3.

| Gate | Result |
|---|---|
| L1 spawn-race | PASS — one worker per token, same-token retry idempotent, check_alive 1 |
| L2 reconnect | PASS-with-finding — dead→evoke→alive 2s, resume in original cwd (T25), transcript saved (T38). **Finding T46**: delivery AFTER resume failed 2/2 — the revived CC's cc-communicate MCP client comes up disconnected; evidence points CC-side (healthy server process, last tool call succeeded, no cc-communicate error; intermittent — Wave-2 L2 passed the same flow). Delivery re-verified via fresh spawns (L4 10/10 acked). |
| L3 cross-realm | PASS (T47) — real WSL CC (peer 4cefe529, Wave-3 code synced, kernel restarted w/ GC evidence); 3/3 acked through the routed host store; B→A replies; cursor re-listen → no re-delivery; per-store cursor independence; zero loss. |
| L4 multi-collab stress | PASS — 10/10 acked, 5/5 reply ids matched, zero loss/dup |
| L5 same-cwd spawns | PASS — distinct sids, no bleed |
| L6 correlated connect | PASS — correlation-matched reply, CONFLICT, same-id reuse, close → info.json closed |

## 6. Known findings / open items (reviewer's attention)

1. **T46** (L2 delivery-after-resume) — attributed to CC v2.1.220's resume↔MCP
   handshake (transcript-restore bake timing out the idle blocking-listen
   server). No cc-communicate code change made; re-test after a CC update.
   Also observed: a stray `❯ bypass` user line in both resumed transcripts —
   neither user nor cc-communicate code typed it (CC permission auto-response
   artifact hypothesized; unverified).
2. **T40** — module `gc.py` collides with the Python stdlib `gc` in this
   sys.path-inserted layout; renamed `cleanup.py` (functions keep the `gc`
   naming). Parity caught the stale-tree residue.
3. Deferred minors (unchanged from the Wave-2 review): known_pids bound-trim
   `sorted(known, key=known.get)` TypeError on None start_time (>8 events);
   `cc-communicate-marketplace/` tree needs T30/T31/T32/T35 + Wave 2 + Wave 3
   at the next release sync (standing release item).
4. Out of scope by design: per-session permission_mode tracking (retry nuance
   documented, not stored); per-record schema guards (records are additive-key
   compatible); byte-based backlog cap; crypto authentication (threat model §4.5).

## 7. Suggested review focus

1. HP-08 exit predicate + GC whitelist boundary (pipe/log untouchability).
2. HP-09 RESOURCE_EXHAUSTED/artifact_refs semantics (refs never bypass the
   cap; backlog retryable mapping).
3. HP-10 default flip correctness (bypass still reachable; resume deviation
   documented; legacy marking).
4. HP-11 wrap-migration dual-read (legacy files keep loading; writers emit
   wrapped; newer schemas refused untouched).
5. The live-gate records T45/T46/T47 vs the checklists in
   `tools/run_regression.py`.
