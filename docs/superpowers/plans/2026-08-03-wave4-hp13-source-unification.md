# HP-13-A Canonical Source + Generated Artifacts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `v2_win/cc-communicate/` the single canonical source and generate `v2_wsl/cc-communicate/` from it, so a clean checkout can verify both artifacts with one command.

**Architecture:** New repo-tool `tools/build_artifacts.py` mirrors the win tree to the wsl tree (`.mcp.json` substituted from platform templates in `tools/artifact_templates/`) with mirror semantics (stale deletions propagate); `verify` regenerates into a temp dir and byte-compares against the committed tree, plus pins both `.mcp.json`s to their templates. `tools/check_parity.py` gains importable `collect()`/`compare()` helpers that the generator reuses — the file-set rules can never drift. `tools/run_regression.py` tier T2 gains an artifact-verify sub-step and a new L7 live smoke checklist.

**Tech Stack:** Python 3 (stdlib only — no new dependencies), argparse CLI, pytest, git. Windows only for execution (`py -3`); generator output is platform-neutral bytes.

## Global Constraints

- `py -3` for ALL Python on Windows.
- Parity rule (unchanged): `v2_win` ↔ `v2_wsl` byte-identical outside allowlist; `ALLOWLIST = {".mcp.json"}`; excludes `data/`, `__pycache__/`, `.git`, `.pytest_cache`, `node_modules`, `*.pyc`, `*.log`.
- `data/` is runtime state — generator/verify NEVER enumerate, copy, or delete anything under it.
- Generated `v2_wsl` stays COMMITTED (verify has a committed reference; git history kept). NOT gitignored.
- Editing workflow rule (replaces manual dual-tree sync): edit `v2_win/` only → `py -3 tools/build_artifacts.py generate` → commit both trees.
- Repo path has spaces + CJK — quote every path.
- **Invariant**: `generate` on the current tree is a 0-diff against committed `v2_wsl` (proven in Task 4 before anything ships).
- Execution is INLINE (durable user mandate: no context-heavy subagents).
- Spec: `docs/superpowers/specs/2026-08-03-wave4-hp13-source-unification-design.md` (committed `e22866e`).

---

### Task 1: `check_parity.py` — expose importable `collect()` + `compare()`

**Files:**
- Modify: `tools/check_parity.py` (whole file — it is ~68 lines)
- Test: `tests/parity/test_parity.py` (unchanged — must stay green)

**Interfaces:**
- Produces: `collect(root: Path) -> {rel_posix: Path}` (was private `_files`), `compare(win_files: dict, wsl_files: dict, allowlist=ALLOWLIST) -> (problems: list[str], compared: int)`, plus existing constants `REPO/WIN/WSL/ALLOWLIST/EXCLUDE_DIRS/EXCLUDE_SUFFIXES`. `main()` behavior and output byte-identical to today.

- [ ] **Step 1: Refactor `tools/check_parity.py`**

Replace the `_hash`/`_files`/`main` block with extracted helpers. The full new file:

