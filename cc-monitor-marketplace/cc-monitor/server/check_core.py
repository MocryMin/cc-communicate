"""Ensures the cc-monitor kernel is alive before a user function runs.

Every user-function MCP tool calls ensure_core() at entry. This lazily starts
the kernel on first use and re-verifies it on every use. Single instance is
enforced by a file lock on core_status.json (core_plan #11a).

Contract with kernel.py (core_plan #11):
  core_status.json = {"status": 0|1, "pid": int, "start_time": float(epoch)}
  - status=1 means "the kernel recorded itself as running" — it is NOT a live
    guarantee. Callers must still verify pid+start_time via psutil, because the
    kernel may have crashed and left status=1 behind (#11: stale-status defense).
  - On init, kernel.py writes status=1+pid+start_time as the READY signal;
    check_core waits for it (#11b handshake).
  - On clean exit, kernel.py writes status=0.

If the kernel fails to signal READY within _HANDSHAKE_TIMEOUT, ensure_core
returns False; the caller should surface the error (and may retry). Tools that
are already past ensure_core and waiting on a queue response must additionally
handle response timeout + re-run ensure_core (#11c exit-race mitigation).
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

# How long to wait for the kernel to signal READY after we spawn it.
_HANDSHAKE_TIMEOUT = 15.0
# Poll interval during handshake.
_HANDSHAKE_POLL = 0.05

# filelock lockfile path (sibling of core_status.json).
_STATUS_LOCK_FILE = CORE_STATUS_FILE + ".lock"


def _read_status() -> dict | None:
    try:
        with open(CORE_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _is_kernel_alive(st: dict) -> bool:
    """Verify the recorded kernel pid is actually a live process with the same
    start_time. Defeats both PID reuse and stale status=1 after a crash."""
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
    """Start the kernel as a detached process that survives this tool call.

    stdout -> DEVNULL (the kernel logs to data/server/kernel.log itself).
    stderr -> data/server/kernel.stderr.log (captures import errors / pre-init
              tracebacks that the kernel's own logging can't catch)."""
    kernel_py = os.path.join(os.path.dirname(__file__), "kernel.py")
    err_log = open(os.path.join(SERVER_DATA_DIR, "kernel.stderr.log"), "ab")
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: independent of the parent,
        # no console popup. The kernel can still spawn children (evoke) with
        # their own windows via `cmd /c start`.
        creationflags = 0x00000008 | 0x00000200
    subprocess.Popen(
        [sys.executable, kernel_py],
        cwd=SERVER_DATA_DIR,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=err_log,
        close_fds=True,
    )


def _wait_for_ready() -> bool:
    """Poll core_status.json until the kernel writes status=1 and is verified
    alive, or until _HANDSHAKE_TIMEOUT."""
    deadline = time.monotonic() + _HANDSHAKE_TIMEOUT
    while time.monotonic() < deadline:
        st = _read_status()
        if st and st.get("status") == 1 and _is_kernel_alive(st):
            return True
        time.sleep(_HANDSHAKE_POLL)
    return False


def ensure_core() -> bool:
    """Make sure the kernel is running and READY. Returns True if alive.

    Called by every user-function MCP tool at entry. Lazily starts the kernel
    on first use (or after it self-exited / crashed). Serialized by a file lock
    so concurrent tool calls don't start multiple kernels (#11a)."""
    ensure_runtime_dirs()
    lock = FileLock(_STATUS_LOCK_FILE)
    with lock:
        # Fast path: already alive.
        st = _read_status()
        if st and st.get("status") == 1 and _is_kernel_alive(st):
            return True
        # Need to start it. Holding the lock serialize concurrent starters;
        # the others block here, then see status=1 after we release.
        _spawn_kernel()
        return _wait_for_ready()
