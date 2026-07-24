import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"


def test_win_wsl_parity():
    r = subprocess.run([sys.executable, str(TOOLS / "check_parity.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, "parity gate failed:\n" + r.stdout + r.stderr
    # a pass that compared 0 files is a vacuous pass and must fail
    assert re.search(rb"PARITY OK \([1-9][0-9]* files compared", r.stdout.encode()), \
        "parity gate passed having compared 0 files:\n" + r.stdout


def test_parity_fails_on_empty_trees(tmp_path, monkeypatch):
    sys.path.insert(0, str(TOOLS))
    import check_parity
    win, wsl = tmp_path / "win", tmp_path / "wsl"
    win.mkdir()
    wsl.mkdir()
    monkeypatch.setattr(check_parity, "WIN", win)
    monkeypatch.setattr(check_parity, "WSL", wsl)
    assert check_parity.main() == 1