```python
"""HP-13-B: 若 v2_win 与 v2_wsl plugin 源码在 allow-list 之外有差异则失败。

allow-list 只放真正的平台入口（默认仅 .mcp.json）。运行时数据/缓存/VCS 不参与
比对。 ALLOWLIST 的每一项都必须有理由；首次运行若报告其它合法平台文件，把它
们连同理由加入 ALLOWLIST 后再跑绿。
HP-13-A: with tools/build_artifacts.py owning the generation of v2_wsl, a
parity pass means "committed v2_wsl matches what the generator produces" for
all non-allowlisted files; .mcp.json is pinned to the platform templates by
build_artifacts.py verify (closes the allowlist hole)."""
import hashlib
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WIN = REPO / "v2_win" / "cc-communicate"
WSL = REPO / "v2_wsl" / "cc-communicate"

ALLOWLIST = {".mcp.json"}  # 平台 MCP command（win: python / wsl: python3）
EXCLUDE_DIRS = {"data", "__pycache__", ".git", ".pytest_cache", "node_modules"}
EXCLUDE_SUFFIXES = {".pyc", ".log"}


def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def collect(root: Path) -> dict:
    """All files under root, posix-relpath -> Path, applying EXCLUDE_DIRS /
    EXCLUDE_SUFFIXES. Shared with tools/build_artifacts.py - the generator's
    file-set rules can never drift from the parity gate's."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1] in EXCLUDE_SUFFIXES:
                continue
            full = Path(dirpath) / fn
            out[full.relative_to(root).as_posix()] = full
    return out


def compare(win: dict, wsl: dict, allowlist=ALLOWLIST):
    """Diff two collect() maps. Returns (problems, compared) where compared is
    the number of non-allowlisted files in the union."""
    compared = [rel for rel in sorted(set(win) | set(wsl)) if rel not in allowlist]
    problems = []
    for rel in compared:
        if rel not in win:
            problems.append("only in wsl: " + rel)
        elif rel not in wsl:
            problems.append("only in win: " + rel)
        elif _hash(win[rel]) != _hash(wsl[rel]):
            problems.append("differs: " + rel)
    return problems, len(compared)


def main() -> int:
    for name, tree in (("win", WIN), ("wsl", WSL)):
        if not tree.is_dir():
            print("PARITY FAIL: %s tree is not an existing directory: %s" % (name, tree))
            return 1
    problems, compared = compare(collect(WIN), collect(WSL))
    if compared == 0:
        # Vacuous-pass guard: covers empty trees AND trees reduced to only
        # allowlisted files (0 meaningful compares must never print OK).
        print("PARITY FAIL: 0 non-allowlisted files to compare - refusing to "
              "pass having compared nothing (trees: %s, %s)" % (WIN, WSL))
        return 1
    if problems:
        print("PARITY FAIL:")
        for p in problems:
            print("  " + p)
        return 1
    print("PARITY OK (%d files compared, allowlist=%s)" % (compared, sorted(ALLOWLIST)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the parity tests + the real parity gate**

Run: `py -3 -m pytest tests/parity -v` then `py -3 tools/check_parity.py`
Expected: 3 passed; `PARITY OK (32 files compared, allowlist=['.mcp.json'])` (output unchanged from before the refactor)

- [ ] **Step 3: Commit**

```bash
git add tools/check_parity.py
git commit -m "refactor(parity): expose collect()/compare() for the artifact generator (HP-13-A)"
```

---

### Task 2: `tools/artifact_templates/` + `tools/build_artifacts.py` (generate + verify) + unit tests

**Files:**
- Create: `tools/artifact_templates/mcp.win.json` (byte-exact copy of `v2_win/cc-communicate/.mcp.json`)
- Create: `tools/artifact_templates/mcp.wsl.json` (byte-exact copy of `v2_wsl/cc-communicate/.mcp.json`)
- Create: `tools/build_artifacts.py`
- Test: `tests/unit/test_build_artifacts.py`

**Interfaces:**
- Consumes: `check_parity.WIN`, `check_parity.WSL`, `check_parity.collect` (Task 1).
- Produces: CLI `py -3 tools/build_artifacts.py generate|verify`. Module functions `build_wsl_tree(win_root, dst)`, `generate() -> int`, `verify() -> int`, `main(argv) -> int`. Constants `TEMPLATE_DIR/WIN_TEMPLATE/WSL_TEMPLATE` (monkeypatchable in tests).

- [ ] **Step 1: Capture the templates byte-exact**

Run:
```bash
cd "/c/研究生/实习/learn AI/projects/cc-communicate"
mkdir -p tools/artifact_templates
cp v2_win/cc-communicate/.mcp.json tools/artifact_templates/mcp.win.json
cp v2_wsl/cc-communicate/.mcp.json tools/artifact_templates/mcp.wsl.json
cmp v2_win/cc-communicate/.mcp.json tools/artifact_templates/mcp.win.json && echo WIN-OK
cmp v2_wsl/cc-communicate/.mcp.json tools/artifact_templates/mcp.wsl.json && echo WSL-OK
grep -c python tools/artifact_templates/mcp.win.json   # expect: python  (win)
grep -c python3 tools/artifact_templates/mcp.wsl.json  # expect: python3 (wsl)
```
Expected: WIN-OK + WSL-OK; the two templates differ only in `"command"`.

- [ ] **Step 2: Write the failing tests** — `tests/unit/test_build_artifacts.py`

```python
"""HP-13-A generator: tools/build_artifacts.py (spec:
docs/superpowers/specs/2026-08-03-wave4-hp13-source-unification-design.md).

Import pattern per tests/parity/test_parity.py: sys.path.insert(TOOLS), then
monkeypatch build_artifacts.WIN / WSL / TEMPLATE_DIR. Always pass explicit
argv to main()."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"

WIN_MCP = '{"win": true}\n'
WSL_MCP = '{"wsl": true}\n'


def _import():
    sys.path.insert(0, str(TOOLS))
    import build_artifacts
    return build_artifacts


def _trees(tmp_path, ba):
    """win tree: 3 content files + data/ + __pycache__/ + .log, and the wsl
    template dir with fake platform templates."""
    win, wsl = tmp_path / "win", tmp_path / "wsl"
    (win / "server").mkdir(parents=True)
    (win / "server" / "kernel.py").write_text("KERNEL")
    (win / "hooks").mkdir()
    (win / "hooks" / "hooks.json").write_text("HOOKS")
    (win / ".mcp.json").write_text(WIN_MCP)
    (win / "data").mkdir()
    (win / "data" / "secrets.json").write_text("runtime state, never copied")
    (win / "__pycache__").mkdir()
    (win / "__pycache__" / "kernel.cpython-311.pyc").write_bytes(b"\x00\x01")
    (win / "debug.log").write_text("log, never copied")
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "mcp.win.json").write_text(WIN_MCP)
    (tpl / "mcp.wsl.json").write_text(WSL_MCP)
    ba.WIN, ba.WSL, ba.TEMPLATE_DIR = win, wsl, tpl
    return win, wsl


def test_generate_mirrors_substitutes_and_excludes(tmp_path, monkeypatch, capsys):
    ba = _import()
    win, wsl = _trees(tmp_path, ba)
    assert ba.main(["generate"]) == 0
    assert (wsl / "server" / "kernel.py").read_text() == "KERNEL"
    assert (wsl / "hooks" / "hooks.json").read_text() == "HOOKS"
    assert (wsl / ".mcp.json").read_text() == WSL_MCP      # substituted
    assert not (wsl / "data").exists()                      # runtime state untouched
    assert not (wsl / "__pycache__").exists()
    assert not (wsl / "debug.log").exists()
    assert "GENERATED" in capsys.readouterr().out


def test_generate_removes_stale_files(tmp_path, monkeypatch):
    ba = _import()
    win, wsl = _trees(tmp_path, ba)
    wsl.mkdir()                                             # target pre-exists
    (wsl / "old.py").write_text("stale")
    (wsl / "data").mkdir()                                  # runtime state stays
    (wsl / "data" / "keep.json").write_text("keep me")
    assert ba.main(["generate"]) == 0
    assert not (wsl / "old.py").exists()
    assert (wsl / "data" / "keep.json").read_text() == "keep me"


def test_verify_passes_on_the_real_committed_tree(monkeypatch, capsys):
    ba = _import()
    out = capsys.readouterr().out
    assert ba.main(["verify"]) == 0, out
    assert "ARTIFACTS OK" in capsys.readouterr().out


def test_verify_fails_on_mutated_committed_file(tmp_path, monkeypatch, capsys):
    ba = _import()
    _trees(tmp_path, ba)
    ba.main(["generate"])
    (ba.WSL / "server" / "kernel.py").write_text("TAMPERED")
    assert ba.main(["verify"]) == 1
    assert "differs: server/kernel.py" in capsys.readouterr().out


def test_verify_fails_on_stale_committed_file(tmp_path, monkeypatch, capsys):
    ba = _import()
    _trees(tmp_path, ba)
    ba.WSL.mkdir()
    (ba.WSL / "extra.py").write_text("stale")               # generator never makes it
    assert ba.main(["verify"]) == 1
    assert "stale in committed" in capsys.readouterr().out


def test_verify_fails_on_missing_committed_file(tmp_path, monkeypatch, capsys):
    ba = _import()
    _trees(tmp_path, ba)
    ba.main(["generate"])
    (ba.WSL / "hooks" / "hooks.json").unlink()              # committed tree lost one
    assert ba.main(["verify"]) == 1
    assert "missing in committed" in capsys.readouterr().out


def test_verify_fails_on_wsl_mcp_deviation(tmp_path, monkeypatch, capsys):
    ba = _import()
    _trees(tmp_path, ba)
    ba.main(["generate"])
    (ba.WSL / ".mcp.json").write_text('{"wsl": "tampered"}\n')
    assert ba.main(["verify"]) == 1
    assert "differs: .mcp.json" in capsys.readouterr().out


def test_verify_fails_on_win_mcp_deviation(tmp_path, monkeypatch, capsys):
    """The canonical tree's own .mcp.json must equal the win template - parity
    cannot see it (allowlisted), verify closes that hole."""
    ba = _import()
    _trees(tmp_path, ba)
    (ba.WIN / ".mcp.json").write_text('{"win": "tampered"}\n')
    assert ba.main(["verify"]) == 1
    assert "mcp.win.json" in capsys.readouterr().out


def test_verify_vacuous_guard(tmp_path, monkeypatch, capsys):
    ba = _import()
    win, wsl = _trees(tmp_path, ba)
    (win / "server" / "kernel.py").unlink()
    (win / "server").rmdir()
    (win / "hooks" / "hooks.json").unlink()
    (win / "hooks").rmdir()
    (win / ".mcp.json").unlink()                            # win content now empty
    assert ba.main(["verify"]) == 1
    assert "0 files compared" in capsys.readouterr().out
```

- [ ] **Step 3: Run to verify they fail**

Run: `py -3 -m pytest tests/unit/test_build_artifacts.py -v`
Expected: FAIL (ModuleNotFoundError: build_artifacts)

- [ ] **Step 4: Write `tools/build_artifacts.py`**

```python
"""HP-13-A: canonical v2_win source -> generated v2_wsl artifact (design:
docs/superpowers/specs/2026-08-03-wave4-hp13-source-unification-design.md).

