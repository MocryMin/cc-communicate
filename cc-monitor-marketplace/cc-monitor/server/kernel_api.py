"""Kernel functions callable via the queue RPC (core_plan "内核函数").

These operate on kernel state passed explicitly as parameters (no module
globals) so each function's state access is visible at the call site and the
functions are easy to test in isolation. kernel.py's _dispatch() routes RPC
requests here.

Implemented (read-only):
  - query_session(sessions, session_id)
  - check_alive(alive_sessions, session_id)

TODO (later increments): evoke, withdraw, and the conversation functions.
"""
from __future__ import annotations

from proc import proc_start_time


def query_session(sessions: dict, session_id: str):
    """Return the session_inf dict for session_id, or None if unknown.
    (core_plan "内核函数 3": returns the record, or 0/None if absent.)"""
    return sessions.get(session_id)


def check_alive(alive_sessions: dict, session_id: str) -> int:
    """Is the session truly alive? Returns 1 (alive) or 0 (not alive).

    Four-step check (core_plan "内核函数 4" + technical challenge #3):
      1. session_id in alive_sessions? If not -> 0.
      2. Is its pid still a live OS process? If not, drop the record -> 0.
      3. Does the pid's current start_time match the recorded one? If not
         (PID reuse), drop the record -> 0.
      4. All pass -> 1.

    'Drop the record' mutates alive_sessions in place (the dict is passed by
    reference from the kernel)."""
    info = alive_sessions.get(session_id)
    if not info:
        return 0
    pid = info.get("pid")
    recorded = info.get("start_time")
    if pid is None or recorded is None:
        return 0
    current = proc_start_time(pid)
    if current is None:
        alive_sessions.pop(session_id, None)  # process gone — stale record
        return 0
    if abs(current - float(recorded)) > 1.0:
        alive_sessions.pop(session_id, None)  # PID reuse — stale record
        return 0
    return 1
