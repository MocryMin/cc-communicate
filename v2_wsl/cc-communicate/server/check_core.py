"""Ensures the cc-communicate kernel is alive before a user function runs.

Every user-function MCP tool calls ensure_core() at entry. This lazily starts
the kernel on first use and re-verifies it on every use. Single instance is
enforced by a file lock on core_status.json (core_plan #11a).

Contract with kernel.py (core_plan #11):
  core_status.json = {"status": 0|1, "pid": int, "start_time": float(epoch)}
  - status=1 means "the kernel recorded itself as running" - NOT a live
    guarantee. Callers must still verify pid+start_time via psutil (#11).
  - On init, kernel.py writes status=1+pid+start_time as the READY signal.
  - On clean exit, kernel.py writes status=0.

Platform: Windows uses DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP; Linux/WSL
uses start_new_session (v2.1 §2.4) - both detach the kernel from the caller so
it survives parent exit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from filelock import FileLock

from paths import CORE_STATUS_FILE, SERVER_DATA_DIR, ensure_runtime_dirs
from proc import proc_start_time

_HANDSHAKE_TIMEOUT = 15.0
_HANDSHAKE_POLL = 0.05

_STATUS_LOCK_FILE = CORE_STATUS_FILE + ".lock"


def _read_status() -> dict | None:
    try:
        with open(CORE_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _is_kernel_alive(st: dict) -> bool:
    if not isinstance(st, dict):
        return False
    pid = st.get("pid")
    recorded = st.get("start_time")
    if not isinstance(pid, int) or recorded is None:
        return False
    current = proc_start_time(pid)
    if current is None:
        return False
    return abs(current - float(recorded)) < 1.0


def _spawn_kernel():
    """Start the kernel as a detached process that survives this tool call."""
    kernel_py = os.path.join(os.path.dirname(__file__), "kernel.py")
    err_log = open(os.path.join(SERVER_DATA_DIR, "kernel.stderr.log"), "ab")
    kwargs = {
        "cwd": SERVER_DATA_DIR,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": err_log,
        "close_fds": True,
    }
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: independent of the parent,
        # no console popup. The kernel can still spawn children (evoke) with
        # their own windows via `cmd /c start`.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        # Linux/WSL: new session = detached, immune to parent terminal SIGHUP.
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, kernel_py], **kwargs)


def _wait_for_ready() -> bool:
    deadline = time.monotonic() + _HANDSHAKE_TIMEOUT
    while time.monotonic() < deadline:
        st = _read_status()
        if st and st.get("status") == 1 and _is_kernel_alive(st):
            return True
        time.sleep(_HANDSHAKE_POLL)
    return False


def ensure_core() -> bool:
    """Make sure the kernel is running and READY. Returns True if alive.

    Lazily starts the kernel on first use (or after it self-exited / crashed).
    Serialized by a file lock so concurrent tool calls don't start multiple
    kernels (#11a)."""
    ensure_runtime_dirs()
    lock = FileLock(_STATUS_LOCK_FILE)
    with lock:
        st = _read_status()
        if st and st.get("status") == 1 and _is_kernel_alive(st):
            return True
        _spawn_kernel()
        return _wait_for_ready()