v2_win/cc-communicate/ is the single committed source of truth. This tool
generates v2_wsl/cc-communicate/ from it (byte-for-byte mirror, .mcp.json
substituted from the platform templates in tools/artifact_templates/) and
verifies that the committed artifacts match the generator output.

Usage:
  py -3 tools/build_artifacts.py generate   # rewrite v2_wsl from v2_win
  py -3 tools/build_artifacts.py verify     # gate: committed trees == generated

Workflow rule: edit v2_win/ only -> generate -> commit both trees.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# Same file-set rules as the parity gate - never drift.
from check_parity import WIN, WSL, collect

TEMPLATE_DIR = Path(__file__).resolve().parent / "artifact_templates"
WIN_TEMPLATE = TEMPLATE_DIR / "mcp.win.json"
WSL_TEMPLATE = TEMPLATE_DIR / "mcp.wsl.json"


def build_wsl_tree(win_root: Path, dst: Path) -> None:
    """Mirror win_root into dst (both are cc-communicate/ plugin roots).

    Every file collected by the parity walk is copied byte-for-byte except
    .mcp.json, which is written from the WSL template. data/ is never
    enumerated (it is excluded by collect()) so it is never touched."""
    for rel, src in collect(win_root).items():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel == ".mcp.json":
            target.write_bytes(WSL_TEMPLATE.read_bytes())
        else:
            target.write_bytes(src.read_bytes())


