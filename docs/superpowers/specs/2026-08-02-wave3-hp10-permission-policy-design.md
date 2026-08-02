# Wave 3 — HP-10 Spawn Permission Policy + Identity Boundary + Threat Model: Design

> **Status**: design approved 2026-08-02 (brainstorming session, sections 1–6,
> user-approved per section; 4 scoping decisions locked via Q&A).
> Next step: writing-plans → implementation plan → **inline execution** (durable
> user mandate: no context-heavy subagents).
> **Context**: `plans/2026-07-24-cc-communicate-hardening-master-plan.md` §3
> Wave 3 = HP-08 (done) → HP-09 (done, `cf2d649`) → HP-10 (this design) →
> HP-11(余). **D4 is locked**: new spawn APIs default `permission_mode="standard"`;
> unattended automation explicitly opts into `"bypass"`; legacy
> `create_collaborator` keeps bypass but marks it in return + log; threat model
> into README.
> **Deliverables (master plan §3)**: spawn 权限默认翻转（D4）· threat model 入
> README · 身份边界如实声明（不伪装成已认证）.

---

## 0. Decisions locked (Q&A, 2026-08-02)

| # | Decision | Value |
|---|---|---|
| D4-a | New-spawn defaults | `spawn_collaborator` + `kernel_api.spawn_cc_new` default **"standard"** (flip from today's "bypass") |
| D4-b | Resume default | `evoke`/`spawn_cc_resume` default **"bypass"** — documented deviation: resume of an already-established session is not a new trust decision (its workspace trust was settled at original spawn); the mode isn't tracked per-session; forcing standard would break automated reconnect (R8) since the coordinator calls evoke and cannot click a trust dialog. The `permission_mode` param exists for explicit override |
| D4-c | Legacy marking | `create_collaborator` keeps bypass EXPLICITLY; return strings gain suffix `" ; permission_mode=bypass (legacy)"` (prefixes `"connect succeed"`/`"connect failed"` stay byte-exact) + durable kernel log line for bypass spawns |
| D4-d | README home | New `v2_win/cc-communicate/README.md` (plugin root, ships with the artifact; parity copy to v2_wsl; marketplace copy at next release sync) |
| D4-e | WorkerHandle | gains `"permission_mode"` field — the coordinator sees its worker's autonomy level |

---

## 1. permission_mode plumbing — default flip + full wiring

**Current**: the MCP tool `spawn_collaborator` accepts `permission_mode` (default
`"bypass"`) but DROPS it before `user_functions`; `spawn.py` hardcodes
`--dangerously-skip-permissions` in both `spawn_cc_new` and `spawn_cc_resume`.

**Change**:

- `spawn.py`: `_permission_argv(mode) -> list` — `"bypass"` →
  `["--dangerously-skip-permissions"]`, `"standard"` → `[]`.
  `spawn_cc_new(cwd, prompt, spawn_token=None, permission_mode="standard")` and
  `spawn_cc_resume(session_id, prompt, cwd=None, permission_mode="bypass")`
  splice the result into the claude argv (Windows `cmd /c start` and WSL
  `_tmux_spawn` alike).
- `validation.py`: `validate_permission_mode(value)` — `"standard"` or
  `"bypass"` only (INVALID_ARGUMENT otherwise); enforced at BOTH boundaries
  (MCP entry check list + kernel `_ARG_VALIDATORS` for
  `spawn_cc_new`/`spawn_cc_resume`).
- `kernel_api.spawn_cc_new(cwd, prompt, spawn_token=None,
  permission_mode="standard")` → passes to `spawn.spawn_cc_new`; dispatch
  routes `args.get("permission_mode", "standard")`.
  `kernel_api.spawn_cc_resume(session_id, prompt, cwd=None,
  permission_mode="bypass")` likewise.
- `user_functions.spawn_collaborator` gains `permission_mode="standard"`,
  passes it into the `spawn_cc_new` RPC args; `_worker_handle` gains the field
  → **WorkerHandle carries `permission_mode`** (D4-e).
- `mcp_server.spawn_collaborator`: default flips `"bypass"` → `"standard"`;
  entry-validates `permission_mode`; PASSES it to `user_functions` (today it
  is dropped — this is the fix).
- `mcp_server.evoke`/`kernel_api.evoke`: pass `"bypass"` explicitly to
  `spawn_cc_resume` (D4-b); the MCP `evoke` tool gains `permission_mode`
  param (default `"bypass"`, explicit override allowed, entry-validated).
- **Retry nuance** (documented): a same-token retry returns the handle with
  the CURRENT call's `permission_mode`; the running worker is under the FIRST
  spawn's mode — coordinators should pass the same mode on retries.

---

## 2. Legacy create_collaborator marking (D4: "在返回与日志标记")

- `user_functions.create_collaborator` passes `permission_mode="bypass"`
  EXPLICITLY to `spawn_collaborator` (survives the default flip).
- Return strings gain the suffix `" ; permission_mode=bypass (legacy)"` on
  both success and failure shapes — the `"connect succeed"`/`"connect failed"`
  prefixes stay byte-exact so prefix-parsers survive.
- Durable log: `kernel_api.spawn_cc_new` logs a `permission_mode=bypass` line
  (kernel log, `logging.getLogger("cc-communicate.kernel")` style) whenever a
  bypass-mode spawn executes — the auditable record D4 requires.

---

## 3. Threat model README (D4)

New `v2_win/cc-communicate/README.md` (plugin root):

- **What it is** — one paragraph: a p2p transport for Claude Code sessions
  (same machine or host↔WSL): message pipes, connection lifecycle, structured
  envelopes, spawn/revive.
- **Threat model** — the D4 statement: `trusted single-user · trusted
  registered peer realm · NOT safe against a malicious local process with
  data-dir access`. One line of "why" per claim: plaintext-JSON data root with
  no authentication; any process that can write `data/` can impersonate a
  session, forge messages, or poison connection state; no crypto
  authentication (out of scope until the model widens — master plan §4.5).
- **permission_mode semantics** — table: `standard` (default for new spawns;
  the spawned CC makes normal permission decisions — a trust dialog may
  appear; coordinator-driven autonomy needs human approval) vs `bypass`
  (explicit opt-in for unattended automation;
  `--dangerously-skip-permissions`; legacy `create_collaborator` and the
  resume path are bypass). Config knobs (`CC_COMMUNICATE_*` envs) listed.
- Parity copy to v2_wsl; marketplace-tree copy at the next release sync
  (standing checklist item, noted).

---

## 4. Identity boundary

- Spawn requests' `caller_sid` + `cwd` validated at both boundaries
  (existing `validate_spawn_entry` + kernel dispatch — unchanged);
  `permission_mode` joins the validated surface (§1).
- The boundary is **documented, not crypto-enforced**: the threat model's
  honest statement is that identity is per-trusted-process — a local process
  with data-dir write access can impersonate any sid. The README states what
  IS enforced (id charset, path containment, single-active connection,
  permission_mode) and what is NOT (authenticated identity).

---

## 5. Error handling & edge cases (summary)

- Standard-mode worker may stall at the workspace trust dialog — the
  documented, accepted cost of the flip (R8); tmux (WSL) renders the dialog
  in the session pty; bypass remains the escape hatch. README + SKILL.md
  state this.
- `evoke` override param exists (default bypass); entry-validated.
- Retry mode mismatch — documented (§1).
- Legacy suffix appended after the reply; `startswith("connect succeed")`
  parsers unaffected; the failed path gets the same suffix.
- Remote spawns: `permission_mode` travels in the `spawn_cc_new` RPC args and
  is re-validated at the remote kernel's dispatch (same rule both sides).
- Unknown mode → INVALID_ARGUMENT at entry; `None` never reaches the kernel
  (API-layer defaults apply first).

---

## 6. Testing & docs

Unit (new file):
- `tests/unit/test_permission_mode.py` — `validate_permission_mode` matrix
  (standard/bypass ok; `"root"`/`""`/`42` → INVALID_ARGUMENT);
  `_permission_argv` both modes; `spawn_cc_new` default standard (captured
  `_detached_popen` argv has NO flag) vs explicit bypass (flag present);
  `spawn_cc_resume` default bypass (flag present); mcp_server
  `spawn_collaborator` default standard + passes mode through (captured
  user_functions call); kernel dispatch routes `permission_mode`; WorkerHandle
  carries the field; `create_collaborator` suffix + explicit bypass; `evoke`
  passes bypass.

Docs:
- SKILL.md: `spawn_collaborator` permission_mode semantics (default standard,
  bypass explicit), WorkerHandle shape + `permission_mode`, evoke doc.
- README created (§3).
- Exit gate: `py -3 tools/run_regression.py --tier auto` (Wave 3 exit adds
  the full live L1–L6 re-run per the user's locked decision); v2_wsl parity
  sync (incl. README); T43 record in `tested&2betest.md`.

## 7. Out of scope (deferred, documented)

- Per-session permission_mode tracking (the retry nuance is documented, not
  stored).
- Crypto authentication / wider threat model (master plan §4.5 — separate
  scope).
- Marketplace-tree sync (standing release checklist item).
- HP-11(余) — separate design, same wave.
