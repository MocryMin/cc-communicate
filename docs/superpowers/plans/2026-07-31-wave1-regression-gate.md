# Wave 1 Exit Regression Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the repeatable wave-exit regression gate (`tools/run_regression.py` + live checklists), run it once as the Wave 1 → Wave 2 gate, record results as T# in `tested&2betest.md`, and retire stale doc status (B1–B7, PB-1/2/3).

**Architecture:** The gate is one stdlib-only script (`tools/run_regression.py`) whose `--tier auto` runs the three scripted tiers (T0 syntax / T1 pytest / T2 parity) and prints a machine-checkable `GATE: PASS|RED` verdict with exit code; `--tier live` prints the four live-gate checklists (L1 spawn-race / L2 reconnect / L3 cross-realm cursors / L4 multi-collab stress) that a human drives. The script lives in `tools/` — **outside** both v2 plugin trees — so parity is unaffected. Gating logic is locked by unit tests (monkeypatched tiers, no subprocess); the real tier implementations are exercised by the real suite.

**Tech Stack:** Python 3.10+ (stdlib only: ast, argparse, os, shutil, subprocess, sys, pathlib), pytest. No new dependencies.

## Global Constraints

- **`py -3` for ALL Python invocations on Windows** — `python`/`python3` are broken Store stubs. From repo root: `py -3 -m pytest`, `py -3 tools/...`.
- **Parity discipline**: `v2_win` ↔ `v2_wsl` plugin trees must stay byte-identical outside the `.mcp.json` allow-list. This plan never touches either tree (new file lives in `tools/`, doc edits touch only `tested&2betest.md`).
- **Test isolation**: tests never write any real plugin `data/` — irrelevant here (the new tests touch no data paths), but the real gate's live tiers deliberately use the real plugin data dir.
- **T# recording**: every bug found or gate run gets an entry in `tested&2betest.md` §1 (`### T<next#> — <title>`, with Method / Result / Confidence).
- **Commit convention**: `feat(...)` / `test(...)` / `fix(...)` / `docs(...)` prefixes, one commit per task.
- **Spec**: `docs/superpowers/specs/2026-07-31-wave1-regression-gate-design.md` — this plan implements it exactly; live checklists in the script are verbatim from spec §4.
- **TDD**: each code task writes the failing test first (RED), then implements (GREEN).
- **Import precedent for tests that import `tools/` modules**: `sys.path.insert(0, str(TOOLS)); import <module>` — see `tests/parity/test_parity.py`.

---

### Task 1: `tools/run_regression.py` + gating-logic tests

**Files:**
- Create: `tools/run_regression.py`
- Create: `tests/unit/test_run_regression.py`

