"""Platform-specific CC process spawning for evoke / create_collaborator.

Two spawn modes:
  - spawn_cc_new(cwd, prompt): start a NEW interactive CC in cwd. For
    create_collaborator. `claude --cwd <dir> <prompt>` (no -p) processes the
    prompt then enters the REPL (stays alive) — verified, core_plan tech #1.
  - spawn_cc_resume(session_id, prompt): RESUME an existing CC session by id
    (same session_id; cwd restored by CC). For evoke (revive a dead peer so
    connect can talk to the same session_id). `claude --resume <id> <prompt>`
    — user-confirmed working on Windows (enters REPL, processes prompt, stays
    alive, same session_id).

Both detach via `cmd /c start` + DETACHED_PROCESS so the spawned CC survives
the caller's exit. CC has no headless mode — a visible terminal is required
for an interactive (non-`-p`) session.

Linux is NOT implemented (Win-only for now). Interface in place for later.
"""
from __future__ import annotations

import os
import subprocess

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _detached_popen(cmd_args):
    """Start a detached process (Windows): independent of the parent's
    console/process group, survives parent exit. `start` opens a new window
    for the interactive CC (it needs a TTY)."""
    subprocess.Popen(
        cmd_args,
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def spawn_cc_new(cwd: str, prompt: str):
    """Spawn a NEW interactive CC in cwd. For create_collaborator.
    Uses `start /D` (Windows) to set the working directory of the new CC
    window. `claude --cwd` was never a valid flag (confirmed 2026-07-04:
    "unknown option --cwd"); `start /D <cwd> claude <prompt>` achieves the
    same effect by having the shell change directory before launching CC."""
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "/D", cwd, "claude", prompt])
    else:
        raise NotImplementedError("spawn_cc_new on Linux not yet implemented (Windows-only for now)")


def spawn_cc_resume(session_id: str, prompt: str):
    """Resume an existing CC session by id (same session_id restored). For
    evoke. User-confirmed: `claude --resume <id> <prompt>` works on Windows."""
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "claude", "--resume", session_id, prompt])
    else:
        raise NotImplementedError("spawn_cc_resume on Linux not yet implemented (Windows-only for now)")
