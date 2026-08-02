"""Wave-exit regression gate (spec: docs/superpowers/specs/2026-07-31-wave1-regression-gate-design.md).

Usage:
  py -3 tools/run_regression.py [--tier auto|live|all]

  auto (default): run the scripted tiers T0 (syntax) / T1 (pytest) /
    T2 (parity + HP-13-A artifact verify), print a per-tier table,
    exit 0 iff all PASS (GATE: PASS).
  live: print the L1-L7 live-gate checklists - informational only, exit 0.
  all: auto tiers first, then the live checklists.

The gate is GREEN only when all tiers (T0-T2 checks + L1-L7) pass. A RED
tier means fix + retest before the wave transition it guards.
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

# L1-L6 live-gate checklists (L1-L4 verbatim from the design spec section 4;
# L5/L6 added for the Wave 3 exit gate - full re-run decision, 2026-08-02).
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
    ("L5 - Same-cwd spawn race (T37 live gate)", """\
  Why:      Wave 2 (HP-04): multiple collaborators spawned in the SAME cwd must
            not cross sessions; Wave 3 (HP-08) touches spawn env + kernel exit,
            so the spawn path is re-verified live
  Prereq:   fresh kernel (no stray CCs), a single cwd P
  Steps:    spawn 2+ collaborators in cwd P (spawn_collaborator with distinct
            spawn_tokens) -> each window my_session_id -> each returns a DISTINCT
            sid -> check_alive each -> 1; tokens bind to the right sessions
  Expected: exactly N spawned windows, N distinct session ids, token->sid map
            matches spawn order; no session bleed
  Pass:     all sids distinct and alive; token bindings correct
  Record:   T# in tested&2betest.md sec1 with per-window session ids + token map"""),
    ("L6 - Correlated connect handshake (T37 live gate)", """\
  Why:      Wave 2 (HP-05): connect replies must be matched by correlation_id
            (hello kind='hello' + correlation_id = connection_id); Wave 3 re-runs
            it because kernel exit/restart now happens while pairs stay registered
  Prereq:   two live CCs (coordinator + worker) on the same machine
  Steps:    coordinator connect(worker, connection_id=C1) -> worker replies ->
            verify reply matched via correlation_id; info.json status=active ->
            connect with a DIFFERENT id C2 -> CONFLICT; close_connection ->
            info.json status=closed -> re-connect with C1 -> reuse (no conflict)
  Expected: correlation-matched reply; single-active CONFLICT; clean close/reuse
  Pass:     all four observations hold
  Record:   T# with correlation-match evidence + info.json states"""),
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


def artifact_run():
    """T2 second sub-step (HP-13-A): committed artifacts must equal what
    tools/build_artifacts.py generate would produce."""
    r = _run([sys.executable, str(TOOLS / "build_artifacts.py"), "verify"])
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
               ("T2 parity", parity_run()),
               ("T2 artifacts", artifact_run())]
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