def generate() -> int:
    build_wsl_tree(WIN, WSL)
    # Mirror semantics: files present in v2_wsl but absent from v2_win are
    # stale (deletions must propagate). .mcp.json is always in win, so a
    # stale removal implies a real deletion.
    win_rels = set(collect(WIN))
    for rel in collect(WSL):
        if rel not in win_rels:
            (WSL / rel).unlink()
    print("GENERATED v2_wsl/cc-communicate (%d files)" % len(win_rels))
    return 0


def verify() -> int:
    problems = []
    # 1. The canonical tree's own .mcp.json must be the win template (closes
    #    the parity allowlist hole - .mcp.json legitimately differs per tree).
    win_mcp = WIN / ".mcp.json"
    if not win_mcp.is_file():
        problems.append("missing v2_win/cc-communicate/.mcp.json")
    elif win_mcp.read_bytes() != WIN_TEMPLATE.read_bytes():
        problems.append("v2_win/cc-communicate/.mcp.json != mcp.win.json")
    # 2. Regenerate the expected WSL tree into a temp dir (pure computation;
    #    zero writes to the repo) and byte-compare with the committed tree.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "wsl"
        build_wsl_tree(WIN, tmp)
        committed, generated = collect(WSL), collect(tmp)
        compared = 0
        for rel in sorted(set(committed) | set(generated)):
            compared += 1
            if rel not in generated:
                problems.append("stale in committed v2_wsl: " + rel)
            elif rel not in committed:
                problems.append("missing in committed v2_wsl: " + rel)
            elif committed[rel].read_bytes() != generated[rel].read_bytes():
                problems.append("differs: " + rel)
        if not problems and compared == 0:
            problems.append("0 files compared - refusing to pass")
    if problems:
        print("ARTIFACTS FAIL:")
        for p in problems:
            print("  " + p)
        return 1
    print("ARTIFACTS OK (%d files compared, templates pinned)" % compared)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("generate", "verify"))
    args = ap.parse_args(argv)
    return generate() if args.command == "generate" else verify()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the new tests — all pass**

