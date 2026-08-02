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
