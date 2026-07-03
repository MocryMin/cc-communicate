"""Process introspection for the cc-monitor upper layer.

Frozen-equivalent of the parts of scripts/lib/proc.js that the upper layer
actually uses. proc.js also has resolveClaude() / isClaudeCmd() / getProcTable()
for the *lower* layer — registrar.js walks the hook process tree to find the
claude ancestor when writing start events. The upper layer does NOT resolve
claude ancestors: it reads events that already carry pid + start_time, and only
needs to verify liveness. Those lower-layer functions are therefore not ported.

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
