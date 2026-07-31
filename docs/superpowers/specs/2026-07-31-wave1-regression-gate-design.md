# Wave 1 Exit Regression Gate — Design

> **Status**: design approved 2026-07-31 (brainstorming session). Next step:
> writing-plans → implementation plan → execute the gate.
> **Context**: `plans/2026-07-24-cc-communicate-hardening-master-plan.md` §6
> requires a full regression (unit + integration + Windows live + WSL/cross-realm
> live + parity gate) before Wave 2, but defines no concrete protocol. Wave 1
> (HP-06/01/02/03) is fully committed; T27's batch journal save fix has a
> pending live gate. This design makes the wave-exit gate concrete, repeatable,
> and enforceable.

---

## 1. Purpose

A single, repeatable **wave-exit gate**: run it before *any* wave transition
(Wave 1 → 2 now; Waves 2 → 3, 3 → 4 later, unchanged). It answers one question
with a machine-checkable verdict plus a disciplined live tier: **"is this wave
done?"** Wave 1 is merely the first execution.

Primary deliverable: `tools/run_regression.py` (stdlib-only, mirroring the
`tools/check_parity.py` precedent) + the live-gate checklists it prints. The
script IS the protocol; this spec describes it.

## 2. Gate anatomy

| Tier | What | How | Pass bar |
|---|---|---|---|
| T0 Syntax | ast.parse all `v2_*/*/server/*.py` + `node --check` on `scripts/*.js` | script internal | 0 errors (JS: WARN not-fail if node missing) |
| T1 Unit/integration | full pytest suite (currently 50 tests) | `py -3 -m pytest -q` (subprocess, cwd=repo root) | all pass |
| T2 Parity | win/wsl hash compare | `py -3 tools/check_parity.py` (subprocess) | exit 0 / `PARITY OK` |
| L1 Spawn-race (T27 live gate) | real `create_collaborator`: exactly **one** spawned window, no error window | live (I drive) | single window, child gets real sid, no `data/`-cwd error window |
| L2 Reconnect (T25 live gate) | real CC closed → `evoke`/`connect` resumes it with correct cwd | live | reconnect succeeds ("No conversation found" absent) |
| L3 Cross-realm cursors (R2 live gate) | current v2_wsl deployed to WSL, WSL kernel restarted, host↔WSL `listen_v2` with per-store cursors | live, cross-realm | per-store cursor independence; no cross-clock interference |
| L4 Multi-collab stress (T26-style) | 1 coordinator + 3–4 collaborators, multiple rounds, `listen_v2` cursors | live | zero message loss, clean end |

### Rules

