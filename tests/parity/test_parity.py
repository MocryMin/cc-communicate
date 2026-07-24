import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_win_wsl_parity():
    r = subprocess.run([sys.executable, str(REPO / "tools" / "check_parity.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, "parity gate failed:\n" + r.stdout + r.stderr
