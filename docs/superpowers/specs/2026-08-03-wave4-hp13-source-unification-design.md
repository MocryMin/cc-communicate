# Wave 4 — HP-13-A Canonical Single Source + Generated win/wsl Artifacts: Design

> **Status**: design approved 2026-08-03 (brainstorming session, sections 1–3,
> user-approved per section; architecture decision A locked via Q&A).
> Next step: writing-plans → implementation plan → **inline execution** (durable
> user mandate: no context-heavy subagents).
> **Context**: `plans/2026-07-24-cc-communicate-hardening-master-plan.md` §3
> Wave 4 = HP-13-A (the last wave). **D3 is locked**: the parity gate
> (HP-13-B, `tools/check_parity.py`) has been live since Gate 0; the canonical
> single-source migration was deferred until the protocol was stable — the
> preconditions are met (193 tests + 6 live gates PASS, Waves 1–3 audited).
> **Deliverables (master plan §3)**: clean checkout → one command
> generates/verifies both plugin artifacts; install entry + live behavior
> unchanged.
> **External audit note (kimi-k3, 2026-08-03)**: Wave 3 PASSED; HP-13-A
> confirmed as the final wave, risk HIGH, precondition satisfied. The 4 minor
> review notes are unchanged standing items (marketplace sync, T46 re-test,
> known_pids TypeError, count-cap-by-design) — none interacts with this design
> beyond the marketplace note being declared out of scope (§4).

---

## 0. Decisions locked (Q&A, 2026-08-03)

| # | Decision | Value |
|---|---|---|
| W4-a | Architecture | **A: `v2_win/cc-communicate/` is the canonical committed source** (it doubles as the live Windows deployment). `v2_wsl/cc-communicate/` becomes a **generated, still-committed artifact** produced by a new generator. No neutral tree, no path churn (every test/tool/doc reference to `v2_win` keeps working) |
| W4-b | Generated artifact form | `v2_wsl` stays **committed** (PRs show artifact diffs; verify has a committed reference; git history of the tree is kept). Verification = regenerate-to-temp → byte-diff against committed. NOT gitignored |
| W4-c | Gate form | New `tools/build_artifacts.py` with `generate` + `verify`; `verify` added as a **second sub-step of tier T2** in `tools/run_regression.py` (T0/T1/T2 gate surface unchanged); `check_parity.py` itself unchanged (`.mcp.json` stays allowlisted there — it legitimately differs — while `verify` closes that hole by pinning both `.mcp.json`s to platform templates) |
| W4-d | Reuse | `build_artifacts.py` **imports the walk/compare helpers from `check_parity.py`** (small refactor: expose a `collect()` function + compare logic; existing parity tests stay green). The generator's file-set rules can never drift from the parity gate's |

---

## 1. Canonical source + generator core

### 1.1 Canonical tree

- `v2_win/cc-communicate/` — unchanged location, unchanged content, single
  source of truth. Nobody edits `v2_wsl/` by hand anymore.
- Excluded from the artifact content (same rules as `check_parity`):
  `data/`, `__pycache__/`, `.git`, `.pytest_cache`, `node_modules`;
  suffixes `*.pyc`, `*.log`. `data/` holds per-machine runtime state and is
  **never** touched by generate or verify.

### 1.2 `tools/build_artifacts.py` (repo-tools level, like `migrate_data.py`; NOT parity-synced)

**`generate`** — materializes `v2_wsl/cc-communicate/` from `v2_win/`:

1. Walk `v2_win/cc-communicate/` with the parity exclude rules.
2. **Mirror semantics**: every non-excluded file copied byte-for-byte to
   `v2_wsl/cc-communicate/`; `.mcp.json` written from the WSL platform
   template instead of copied; **stale files present in `v2_wsl` but absent
   from `v2_win` are deleted** (deletions propagate — today parity would
   merely report "only in wsl").
3. `data/` and its contents are never enumerated, copied, or deleted.

**`verify`** — the "one command from clean checkout" gate (exit 1 on drift):

1. Regenerate the expected WSL tree **into a temp dir** (pure computation;
   zero writes to the repo).
2. Byte-compare temp output vs the committed `v2_wsl/cc-communicate/` —
   **all** files, **including `.mcp.json`** (vs the WSL template).
3. Pin the canonical tree: `v2_win/cc-communicate/.mcp.json` must equal the
   win template byte-for-byte.
4. Print `ARTIFACTS OK (N files)` or a diff list; exit code reflects the
   outcome.

**Key invariant**: `generate` on the current tree is a **0-diff** against the
committed `v2_wsl` — asserted during execution before anything else ships, and
kept by the self-test in §3.

