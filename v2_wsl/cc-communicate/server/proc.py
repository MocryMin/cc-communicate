"""Process introspection for the cc-communicate upper layer.

Frozen-equivalent of the parts of scripts/lib/proc.js that the upper layer
uses. The upper layer reads events that already carry pid + start_time (for
liveness), AND resolves the claude ancestor (for my_session_id - the MCP
server walks up from its own pid to find the calling CC's claude binary).

Uses psutil (cross-platform). On Windows, psutil's create_time() reads the same
Win32 FILETIME that proc.js's CIM branch reads, so epoch values agree across
the two layers for the same process.
"""
from __future__ import annotations

import re
from datetime import datetime

import psutil

_FRACT_TRAIL = re.compile(r"(\.\d{6})\d+")


def live_procs() -> dict[int, float]:
    out: dict[int, float] = {}
    try:
        for p in psutil.process_iter(["pid", "create_time"]):
            ct = p.info.get("create_time")
            if ct is not None:
                out[p.info["pid"]] = float(ct)
    except psutil.Error:
        pass
    return out


def proc_start_time(pid: int) -> float | None:
    try:
        return float(psutil.Process(pid).create_time())
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied:
        return None
    except psutil.Error:
        return None


def parse_start_time(s) -> float | None:
    if not s:
        return None
    try:
        t = s.strip()
        t = _FRACT_TRAIL.sub(lambda m: m.group(1), t, count=1)
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        return datetime.fromisoformat(t).timestamp()
    except (ValueError, TypeError):
        return None


def resolve_claude(self_pid: int):
    """Walk up the process tree from self_pid to find the claude binary ancestor.
    Returns (pid, start_time_epoch) or (None, None).

    Identification is by process NAME (claude / claude.exe), NOT by cmdline
    substring. v0.1 matched "claude" in cmdline and skipped cmdlines containing
    "cc-communicate" - but the spawn/evoke prompts literally contain
    "cc-communicate", so a spawned CC's claude parent was rejected and
    my_session_id failed (v2.2 Amd1 / BUG-1). Our own scripts run as python/node
    (name python/node); intermediate shells are cmd/bash/tmux - none named
    claude - so a name check cleanly distinguishes the real claude binary from
    processes whose cmdline merely references claude (e.g. the prompt text)."""
    try:
        p = psutil.Process(self_pid)
        for parent in p.parents():
            try:
                name = (parent.name() or "").lower()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
            if name not in ("claude", "claude.exe"):
                continue
            return parent.pid, parent.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return None, None


def claude_binary_path(self_pid: int):
    """Absolute path to the claude binary ancestor's executable, or None.

    On WSL `which claude` returns the Windows version (C13); the kernel detects
    its own claude ancestor's exe() at init so spawn.py uses the full Linux
    path. On Windows this is unnecessary (claude is on PATH)."""
    pid, _ = resolve_claude(self_pid)
    if pid is None:
        return None
    try:
        return psutil.Process(pid).exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
        return None
