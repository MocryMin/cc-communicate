"""Platform-specific CC process spawning for evoke / create_collaborator.

Two spawn modes:
  - spawn_cc_new(cwd, prompt): start a NEW interactive CC in cwd.
  - spawn_cc_resume(session_id, prompt): RESUME an existing CC session by id.

Windows: `cmd /c start` opens a new window (TTY) for the interactive CC; the
spawned CC survives the caller's exit. WSL2: `tmux new-session -d` provides a
pty (no GUI needed) - the WSL2 equivalent of `cmd /c start` (v2.1 §2.3 / #W3).

Both modes pass `--dangerously-skip-permissions` so the spawned CC skips the
workspace-trust dialog (v2.2 Amd9 / D2). On WSL the claude binary is invoked by
its full Linux path (detected at kernel init, stored in machine_identity) - the
default `which claude` returns the Windows version (C13).
"""
from __future__ import annotations

import os
import subprocess
import time

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _detached_popen(cmd_args):
    """Windows: detached process independent of parent, survives parent exit.
    `start` opens a new window for the interactive CC (it needs a TTY)."""
    subprocess.Popen(
        cmd_args,
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _claude_bin() -> str:
    """The claude binary to invoke. Windows: 'claude' (on PATH). Linux: the full
    path from machine_identity (or fall back to 'claude' if undetected)."""
    if os.name == "nt":
        return "claude"
    try:
        from machine_identity import load_or_create
        binpath = load_or_create().get("claude_bin")
        if binpath:
            return binpath
    except Exception:
        pass
    return "claude"  # last resort; on WSL this may hit the Windows version (C13)


def _tmux_spawn(cwd: str, claude_argv: list):
    """WSL: detached tmux session (pty) running claude. Survives parent exit.
    `-c` sets cwd (equivalent to Windows `start /D`). Session name is unique
    (time + pid) to avoid collisions on repeated evoke (C11)."""
    session_name = f"cc_{int(time.time())}_{os.getpid()}"
    cmd = ["tmux", "new-session", "-d", "-s", session_name]
    if cwd:
        cmd += ["-c", cwd]
    cmd += claude_argv
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def spawn_cc_new(cwd: str, prompt: str):
    """Spawn a NEW interactive CC in cwd (for create_collaborator). `claude
    <prompt>` (no -p) processes the prompt then enters the REPL (stays alive).
    `--dangerously-skip-permissions` skips the workspace-trust dialog (Amd9)."""
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "/D", cwd, "claude",
                         "--dangerously-skip-permissions", prompt])
    else:
        _tmux_spawn(cwd, [_claude_bin(), "--dangerously-skip-permissions", prompt])


def spawn_cc_resume(session_id: str, prompt: str):
    """Resume an existing CC session by id (for evoke). Same session_id restored.
    `claude --resume <id> <prompt>` enters the REPL, processes the prompt, stays
    alive. cwd is restored by --resume, so no -c on the tmux branch."""
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "claude", "--resume", session_id,
                         "--dangerously-skip-permissions", prompt])
    else:
        _tmux_spawn("", [_claude_bin(), "--resume", session_id,
                         "--dangerously-skip-permissions", prompt])