Run: `py -3 -m pytest tests/unit/test_build_artifacts.py -v`
Expected: 9 passed. (Note `test_verify_passes_on_the_real_committed_tree` passes only because the committed `v2_wsl` is in sync — the invariant.)

- [ ] **Step 6: Commit**

```bash
git add tools/artifact_templates tools/build_artifacts.py tests/unit/test_build_artifacts.py
git commit -m "feat(tools): build_artifacts.py - generate/verify v2_wsl from canonical v2_win (HP-13-A)"
```

---

### Task 3: `run_regression.py` — T2 artifact sub-step + L7 live checklist + test updates

**Files:**
- Modify: `tools/run_regression.py` (docstring + `parity_run` block area + `LIVE_CHECKLISTS` + `results` list)
- Modify: `tests/unit/test_run_regression.py`
- Test: `tests/unit/test_run_regression.py`

**Interfaces:**
- Consumes: `tools/build_artifacts.py verify` (Task 2) — shelled out like `check_parity.py`.
- Produces: T2 output `PARITY OK (32 files)` + `ARTIFACTS OK (33 files)`; LIVE_CHECKLISTS gains `L7`; gate stays all-green-only.

- [ ] **Step 1: Update the docstring + add `artifact_run()` + extend `results`**

In `tools/run_regression.py`:

1. Docstring lines 5–13 → replace with:

```
  auto (default): run the scripted tiers T0 (syntax) / T1 (pytest) /
    T2 (parity + HP-13-A artifact verify), print a per-tier table,
    exit 0 iff all PASS (GATE: PASS).
  live: print the L1-L7 live-gate checklists - informational only, exit 0.
  all: auto tiers first, then the live checklists.

The gate is GREEN only when all tiers (T0-T2 checks + L1-L7) pass. A RED
tier means fix + retest before the wave transition it guards.
```

2. The `LIVE_CHECKLISTS` comment at line 31–32 → keep, but append a new tuple (after the L6 entry, before the closing `]`):

