"""Process introspection for the cc-communicate upper layer.

Frozen-equivalent of the parts of scripts/lib/proc.js that the upper layer
uses. The upper layer reads events that already carry pid + start_time (for
liveness), AND resolves the claude ancestor (for my_session_id — the MCP
server walks up from its own pid to find the calling CC's claude.exe). Both
needs are covered here via psutil.

Uses psutil (cross-platform). On Windows, psutil's create_time() reads the same
Win32 FILETIME that proc.js's CIM branch reads, so epoch values agree across
the two layers for the same process.

start_time appears in two forms in this system:
  - Event files (written by registrar.js / proc.js): ISO8601 string.
  - core_status.json + alive_sessions snapshot (written by this layer): epoch float.
Use parse_start_time() to bring the ISO form to epoch before comparing.
"""
from __future__ import annotations

import re
from datetime import datetime

import psutil

# .NET CIM 'o' format can emit 7 fractional digits; Python <3.11 fromisoformat
# accepts at most 6. Truncate the excess before parsing.
_FRACT_TRAIL = re.compile(r"(\.\d{6})\d+")


def live_procs() -> dict[int, float]:
    """Map pid -> create_time (epoch seconds) for every live process.

    Returns {} on any failure — callers treat 'pid not in map' as dead, which
    is the safe (fail-closed) outcome when the process table can't be read."""
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
    """create_time (epoch seconds) for a single pid, or None if the pid is not
    live or cannot be read. Cheaper than live_procs() for one-pid checks
    (used by check_alive)."""
    try:
        return float(psutil.Process(pid).create_time())
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied:
        # Process exists but we can't read its start time — cannot verify,
        # so fail closed (treat as not-alive).
        return None
    except psutil.Error:
        return None


def parse_start_time(s) -> float | None:
    """Parse an ISO8601 start_time string (as written by registrar.js / proc.js
    CIM 'o' format, e.g. '2026-07-03T12:34:56.7890000-07:00') to epoch seconds.

    Returns None if unparseable. Callers must treat None as 'cannot verify' and
    fail closed (return not-alive), never as 'definitely alive'."""
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
    """Walk up the process tree from self_pid to find the claude.exe ancestor.
    Returns (pid, start_time_epoch) or (None, None).

    Used by my_session_id: the MCP server is a child of claude.exe, so walking
    up from the MCP server's pid finds the calling CC's pid, which is then
    looked up in sessions to get the session_id. Upper-layer equivalent of
    proc.js's resolveClaude (frozen lower layer)."""
    try:
        p = psutil.Process(self_pid)
        for parent in p.parents():
            try:
                name = (parent.name() or "").lower()
                cmdline = " ".join(parent.cmdline() or [])
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
            if "claude" not in name and "claude" not in cmdline.lower():
                continue
            # Skip our own scripts (a spawned child's cmdline might reference
            # claude, e.g. `claude --resume ...`) — match the real claude binary.
            low = cmdline.lower()
            if ("cc-communicate" in low or "registrar" in low or "mcp_server" in low
                    or "listen_poller" in low or "kernel.py" in low):
                continue
            return parent.pid, parent.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return None, None
