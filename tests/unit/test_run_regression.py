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
    # Keep the REAL syntax_check (the vacuous-pass guard is a T0 concern; with
    # tmp trees it spawns no subprocesses) but stub the other two tiers - a real
    # pytest_run would re-collect THIS test file and recurse infinitely.
    monkeypatch.setattr(rr, "pytest_run", lambda: (rr.PASS, "ok"))
    monkeypatch.setattr(rr, "parity_run", lambda: (rr.PASS, "ok"))
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