```python
    ("L7 - Wave-4 smoke: live behavior unchanged (HP-13-A)", """\
  Why:      HP-13-A reorganized artifact production, not protocol; this smoke
            gate proves the deliverable's core claim: install entry + live
            behavior unchanged, cross-realm install path untouched
  Prereq:   parity + artifacts green; WSL peer registered (L3-style)
  Steps:    spawn ONE collaborator via repo v2_win (spawn_collaborator) ->
            send 1 message -> worker acks -> check_alive == 1 ->
            check_alive(WSL peer) == 1 -> one cross-realm probe message
  Expected: spawn/ack works through the canonical tree; WSL peer alive and
            responds (cross-realm path intact)
  Pass:     send+ack ok AND WSL peer alive with a routed reply
  Record:   T# with ack evidence + peer reply"""),
```

3. After `parity_run()` (line 175), add:

```python
def artifact_run():
    """T2 second sub-step (HP-13-A): committed artifacts must equal what
    tools/build_artifacts.py generate would produce."""
    r = _run([sys.executable, str(TOOLS / "build_artifacts.py"), "verify"])
    if r.returncode:
        return RED, (r.stdout.strip() or r.stderr.strip()).splitlines()[-1]
    return PASS, r.stdout.strip().splitlines()[-1]
```

4. In `main()`, the results list → add the sub-step:

```python
    results = [("T0 syntax", syntax_check()),
               ("T1 pytest", pytest_run()),
               ("T2 parity", parity_run()),
               ("T2 artifacts", artifact_run())]
```

- [ ] **Step 2: Update `tests/unit/test_run_regression.py`**

1. Every test that stubs `parity_run` must also stub `artifact_run` (else the real subprocess runs against the repo): `test_tree_without_server_py_is_red`, `test_gate_red_when_any_tier_red`, `test_gate_pass_only_when_all_green`, `test_all_tier_runs_auto_then_prints_live` — add after each `parity_run` stub:

```python
    monkeypatch.setattr(rr, "artifact_run", lambda: (rr.PASS, "ok"))
```

2. `test_live_tier_prints_all_checklists_exits_0` → header loop gains L7:

```python
    for hdr in ("L1", "L2", "L3", "L4", "L5", "L6", "L7"):
```

3. New test (append at the end of the file):

```python
def test_artifact_tier_red_fails_gate(monkeypatch, capsys):
    rr = _import()
    monkeypatch.setattr(rr, "syntax_check", lambda: (rr.PASS, "ok"))
    monkeypatch.setattr(rr, "pytest_run", lambda: (rr.PASS, "ok"))
    monkeypatch.setattr(rr, "parity_run", lambda: (rr.PASS, "ok"))
    monkeypatch.setattr(rr, "artifact_run", lambda: (rr.RED, "ARTIFACTS FAIL"))
    code, out = _run(rr.main, ["--tier", "auto"], capsys)
    assert code == 1
    assert "T2 artifacts" in out and "RED" in out
```

- [ ] **Step 3: Run the regression-suite tests**

Run: `py -3 -m pytest tests/unit/test_run_regression.py tests/parity tests/unit/test_build_artifacts.py -v`
Expected: all pass (7 run_regression incl. the new red-tier test + 3 parity + 9 build_artifacts = 19).

- [ ] **Step 4: Commit**

```bash
git add tools/run_regression.py tests/unit/test_run_regression.py
git commit -m "feat(gate): T2 artifact-verify sub-step + L7 live smoke checklist (HP-13-A)"
```

---

### Task 4: Wave-4 exit — invariant proof, full auto gate, live smoke gate, records

**Files:**
- Modify: `tested&2betest.md` (T49 record; §1)
- Modify: `.superpowers/wave4-recovery.md` (conventions §3 — supersede the dual-tree sync rule with the generate rule)
- Records only — no source code.

- [ ] **Step 1: Prove the 0-diff invariant on the real tree**

Run:
```bash
cd "/c/研究生/实习/learn AI/projects/cc-communicate"
py -3 tools/build_artifacts.py generate
git diff --stat v2_wsl
```
Expected: `GENERATED v2_wsl/cc-communicate (33 files)`; `git diff` empty (generation changed nothing — the committed artifact already matches).

- [ ] **Step 2: Full auto gate**

