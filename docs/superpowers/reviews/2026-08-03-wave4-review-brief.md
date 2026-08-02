# Wave 4 Review Brief (for kimi-k3)

> Prepared 2026-08-03 after Wave 4 (HP-13-A, the final wave) was pushed
> (`80e701c`..`7c51784`, 8 commits, origin/main). Wave 3's audit PASSED
> (recorded as T48) with no fix-before-merge items; the reviewer confirmed
> HP-13-A as the last wave with the protocol-stable precondition met.
> Gate state at brief time: **GATE PASS** — T0 syntax (44 .py + 2 .js),
> T1 pytest (204 tests), T2 parity (32 files) + **T2 artifacts (33 files,
> templates pinned)**.

---

## 0. What the reviewer should know

- Project: cc-communicate — a Claude Code p2p plugin. Two byte-identical
  trees: `v2_win/` (Windows) and `v2_wsl/` (WSL2), parity-gated since
  Gate 0 (HP-13-B, D3). Python server in `server/`, JS hooks in `scripts/`.
- Wave 4 implements master plan §3 **HP-13-A** — canonical single source +
  generated win/wsl artifacts (decision W4-a..d, spec
  `docs/superpowers/specs/2026-08-03-wave4-hp13-source-unification-design.md`,
  plan `docs/superpowers/plans/2026-08-03-wave4-hp13-source-unification.md`).
- **Execution process changed this wave**: the durable inline-execution
  mandate was lifted by the user — Wave 4 ran through the SDD
  (subagent-driven-development) loop: one fresh implementer subagent per
  task, a task review after each (spec + quality), fix rounds where needed,
  and a final whole-branch review with deferred-minor triage. Records live
  in `.superpowers/sdd/2026-08-03-wave4-hp13-source-unification/progress.md`
  (gitignored workspace).