**Interfaces:**
- Consumes: existing `tools/check_parity.py` (shelled out to, not imported — it owns parity logic); existing `tests/parity/test_parity.py` import precedent.
- Produces (Task 2 consumes):
  - `run_regression.main(argv: list[str] | None = None) -> int` — CLI entry; 0 iff GATE PASS; 2 on bad tier.
  - `run_regression.syntax_check() -> (status, detail)` — T0; status ∈ {"PASS","RED"}.
  - `run_regression.pytest_run() -> (status, detail)` — T1.
  - `run_regression.parity_run() -> (status, detail)` — T2.
  - `run_regression.LIVE_CHECKLISTS` — list of `(header: str, body: str)` for L1–L4 (Task 3–6 drive live gates from these).
  - Module constants: `REPO`, `TOOLS`, `WIN`, `WSL` (monkeypatch targets for tests).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_run_regression.py` (complete file):

```python
"""Gating logic of tools/run_regression.py (spec: 2026-07-31-wave1-regression-gate-design).

No subprocess here: tier functions are monkeypatched so these tests lock the
GATE pass/red decision, the tree preflight, and live-checklist printing. The
real tier implementations are exercised by the real suite / parity gate.
Import mechanism follows tests/parity/test_parity.py. Always pass explicit
argv to main() - main(None) would read pytest's own sys.argv."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"


def _import():
    sys.path.insert(0, str(TOOLS))
    import run_regression
    return run_regression


def _run(main, argv, capsys):
    code = main(argv)
    return code, capsys.readouterr().out


def test_missing_tree_is_red(tmp_path, monkeypatch, capsys):
    rr = _import()
    monkeypatch.setattr(rr, "WIN", tmp_path / "win-missing")
    monkeypatch.setattr(rr, "WSL", tmp_path / "wsl-missing")
    code, out = _run(rr.main, [], capsys)
    assert code == 1
    assert "GATE: RED" in out and "missing" in out


def test_tree_without_server_py_is_red(tmp_path, monkeypatch, capsys):
    """Existing trees but zero server/*.py -> vacuous syntax check must not pass."""
    rr = _import()
    win, wsl = tmp_path / "win", tmp_path / "wsl"
    (win / "server").mkdir(parents=True)
    (wsl / "server").mkdir(parents=True)
    monkeypatch.setattr(rr, "WIN", win)
    monkeypatch.setattr(rr, "WSL", wsl)
    code, out = _run(rr.main, [], capsys)
    assert code == 1
    assert "GATE: RED" in out


def test_gate_red_when_any_tier_red(monkeypatch, capsys):
    rr = _import()
    monkeypatch.setattr(rr, "syntax_check", lambda: (rr.RED, "boom"))
    monkeypatch.setattr(rr, "pytest_run", lambda: (rr.PASS, "ok"))
    monkeypatch.setattr(rr, "parity_run", lambda: (rr.PASS, "ok"))
    code, out = _run(rr.main, ["--tier", "auto"], capsys)
    assert code == 1
    assert "T0 syntax" in out and "RED" in out
    assert out.strip().endswith("GATE: RED")


def test_gate_pass_only_when_all_green(monkeypatch, capsys):
    rr = _import()
    monkeypatch.setattr(rr, "syntax_check", lambda: (rr.PASS, "3 .py parsed"))
    monkeypatch.setattr(rr, "pytest_run", lambda: (rr.PASS, "50 passed"))
    monkeypatch.setattr(rr, "parity_run",
                        lambda: (rr.PASS, "PARITY OK (120 files compared)"))
    code, out = _run(rr.main, ["--tier", "auto"], capsys)
    assert code == 0
    assert out.strip().endswith("GATE: PASS")


def test_live_tier_prints_all_checklists_exits_0(capsys):
    rr = _import()
    code, out = _run(rr.main, ["--tier", "live"], capsys)
    assert code == 0
    for hdr in ("L1", "L2", "L3", "L4"):
        assert hdr in out


def test_all_tier_runs_auto_then_prints_live(monkeypatch, capsys):
    rr = _import()
    monkeypatch.setattr(rr, "syntax_check", lambda: (rr.PASS, "ok"))
    monkeypatch.setattr(rr, "pytest_run", lambda: (rr.PASS, "ok"))
    monkeypatch.setattr(rr, "parity_run", lambda: (rr.PASS, "ok"))
    code, out = _run(rr.main, ["--tier", "all"], capsys)
    assert code == 0
    assert "GATE: PASS" in out and "L1" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -3 -m pytest tests/unit/test_run_regression.py -v`
Expected: `ModuleNotFoundError: No module named 'run_regression'` (import failure).

- [ ] **Step 3: Write `tools/run_regression.py`**

Complete file (verbatim — the live checklists ARE the protocol text; spec §4):

```python
"""Wave-exit regression gate (spec: docs/superpowers/specs/2026-07-31-wave1-regression-gate-design.md).

Usage:
  py -3 tools/run_regression.py [--tier auto|live|all]

  auto (default): run the scripted tiers T0 (syntax) / T1 (pytest) /
    T2 (parity), print a per-tier table, exit 0 iff all PASS (GATE: PASS).
  live: print the L1-L4 live-gate checklists - informational only, exit 0.
  all: auto tiers first, then the live checklists.

The gate is GREEN only when all 7 tiers (T0-T2 + L1-L4) pass. A RED tier
means fix + retest before the wave transition it guards.
"""
from __future__ import annotations

import argparse
import ast
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
WIN = REPO / "v2_win" / "cc-communicate"
WSL = REPO / "v2_wsl" / "cc-communicate"
SERVER_REL = "server"
SCRIPTS_REL = "scripts"
PASS, RED = "PASS", "RED"

# L1-L4 live-gate checklists (verbatim from the design spec section 4).
# A human drives these; the script only prints them.
LIVE_CHECKLISTS = [
    ("L1 - Spawn-race re-test (T27 live gate)", """\
  Why:      batch journal save removed the event-loop stall; must prove exactly
            one spawned window, no error window
  Prereq:   fresh kernel (no stray CCs), real plugin data dir
  Steps:    my_session_id -> create_collaborator(prompt) -> observe windows ->
            child my_session_id -> check_alive
  Expected: exactly ONE spawned window; no error window containing a data/ path;
            child returns a real sid; check_alive -> 1
  Pass:     all three observations hold
  Record:   T# in tested&2betest.md sec1 with window count + error-window evidence"""),
    ("L2 - Reconnect live gate (T25)", """\
  Why:      verify sessions[sid].cwd flows through evoke -> spawn_cc_resume so a
            closed CC resumes in its project cwd (the "No conversation found" fix)
  Prereq:   a real CC whose session was registered, then CLOSED
  Steps:    CC-A (real, project cwd P): my_session_id -> sid-A; register pair ->
            close CC-A completely -> check_alive(sid-A) -> 0 ->
            evoke(sid-A) -> claude --resume sid-A in cwd P ->
            send_message to sid-A succeeds
  Expected: resume lands in cwd P (session_ctrl start event cwd == P);
            delivery succeeds; no "No conversation found"
  Pass:     reconnect succeeds with correct cwd
  Record:   T# with resume cwd evidence (session_ctrl start event cwd field)"""),
    ("L3 - Cross-realm cursor live gate (R2)", """\
  Why:      per-store cursors must stay independent across host<->WSL with no
            cross-clock interference (PB-3 fix, live) - master plan R2's live gate
  Prereq:   v2_wsl deployed to WSL and identical to v2_win (parity is green);
            WSL kernel restarted so it runs HP-01/02/03 code
  Steps:    sync v2_wsl/cc-communicate -> WSL project dir; restart WSL kernel ->
            host CC-A <-> WSL CC-B register pair ->
            A sends 3 to B; B listen_v2(cursors) -> seq 1..3 -> B ACKs cursor=2 ->
            B sends 2 back to A; A listen_v2 with its OWN store cursor ->
            A re-listen_v2 same cursors -> no re-delivery (archived by cursor)
  Expected: each side's cursor map only touches its own store; no message seen
            twice, none lost; clock skew irrelevant
  Pass:     per-store cursor independence + zero loss
  Record:   T# with per-side cursor maps + message counts"""),
    ("L4 - Multi-collab stress (T26-style)", """\
  Why:      load test for batch journal save: many concurrent drains, one fsync
            per drain cycle; must be zero-loss under real load
  Prereq:   fresh kernel, 3-4 collaborators spawnable
  Steps:    coordinator spawns 3-4 collaborators (create_collaborator) ->
            5+ rounds: coordinator sends 1 tagged message to each collaborator;
            each collaborator listen_v2 + ACKs cursor + replies ->
            verify every message_id received exactly once; clean close_connection
  Expected: zero loss, zero duplicates, clean end
  Pass:     all rounds complete with 1:1 send/receive
  Record:   T# with round/message counts"""),
]


def _run(cmd, cwd=REPO):
    """Run a command, capture output. cmd items are str()-coerced (Path-safe)."""
    return subprocess.run([str(x) for x in cmd], cwd=str(cwd),
                          capture_output=True, text=True)


def _check_trees() -> bool:
    """Both v2 trees must exist before any tier runs (vacuous-pass guard)."""
    missing = [str(p) for p in (WIN, WSL) if not p.is_dir()]
    if missing:
        print("GATE: RED (missing tree(s): %s)" % ", ".join(missing))
        return False
    return True


def syntax_check():
    """T0: ast.parse every server/*.py in both trees + node --check scripts/*.js.

    node missing -> tier still PASS with a visible WARN in the detail (per
    spec: JS check is best-effort). Zero server .py in a tree is RED (a
    vacuous syntax pass must never print OK)."""
    problems, n_py, n_js = [], 0, 0
    for tree in (WIN, WSL):
        py_files = sorted((tree / SERVER_REL).glob("*.py"))
        n_py += len(py_files)
        if not py_files:
            problems.append("no server/*.py in %s" % tree)
        for py in py_files:
            try:
                ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError as e:
                problems.append("%s: %s" % (py.relative_to(REPO), e))
    node = shutil.which("node")
    if node is None:
        return PASS, ("%d .py parsed; 0 .js checked (WARN: node not found)" % n_py)
    for tree in (WIN, WSL):
        for js in sorted((tree / SCRIPTS_REL).glob("*.js")):
            n_js += 1
            r = _run([node, "--check", str(js)])
            if r.returncode:
                problems.append("%s: %s" % (js.relative_to(REPO),
                                            r.stderr.strip().splitlines()[-1]))
    if problems:
        return RED, "; ".join(problems[:5])
    return PASS, ("%d .py + %d .js parsed clean" % (n_py, n_js))


def pytest_run():
    """T1: full pytest suite. On RED print the output tail so the failure is
    diagnosable without re-running."""
    r = _run([sys.executable, "-m", "pytest", "-q"])
    detail = next((ln for ln in reversed(r.stdout.splitlines()) if ln.strip()),
                  "").strip()
    if r.returncode:
        print("--- pytest output (tail) ---")
        print("\n".join(r.stdout.splitlines()[-40:]))
        print("--- end ---")
        return RED, detail or "pytest exit %d" % r.returncode
    return PASS, detail


def parity_run():
    """T2: shell out to tools/check_parity.py - it owns the hash logic."""
    r = _run([sys.executable, str(TOOLS / "check_parity.py")])
    if r.returncode:
        return RED, (r.stdout.strip() or r.stderr.strip()).splitlines()[-1]
    return PASS, r.stdout.strip().splitlines()[-1]


def print_live_checklists():
    for header, body in LIVE_CHECKLISTS:
        print(header)
        print(body)
        print()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="cc-communicate wave-exit regression gate "
                    "(spec: docs/superpowers/specs/2026-07-31-wave1-regression-gate-design.md)")
    parser.add_argument("--tier", choices=("auto", "live", "all"), default="auto",
                        help="auto: scripted tiers T0-T2; live: print L1-L4 "
                             "checklists only; all: both")
    args = parser.parse_args(argv)
    if args.tier == "live":
        print_live_checklists()
        return 0
    if not _check_trees():
        return 1
    results = [("T0 syntax", syntax_check()),
               ("T1 pytest", pytest_run()),
               ("T2 parity", parity_run())]
    for name, (status, detail) in results:
        print("%-10s %-4s (%s)" % (name, status, detail))
    gate_ok = all(status == PASS for _, (status, _) in results)
    print("GATE: %s" % ("PASS" if gate_ok else "RED"))
    if args.tier == "all" and gate_ok:
        print("\nLive gates next (drive per checklist):\n")
        print_live_checklists()
    return 0 if gate_ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -3 -m pytest tests/unit/test_run_regression.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Smoke the real script (auto tier, must be green on current code)**

Run: `py -3 tools/run_regression.py`
Expected: three tier lines, all PASS, final `GATE: PASS`, exit code 0.
Run: `py -3 tools/run_regression.py --tier live`
Expected: four L-headers (L1–L4) printed, exit code 0.

- [ ] **Step 6: Run the full suite + parity (no regressions)**

Run: `py -3 -m pytest -q` → all PASS (50 existing + 6 new).
Run: `py -3 tools/check_parity.py` → `PARITY OK` (untouched trees, expected).

- [ ] **Step 7: Commit**

```bash
git add tools/run_regression.py tests/unit/test_run_regression.py
git commit -m "feat(gate): wave-exit regression gate script (T0/T1/T2 + L1-L4 checklists)

- tools/run_regression.py: --tier auto|live|all; GATE PASS/RED verdict + exit
  code; T0 ast+syntax (node --check best-effort), T1 pytest (tail on RED),
  T2 parity subprocess; tree preflight guards vacuous passes
- tests/unit/test_run_regression.py: 6 tests locking gating logic via
  monkeypatched tiers (no subprocess)
- spec: 2026-07-31-wave1-regression-gate-design.md"
```

---

### Task 2: Run the gate — auto tiers (execution, expected GREEN)

**Files:**
- Record: `tested&2betest.md` §1 (T28 entry, partially — full results after Task 6)

**Interfaces:**
- Consumes: Task 1's `tools/run_regression.py`.
- Produces: evidence that the scripted tiers pass on current code (prereq for
  declaring Wave 1 complete pending live gates).

- [ ] **Step 1: Run the auto tier**

Run: `py -3 tools/run_regression.py --tier auto`
Expected: `T0 syntax PASS`, `T1 pytest PASS (50 passed ...)`, `T2 parity PASS (PARITY OK ...)`, `GATE: PASS`, exit 0.
If any tier is RED: stop and record the failure — the suite was green at plan
time (50 passed in 4.62s), so a RED here means something regressed; fix it
(following the repo's T# convention: record the bug + fix in
`tested&2betest.md` §1) before continuing.

- [ ] **Step 2: Capture the evidence line**

Copy the exact tier table output (the three tier lines + GATE line) into the
session transcript — it becomes the T28 record's per-tier results in Task 6.

---

### Task 3: Live gate L1 (spawn-race re-test) + L2 (reconnect)

**Files:**
- Record: `tested&2betest.md` §1 (bug T#s if a gate fails; final T28 in Task 6)

**Interfaces:**
- Consumes: real plugin data dir; real MCP tools (`my_session_id`,
  `create_collaborator`, `check_alive`, `evoke`, `connect`, `send_message`);
  the L1/L2 checklist text printed by `py -3 tools/run_regression.py --tier live`.
- Produces: live evidence for the Wave 1 exit gate; T# entries per result.

- [ ] **Step 1: Run L1 (spawn-race re-test)**

Prereq: fresh kernel (no stray CC processes); real plugin data dir.
1. Drive `my_session_id` from the coordinator CC → record coordinator sid.
2. Call `create_collaborator(caller_sid, cwd, prompt=...)`.
3. Observe the spawned windows: **count them**, and note whether any window is
   an error window containing a `data/` path (the T27 symptom).
4. In the child CC: `my_session_id` → record child sid.
5. `check_alive(child sid)` → must be 1.
Expected: exactly ONE spawned window, no error window with `data/`, child got a
real sid. RED if any of the three fails → record bug as its own T# (per the
T27 pattern: symptom → mechanism → fix → re-run this gate) and re-run L1.

- [ ] **Step 2: Run L2 (reconnect live gate)**

1. Real CC-A in project cwd P: `my_session_id` → sid-A; register a conversation
   pair with the coordinator.
2. **Close CC-A completely** (exit the CC process).
3. `check_alive(sid-A)` → 0 (kernel sees it dead).
4. `evoke(sid-A)` → should spawn `claude --resume sid-A` **in cwd P**.
5. Verify the resume: the new SessionStart's `cwd` field in
   `data/session_ctrl/` == P (this is the T25 fix's evidence).
6. Send a message to sid-A; it must be delivered (the resumed CC listens).
Expected: reconnect succeeds with correct cwd, no "No conversation found".
RED → bug T# + fix + re-run L2.

- [ ] **Step 3: Record results as T# entries in `tested&2betest.md` §1**

For each gate: `### T<next#> — <gate result>` with Method (what was driven),
Result (observed counts/evidence), Confidence. If both passed, these results
fold into the T28 gate record in Task 6 (single T28 + per-gate detail lines is
also fine — keep one record per gate result, matching the file's existing
style).

---

### Task 4: Live gate L3 (cross-realm cursors, R2)

**Files:**
- Deploy (outside repo): WSL side project dir (e.g. `~/projects/v2_wsl`)
- Record: `tested&2betest.md` §1

**Interfaces:**
- Consumes: `tools/run_regression.py --tier live` L3 checklist; parity-verified
  v2_wsl tree; WSL environment (`wsl.exe -d Ubuntu`); real CCs on both realms.
- Produces: cross-realm live evidence (per-store cursor independence under
  HP-01/02/03 code); T# entry.

- [ ] **Step 1: Sync v2_wsl to WSL and restart the WSL kernel**

1. Copy the repo's `v2_wsl/cc-communicate` into the WSL project dir
   (`wsl.exe -d Ubuntu -- cp -r ...`; note the WSL-path-mangling workaround:
   wrap the command in `wsl bash -c '...'` with the Windows path quoted, or
   use `//wsl.localhost`/`/mnt/c` paths directly — see the repo's known
   footgun).
2. Verify the deployed tree matches parity (`py -3 tools/check_parity.py`
   stays green — it compares repo trees, so instead diff deployed vs repo:
   `diff -rq -x data -x __pycache__ <repo>/v2_wsl/cc-communicate <wsl-dir>`).
3. Restart the WSL kernel so it runs HP-01/02/03 code: terminate any running
   WSL kernel (or its CC) — the next RPC lazy-starts the new one.

- [ ] **Step 2: Drive the cross-realm cursor scenario**

1. Host CC-A ↔ WSL CC-B: register a conversation pair (both realms' MCP tools
   reachable; the L3 checklist's exact steps).
2. A sends 3 messages to B; B runs `listen_v2(cursors=...)` and must see
   sequences 1..3.
3. B ACKs cursor=2, sends 2 messages back to A.
4. A `listen_v2` with **its own store's** cursor → sees B's 2 messages.
5. A re-runs `listen_v2` with the same cursors → no re-delivery (the archived
   messages must not reappear).
Expected per checklist: each side's cursor map touches only its own store; no
message twice, none lost. RED → bug T# + fix + re-run L3.

- [ ] **Step 3: Record as T# in `tested&2betest.md` §1** (per-side cursor maps
and message counts as evidence). If an interactive WSL step blocks (trust
prompt, etc.), hand off to the user with the exact blocked command.

---

### Task 5: Live gate L4 (multi-collab stress)

**Files:**
- Record: `tested&2betest.md` §1

**Interfaces:**
- Consumes: `--tier live` L4 checklist; real CC spawns (`create_collaborator`);
  `listen_v2` + cursors on all collaborators.
- Produces: stress evidence for batch journal save under load; T# entry.

- [ ] **Step 1: Run the stress scenario**

1. Coordinator CC spawns 3–4 collaborators (one `create_collaborator` each —
   exactly one window per spawn, no error windows: L1 discipline holds here
   too).
2. 5+ rounds: coordinator sends 1 tagged message to each collaborator; each
   collaborator `listen_v2` + ACKs cursor + replies.
3. After the rounds: count message_ids — every sent message received exactly
   once (no loss, no duplicates); `close_connection` cleanly on all.
Expected: zero loss, zero duplicates, clean end. RED → bug T# + fix + re-run L4.

- [ ] **Step 2: Record as T# in `tested&2betest.md` §1** (round/message counts,
duplicate/loss evidence).

---

### Task 6: Wrap-up — T28 gate record + retire stale doc status

**Files:**
- Modify: `tested&2betest.md` (§1: T28 entry; §1 "Potential bugs": PB-1/2/3
  resolution; §2: status banner)

**Interfaces:**
- Consumes: Task 2's captured tier table + Tasks 3–5's T# results.
- Produces: the completed Wave 1 exit gate record; updated doc truth.
  **After this task: GATE: PASS → Wave 2 (HP-07 first) is unlocked.**

- [ ] **Step 1: Add the T28 gate record at the end of §1** (after T27)

Template (fill the Result/Confidence lines from the actual run):

```markdown
### T28 — Wave 1 exit regression gate (scripted tiers + live L1–L4)

- **Method**: `py -3 tools/run_regression.py --tier auto` → T0 syntax / T1
  pytest / T2 parity; then live checklists L1 (spawn-race re-test) / L2
  (reconnect) / L3 (cross-realm cursors) / L4 (multi-collab stress) driven per
  the script's printed checklists; each live gate RED → bug T# + fix + re-run.
- **Result**: T0 `...` / T1 `...` / T2 `...` → GATE PASS (scripted);
  L1 `...` / L2 `...` / L3 `...` / L4 `...` → all pass (per-gate evidence in
  the gate's own T# entries above).
- **Confidence**: high — scripted tiers machine-checked; live gates driven
  with real CCs per checklist.
```

- [ ] **Step 2: Mark PB-1/2/3 resolved in §1 "Potential bugs"**

Replace each `Decision: not fixed ...` line:
- PB-1: → `Resolved by HP-01: filenames carry sequence + message_id, no ts collision possible (covered by tests/unit/test_message_record.py::test_burst_same_ms_no_overwrite).`
- PB-2: → `Resolved by HP-01: ordering is by per-store sequence, never created_at_ms (covered by tests/unit/test_message_record.py::test_clock_backward_still_sequence_ordered).`
- PB-3: → `Resolved by HP-02: per-store cursors replace merged watermarks (covered by tests/unit/test_cursor_ack.py; live gate L3).`

- [ ] **Step 3: Retire §2 with a status banner**

Immediately under the `## §2 To-be-tested (need user / WSL deployment)`
heading, add:

```markdown
> **Status (2026-07-31): all B1–B7 resolved** — each item carries its
> CONFIRMED/DONE update below. Section kept as historical reference; new
> live-gate results are recorded in §1 as T# entries.
```

- [ ] **Step 4: Full suite + parity + gate re-run (final GREEN evidence)**

Run: `py -3 -m pytest -q` → all PASS.
Run: `py -3 tools/check_parity.py` → `PARITY OK`.
Run: `py -3 tools/run_regression.py` → `GATE: PASS`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add tested\&2betest.md
git commit -m "docs(T28): Wave 1 exit regression gate PASS (T0-T2 + live L1-L4)

- T28 gate record with per-tier evidence
- PB-1/2/3 marked resolved by HP-01/02 with covering tests
- retire B1-B7 section to historical reference (all resolved)"
```

---

## Self-review notes (completed before save)

- **Spec coverage**: §2 gate anatomy → Task 1 (script) + Tasks 3–5 (live
  gates); §3 script design → Task 1; §4 live procedures → Task 1 checklists +
  Tasks 3–5 steps; §5 wrap-up → Task 6; §6 execution order → Task 2 → 3–5 → 6;
  §7 out-of-scope respected (no server/scripts changes).
- **Placeholders**: none — every step carries exact commands, code, or
  checklist text; T28's Result lines are deliberately fill-in-after-run (the
  record's content is the run's output, impossible to pre-write).
- **Type consistency**: `syntax_check/pytest_run/parity_run` return
  `(status, detail)` tuples everywhere; `main(argv)` used with explicit `[]`
  in tests; module constants `REPO/TOOLS/WIN/WSL` are the monkeypatch targets
  in both the script and the tests.