Run: `py -3 tools/run_regression.py`
Expected: T0 syntax PASS, T1 pytest PASS (193 + 10 new = 203), T2 parity PASS (32), T2 artifacts PASS (33), `GATE: PASS`.

- [ ] **Step 3: LF pinning (CRLF hazard, Task-2 fix-round finding)**

This repo runs `core.autocrlf=true` with LF blobs: a `git checkout`/restore of a v2_* file writes CRLF to the working copy, which is byte-different from the LF canonical — `verify()`/parity correctly FAIL while `git diff` (eol-normalizing) shows nothing. Pin the byte-fidelity surface to LF so any checkout state is gate-green:

Create `.gitattributes` at the repo root:

```
# HP-13-A: artifact/parity gates hash raw bytes; v2 trees + templates must
# stay LF in the working copy even under core.autocrlf=true.
v2_win/** text eol=lf
v2_wsl/** text eol=lf
tools/artifact_templates/** text eol=lf
```

Then run: `git add --renormalize . && git status --short` — expected: no file changes (blobs and working copies are already LF; renormalize is a no-op). Commit:

```bash
git add .gitattributes
git commit -m "build(git): pin v2 trees + artifact templates to LF (CRLF hazard, HP-13-A)
Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 4: Live smoke gate (drive L7) — real CC + WSL peer**

Per the L7 checklist: script-import coordinator (synthetic sid, real data root v2_win/cc-communicate/data) → `spawn_collaborator` one worker via the repo v2_win → send 1 message → worker acks → `check_alive` == 1 → `check_alive`(WSL peer 4cefe529) == 1 → one cross-realm probe message → routed reply.
Expected: send+ack OK; WSL peer alive with a routed reply. Record evidence in T49.

- [ ] **Step 5: Records + docs**

1. `tested&2betest.md` §1 — append:

```markdown
### T49 — Wave 4 acceptance: HP-13-A canonical source + generated artifacts (auto + live smoke gates)

- **Auto gate**: T0 syntax / T1 pytest (N tests) / T2 parity (32) / T2 artifacts (33) GATE PASS.
- **Invariant**: `build_artifacts.py generate` on the canonical tree produced 0 diff vs committed v2_wsl.
- **Live smoke (L7)**: spawn+send+ack through repo v2_win OK; WSL peer check_alive 1; cross-realm probe routed reply OK.
- **Confidence**: high — real CC, real WSL peer, real store records.
```

2. `.superpowers/wave4-recovery.md` §3 — replace the parity-sync bullet with: "Edit `v2_win/` only → `py -3 tools/build_artifacts.py generate` → commit both trees (HP-13-A); forgetting surfaces as red T1/T2."
3. Memory `hardening-program-status.md` — Wave 4 COMPLETE + status update.

- [ ] **Step 6: Commit + push (user approval), then review package**

```bash
git add tested\&2betest.md
git commit -m "docs(W4): T49 - HP-13-A acceptance (auto + live smoke gates)
Co-Authored-By: Claude <noreply@anthropic.com>"
```
Push after user approval; then prepare the Wave-4 review brief for kimi-k3.

---

## Self-Review

**Spec coverage:**
- §1.1 canonical tree → Tasks 1/2/4 (invariant proof)
- §1.2 generate (mirror/substitute/stale) → Task 2
- §1.2 verify (temp-regen, full compare incl. .mcp.json, win pin) → Task 2
- §1.3 templates → Task 2 Step 1
- §1.4 check_parity refactor + docstring note → Task 1
- §2 gate integration (T2 sub-step, red-on-forget) → Task 3
- §3 tests (all 4 test groups) → Task 2 (9 tests) + Task 3 (test updates)
- §5 exit gates (auto + live smoke) → Task 4
- §6 records (T49) → Task 4

**Placeholder scan:** no TBD/TODO; every code step has full code; test counts are computed at run time where the exact value could drift (pytest total in Task 4 Step 2 says 205 = 193 + 12 new; verify against the actual run — the plan's T49 record uses "N tests" for the exact count).