- Test records T49 (+ findings as T#s) in `tested&2betest.md` §1.

## 1. What HP-13-A delivers

- **Canonical source**: `v2_win/cc-communicate/` — unchanged location,
  unchanged content, single source of truth. `v2_wsl/cc-communicate/` is now
  a GENERATED artifact (still committed; verify has a committed reference).
- **`tools/build_artifacts.py`** (repo-tools level, like `migrate_data.py`):
  - `generate` — mirrors v2_win → v2_wsl byte-for-byte EXCEPT `.mcp.json`
    (written from the WSL template), deletes stale files (deletions
    propagate), never touches `data/`; **vacuous guard**: empty/missing
    v2_win → `GENERATE FAIL`, exit 1, v2_wsl untouched (final-review fix).
  - `verify` — the "one command from clean checkout" gate: regenerates the
    expected WSL tree into a temp dir (pure computation, zero repo writes),
    byte-compares ALL files including `.mcp.json` against the committed
    tree, pins `v2_win/.mcp.json` == win template (closes the parity
    allowlist hole), fails on 0 files compared. Prints
    `ARTIFACTS OK (33 files compared, templates pinned)`.
  - **Reuse, not fork**: imports `collect()`/`compare()` from
    `check_parity.py` (small refactor — main() behavior/output unchanged,
    parity tests lock it) — generator file-set rules can never drift from
    the parity gate's.
- **Templates**: `tools/artifact_templates/mcp.win.json` + `mcp.wsl.json`
  (byte-exact copies; sole difference `"python"` vs `"python3"`).
- **Gate integration**: tier T2 gains a second sub-step
  (`T2 artifacts` → `build_artifacts.py verify`); output
  `PARITY OK (32 files)` + `ARTIFACTS OK (33 files)`. LIVE_CHECKLISTS
  gains **L7** (Wave-4 smoke: live behavior unchanged). Shared
  `_tool_result()` helper (final-review DRY fix).
- **`.gitattributes`**: `v2_win/**`, `v2_wsl/**`,
  `tools/artifact_templates/**` pinned `text eol=lf` — closes a REAL hazard
  found during review: this repo runs `core.autocrlf=true` with LF blobs, so
  a `git checkout` writes CRLF working copies → byte gates fail while
  `git diff` (eol-normalizing) shows nothing.
- **Deliverable met**: clean checkout → `py -3 tools/build_artifacts.py
  verify` verifies both artifacts in one command; install entry
  (`${CLAUDE_PLUGIN_ROOT}/server/mcp_server.py`) and live behavior unchanged.

## 2. Review trail (the chain the reviewer should weigh)

| Stage | Outcome |
|---|---|
| Task 1 — check_parity refactor (collect/compare) | Review: ✅, Approved. 2 plan-mandated Minor nits (loose annotations; mutable default) → deferred |
| Task 2 — templates + build_artifacts.py + 9 tests | Implementer found 2 plan-code bugs (import-time-frozen template paths defeated the test monkeypatch; vacuous guard shadowed by earlier problems) → fixed minimally, reviewer confirmed correct. Review found 1 Important plan-mandated defect: the real-tree self-test was NEUTERED in suite runs (direct attribute assignment, no teardown — it verified a leftover fake tree) → fix round 1 (monkeypatch teardown) → re-review Approved with drift-injection proof |
| Task 3 — T2 artifacts sub-step + L7 | Review: ✅, Approved. 1 Important plan-mandated finding (verbatim duplication of the result-extraction block) → fix round 1 (`_tool_result` helper) → re-review Approved, output byte-identical |
| Task 4 — wave exit (controller-executed) | 0-diff invariant proven (generate → empty diff); auto gate GATE: PASS; LF pinning; L7 live smoke PASS (below); T49 |
| Final whole-branch review (6c8efa8..8d5b983) | **Ready for merge** — 7 deferred minors all ship-as-recorded; 3 Minor findings → ONE fix wave (7c51784: generate vacuous guard + test, L1-L4 help text, recovery-doc) → scoped re-review: all ADDRESSED, no new breakage, **Ready for merge** |

## 3. Gate evidence

- **Auto gate** (`py -3 tools/run_regression.py`): T0 syntax PASS (44 .py +
  2 .js), T1 pytest PASS (204 tests), T2 parity PASS (32 files), **T2
  artifacts PASS (33 files, templates pinned)** — GATE: PASS.
- **0-diff invariant**: `py -3 tools/build_artifacts.py generate` on the
  canonical tree → `GENERATED v2_wsl/cc-communicate (33 files)` + EMPTY
  `git diff --stat v2_wsl`. Guarded in-suite by the real-tree self-test
  (drift-injection proven: a tampered v2_wsl file fails the suite).
- **L7 live smoke** (T49, driven from the session's real plugin on the
  canonical tree): spawn_collaborator (w4-smoke-tok) → worker 8678a175,
  cwd == repo, WorkerHandle `permission_mode: standard` → correlated
  connect (reply matched connection_id) → send 1 probe → worker ACKed the
  exact message_id (store seq 122→123) → check_alive 1 → WSL peer
  4cefe529 registered, WSL session 2011c315 check_alive 1 → cross-realm
  connect + probe → routed reply through the host store with the exact
  message_id (seq 127) → both connections closed clean.
- Wave 3's L1-L6 protocol gates remain standing (unchanged server code —
  parity 32 files proves the server trees are byte-identical to the
  audited Wave-3 state).

## 4. Deferred minors (all ship-as-recorded, final-review triage)

1. `check_parity.py:28,43` — loose type annotations vs the plan's
   Interfaces prose (the plan's own code block is authoritative; no
   type-checking in CI).
2. `check_parity.py:43` — mutable default `allowlist=ALLOWLIST` aliases the
   module constant (membership-only use).
3. `build_artifacts.py:31-33` — `WIN_TEMPLATE`/`WSL_TEMPLATE` dead constants
   after the call-time fix (kept for the interface contract).
4. `build_artifacts.py` — missing template file → FileNotFoundError
   traceback instead of clean exit 1 (templates are committed).
5. `test_build_artifacts.py` — unused `monkeypatch` fixture param on the
   real-tree self-test (cosmetic).
6. `run_regression.py` `_tool_result` — empty-output (both streams)
   → IndexError → traceback instead of clean RED row (pre-existing pattern,
   gate still fails loudly).
7. `run_regression.py` — the line-32 comment "L1-L4 verbatim from the
   design spec section 4" (historical provenance; help text and docstring
   are L1-L7).

## 5. Suggested review focus

1. `build_artifacts.py` generate/verify semantics: mirror + substitution +
   stale deletion + vacuous guard; verify purity (temp-dir regen, template
   pins); `data/` untouchability.
2. The re-use relationship (check_parity.collect/compare) — file-set rules
   cannot drift.
3. The review-trail claim that plan-text defects (frozen templates,
   neutered self-test) were found by review and fixed with evidence.
4. `.gitattributes` LF pinning rationale vs the autocrlf hazard.
5. T49 vs the L7 checklist in `tools/run_regression.py`.