### 1.3 Platform templates

- `tools/artifact_templates/mcp.win.json` + `mcp.wsl.json` — captured
  byte-exact from today's two files (sole difference: `"command": "python"`
  vs `"command": "python3"`). The mirrored content includes the
  platform-neutral metadata files (`.gitignore`, `.claude-plugin/plugin.json`,
  README, hooks/scripts/server/skills) — everything inside
  `cc-communicate/` except the parity excludes.
- Build metadata lives outside the plugin trees → not parity-compared, not
  duplicated. `verify` treats them as the pinned references for `.mcp.json`.

### 1.4 `check_parity.py` refactor (minimal)

- Expose the walk (`collect(root) -> {rel: Path}`) and the compare logic as
  importable functions; `main()` keeps its exact current behavior and output
  (parity tests stay green). `ALLOWLIST`, `EXCLUDE_DIRS`,
  `EXCLUDE_SUFFIXES` stay module-level constants reused by the generator.
- Docstring gains one line stating the new meaning: with the generator in
  place, a parity pass = "committed `v2_wsl` matches what the generator
  produces" for all non-allowlisted files.

---

## 2. Gate integration

- `check_parity.py` unchanged in behavior — still the win≡wsl byte gate for
  all non-allowlisted files, still invoked by tier T2.
- **`build_artifacts.py verify` added as a second T2 sub-step**: output
  becomes `PARITY OK (32 files)` + `ARTIFACTS OK (33 files)` — the artifact
  count is the parity file set (32, all non-allowlisted files present in
  both trees) plus `.mcp.json` (1). Together they
  cover the whole artifact contract: parity = non-`.mcp.json` equivalence;
  verify = full equivalence including `.mcp.json` template pins + win
  template pin. No new tier (T0/T1/T2 gate surface stays as approved in
  prior waves; the Wave-4 gate evidence keeps the same shape).
- The **editing workflow rule** replaces the manual dual-tree sync
  convention (recovery notes §3):
  > Edit `v2_win/` only → `py -3 tools/build_artifacts.py generate` →
  > commit both trees.
  Forgetting the generate step surfaces as a **red T2** (parity or verify
  fails) instead of silent drift — R6 (manual dual-domain sync) is closed
  for generated files.

---

## 3. Tests

- `tests/parity/test_parity.py` — unchanged (still guards the parity gate
  itself, including the vacuous-pass guard).
- New `tests/unit/test_build_artifacts.py` (repo-tool test pattern per
  `test_migrate_data.py` / `test_parity.py`: import + monkeypatch paths,
  subprocess for the CLI entry):
  1. `generate` into a temp output dir: mirrors win content minus excludes;
     substitutes `.mcp.json` from the WSL template; **removes stale files**
     (mirror semantics); never touches `data/`.
  2. `verify` **passes on the real committed tree** (self-test — any
     generator drift that would change `v2_wsl` breaks the suite
     immediately).
  3. `verify` fails on each drift class: mutated file; deleted file; stale
     extra file; `v2_wsl/.mcp.json` deviating from the WSL template;
     `v2_win/.mcp.json` ≠ win template.
  4. Vacuous guard: empty win tree → fail (mirrors the parity vacuous-pass
     rule).
- conftest: nothing new in the reload list (repo-tool, not a server module).

---

## 4. Out of scope (standing items, unchanged)

- `cc-communicate-marketplace/` sync (T30/T31/T32/T35 + Wave 2 + Wave 3) —
  separate release item (kimi-k3 minor #3).
- WSL deployment push (`//wsl.localhost` copy to
  `/home/mocry/projects/v2_wsl/cc-communicate`) — stays a manual copy of the
  repo's regenerated `v2_wsl` (L3 mechanics unchanged).
- HP-11 default data-root switch — outside this period (master plan §4.6).
- known_pids bound-trim TypeError; T46 re-test after a CC update —
  deferred minors, unchanged.

---

## 5. Exit gates (wave convention)

1. **Auto gate**: full suite — T0 syntax (py_compile + parity file set),
   T1 pytest (193 + new tests), T2 parity **+ artifact verify**.
2. **Live smoke gate** (this is a tooling wave, not a protocol wave):
   - one real spawn + send/ack through the repo `v2_win` (proves the
     deliverable's core claim: live behavior unchanged);
   - `check_alive` of the WSL peer + one cross-realm probe (proves the WSL
     install path is untouched);
   - record as T49; any findings get their own T#.
3. Push (user approval), then the external review package.

---

## 6. Records

- T49 = Wave 4 acceptance (auto gate + live smoke gate).
- Any finding during execution → own T# in `tested&2betest.md` §1.
- Audit record after the external review.
