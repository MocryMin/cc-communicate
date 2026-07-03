"""Platform-specific CC process spawning for evoke / create_collaborator.

Windows is implemented (core_plan tech challenge #1, verified):
  `cmd /c start claude --cwd <dir> <prompt>` opens a new cmd window. `claude
  <prompt>` WITHOUT -p processes the prompt then enters the interactive REPL
  (does not exit); `start` detaches the window from the parent so the spawned
  CC survives the caller's exit. CC has no headless mode — a visible terminal
  is required for an interactive (non-`-p`) session.

Linux is NOT implemented (Win-only for now). The interface is in place so a
Linux branch can be added later without touching callers — candidates would be
`gnome-terminal --working-directory=<cwd> -- claude <prompt>` or `xterm -e ...`.
"""
from __future__ import annotations

import os
import subprocess

# Windows process creation flags (avoid importing subprocess constants that
# don't exist on non-Windows).
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def spawn_cc(cwd: str, prompt: str):
    """Spawn an interactive CC in cwd with an initial prompt. Detached — the
    spawned CC survives the caller's exit. Dispatches by platform."""
    if os.name == "nt":
        _spawn_cc_windows(cwd, prompt)
    else:
        _spawn_cc_linux(cwd, prompt)


def _spawn_cc_windows(cwd: str, prompt: str):
    # `start` opens a new cmd window (interactive CC needs a TTY). DETACHED_PROCESS
    # | CREATE_NEW_PROCESS_GROUP makes the child independent of the parent's
    # console/process group, so it survives the caller (e.g. the kernel) exiting.
    subprocess.Popen(
        ["cmd", "/c", "start", "claude", "--cwd", cwd, prompt],
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _spawn_cc_linux(cwd: str, prompt: str):
    # TODO: open a terminal running claude in cwd. Not implemented — Windows-only
    # for now. Raise so callers fail loudly rather than silently no-op'ing.
    raise NotImplementedError("spawn_cc on Linux not yet implemented (Windows-only for now)")