- **Auto tiers (T0–T2) are blocking**: any RED → GATE RED, fix + retest.
- **Live tiers (L1–L4) are blocking**: any RED → GATE RED, bug recorded as T#
  in `tested&2betest.md` §1, fixed, then **that gate alone re-runs** (tiers are
  independent; a live failure does not invalidate other tiers' results).
- **GATE is GREEN only when all 7 tiers pass** → then and only then Wave 2
  (HP-07 first) is allowed.
- Tiers are independent: no tier consumes another's output.

### Deliverable artifacts

1. `tools/run_regression.py`
2. This spec (`docs/superpowers/specs/`)
3. The run itself → one T# entry in `tested&2betest.md` §1 (e.g. T28) with
   per-tier results; per-bug T# entries for anything the live gates uncover.

## 3. `tools/run_regression.py` design

**CLI** (`py -3 tools/run_regression.py`):

| Invocation | Behavior |
|---|---|
| (default) / `--tier auto` | runs T0 → T1 → T2; per-tier table; exit 0 iff all PASS, nonzero on any RED; continues remaining tiers after a failure (reports everything) |
| `--tier live` | **prints** L1–L4 checklists (steps, expected, T# template) — no execution, exit 0 (informational only); the live-session driver |
| `--tier all` | auto tiers, then prints live checklist with "run live gates now? (y/N)" |
| `--help` | one-line summary per tier |

**Behavior details:**

- T1: `subprocess` with `sys.executable -m pytest -q`, cwd = repo root; on
  failure print the last ~40 lines of pytest output (RED is diagnosable
  without re-running).
- T2: `subprocess` `tools/check_parity.py`, check exit code — no duplicated
  hash logic; check_parity stays the single parity source.
- Output: one line per tier, e.g.
  `T1 pytest ......... PASS (50 passed in 4.62s)` / `RED (<reason>)`,
  then final `GATE: PASS` / `GATE: RED`. Exit code 0 iff GATE PASS.
- T0 syntax: ast.parse each `v2_*/*/server/*.py` and `scripts/*.js`;
  `node --check` if node on PATH, else `WARN (node not found)` — tier stays
  PASS with visible warning.
- Stdlib only (ast, os, subprocess, sys, pathlib). No new dependencies.

**Testing the script** (`tests/unit/test_run_regression.py`, following the
`test_parity.py` import precedent — `sys.path.insert` + import):
1. Missing/empty v2 trees → RED.
2. Monkeypatched tier functions returning PASS/RED → table + exit code correct.
3. `GATE: RED` when any tier RED; `GATE: PASS` only when all green.
- No subprocess in tests: tier functions are callable directly; the CLI entry
  wraps them. (The pytest/parity invocations themselves are already covered by
  the real suite; the test locks the *gating logic*.)

**Reuse:** the same script is the Wave 2/3/4 exit gate unchanged; live
checklists may grow (new gates appended as new sections).

## 4. Live gate procedures

Every live gate has the same 6-field checklist shape, printed verbatim by
`--tier live`:

```
L1 — Spawn-race re-test (T27 live gate)
  Why:      batch journal save removed the event-loop stall; must prove exactly
            one spawned window, no error window
  Prereq:   fresh kernel (no stray CCs), real plugin data dir
  Steps:    my_session_id -> create_collaborator(prompt) -> observe windows ->
            child my_session_id -> check_alive
  Expected: exactly ONE spawned window; no error window containing a data/ path;
            child returns a real sid
  Pass:     all three observations hold
  Record:   T# in tested&2betest.md §1 with window count + error-window evidence
```

Run order (cheapest/Windows-only first; cross-realm and stress later):

1. **L1 Spawn-race (T27)** — validates the batch journal save under a real CC
   spawn. Evidence: window count, child's `my_session_id`, absence of the
   `data/`-cwd error window.
2. **L2 Reconnect (T25)** — real CC closed, then `evoke`/`connect` resumes it;
   verifies `sessions[sid].cwd` flows through `spawn_cc_resume` (the "No
   conversation found" fix) on the new kernel code.
3. **L3 Cross-realm cursors (R2)** — deploy current v2_wsl into WSL (parity
   guarantees identity with win), restart the WSL kernel (picks up
   HP-01/02/03), then: host↔WSL conversation, `listen_v2` with cursors on both
   stores; verify per-store cursor independence and no cross-clock
   interference (PB-3's fix, live). This is master-plan R2's "Wave 1 live gate
   实测".
4. **L4 Multi-collab stress** — T26-style: 1 coordinator + 3–4 collaborators,
   multiple rounds, `listen_v2` cursors, verify zero loss + clean end. This is
   the load test for batch journal save (many concurrent drains, one fsync per
   drain cycle).

**Session mechanics:** the builder drives all four (spawn CCs with bypass
permissions, watch `data/` side effects, record results). The user bails out
only on trust prompts or interactive WSL steps (L3's WSL side is the likely
spot).

**Recording:** the whole gate run gets one T# (e.g. T28 "Wave 1 exit
regression") summarizing per-tier results; any live bug found gets its own T#
with the fix (fail = fix + retest that gate).

## 5. Wrap-up scope (doc cleanup, done after the gate run)

- `tested&2betest.md` §2: B1–B7 are all resolved (each carries CONFIRMED/DONE
  updates) but the section still reads "To-be-tested". Add a status banner at
  the section top: all B1–B7 resolved as of the run date; section retired to
  historical reference.
- §1 "Potential bugs": PB-1 (same-ms overwrite), PB-2 (clock-backward), PB-3
  (cross-realm skew) are resolved by HP-01/02 (sequence + per-store cursors)
  but still say "not fixed". Update each with "Resolved by HP-01/HP-02" +
  pointer to the covering tests (`test_message_record.py` burst &
  clock-backward, `test_cursor_ack.py` per-store).
- §1 gets the gate-run T# (T28) and any bug T#s found.

## 6. Execution order

1. Build `tools/run_regression.py` + its tests (TDD; new file lives outside
   the v2 trees, parity unaffected).
2. Run auto tiers (T0–T2) — expected GREEN immediately (suite green now; the
   script makes it a gate).
3. Live session: L1 → L2 → L3 → L4. L3 includes syncing v2_wsl into WSL +
   WSL kernel restart. Fail → fix + retest that gate.
4. Record T# results, doc cleanup (Section 5), commit everything.
5. **GATE: PASS** → Wave 2 (HP-07 structured results) unlocked; the script
   becomes the standing wave-exit gate.

## 7. Out of scope (deliberately)

- No changes to `server/` or `scripts/` code (unless a live gate uncovers a
  bug — then that fix is in scope).
- No Wave 2 design work.
- No changes to the hardening master plan's locked decisions (D1–D10).
