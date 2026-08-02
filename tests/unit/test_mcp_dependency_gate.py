"""AR-01: the MCP dependency declaration must pin major 1, and the import
path mcp_server.py uses must resolve in a fresh interpreter.

A fresh `pip install -r server/requirements.txt` resolves mcp to the newest
allowed version. Before AR-01 the declaration was `mcp>=1.28` (no upper
bound) -> a clean environment got MCP 2.0.0, where `mcp.server.fastmcp` (the
exact import in mcp_server.py) no longer exists -> the server could not even
import. These tests lock the declaration AND prove the import path resolves
under the pinned constraint (the reviewer's clean-install/import gate).

Also N-03: the dev-dependency entry (pytest) must exist at the repo root so
the gate's T1 tier is reproducible from a clean checkout.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REQ = REPO / "v2_win" / "cc-communicate" / "server" / "requirements.txt"


def _mcp_line() -> str:
    """The requirements.txt line declaring the mcp dependency."""
    req = REQ.read_text(encoding="utf-8")
    for line in req.splitlines():
        core = line.split("#")[0].strip()
        if core.startswith("mcp"):
            return core
    raise AssertionError("no mcp line in %s" % REQ)


def test_requirements_pins_mcp_major_1():
    """AR-01: the declaration must cap the major version below 2 - an
    unbounded pin silently installs MCP 2.x on a fresh environment, which
    breaks the server's `from mcp.server.fastmcp import FastMCP` import."""
    assert _mcp_line() == "mcp>=1.28,<2"


def test_mcp_fastmcp_imports_in_fresh_interpreter():
    """The exact import mcp_server.py makes resolves under the pinned
    constraint, in a pristine interpreter (no repo sys.path leakage)."""
    code = ("import importlib.metadata as md;"
            "v = md.version('mcp');"
            "assert v.startswith('1.'), 'installed mcp %s outside pin' % v;"
            "from mcp.server.fastmcp import FastMCP;"
            "print('ok mcp', v)")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, "-c", code],
                       cwd=str(REPO / "tools"), capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert r.stdout.startswith("ok mcp 1.")


def test_requirements_dev_declares_pytest():
    """N-03: the dev-dependency entry exists at the repo root and declares
    the gate's T1 tool (pytest), so `pip install -r requirements-dev.txt`
    makes the one-command gate reproducible from a clean checkout."""
    dev = REPO / "requirements-dev.txt"
    assert dev.is_file(), "missing requirements-dev.txt at repo root"
    text = dev.read_text(encoding="utf-8")
    assert any(line.split("#")[0].strip().startswith("pytest")
               for line in text.splitlines()), \
        "requirements-dev.txt must declare pytest"
