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


def _detached_popen(cmd_args, cwd=None, env=None):
    """Windows: detached process independent of parent, survives parent exit.
    `start` opens a new window for the interactive CC (it needs a TTY). cwd is
    set via Popen (not `start /D <path>`) so paths with spaces work, and so the
    spawned/resumed CC's per-project lookup keys on the right cwd (T25). env:
    extra vars for the child (HP-04 spawn_token; inherited by cmd -> claude ->
    SessionStart hook)."""
    subprocess.Popen(
        cmd_args,
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _child_env(spawn_token: str = None) -> dict:
    """Sanitized child env for spawned CCs (T38 / HP-08): CC-internal
    CLAUDE_CODE_CHILD_SESSION must not leak into children (it turns
    transcript saving off -> non-resumable sessions). Whitelist-extensible -
    only add vars with concrete evidence. HP-04 spawn_token (plan A) is
    injected here."""
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_CHILD_SESSION", None)
    if spawn_token:
        env["CC_COMMUNICATE_SPAWN_TOKEN"] = spawn_token
    return env


def _permission_argv(mode: str) -> list:
    """HP-10 (D4): argv fragment for the spawn's permission mode. "bypass"
    skips the workspace-trust dialog (unattended automation opt-in); the
    "standard" default omits the flag so the spawned CC makes normal
    permission decisions (a trust dialog may appear)."""
    if mode == "bypass":
        return ["--dangerously-skip-permissions"]
    return []


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


def _tmux_spawn(cwd: str, claude_argv: list, env_token: str = None):
    """WSL: detached tmux session (pty) running claude. Survives parent exit.
    `-c` sets cwd (equivalent to Windows `start /D`). Session name is unique
    (time + pid) to avoid collisions on repeated evoke (C11). env_token (HP-04):
    the spawn token is set INSIDE the session via `env VAR=x claude` so claude
    and its SessionStart hook see it."""
    session_name = f"cc_{int(time.time())}_{os.getpid()}"
    cmd = ["tmux", "new-session", "-d", "-s", session_name]
    if cwd:
        cmd += ["-c", cwd]
    # T38: strip CC-internal CLAUDE_CODE_CHILD_SESSION from the session env
    # (it turns transcript saving off -> non-resumable workers)
    env_args = ["env", "-u", "CLAUDE_CODE_CHILD_SESSION"]
    if env_token:
        env_args.append("CC_COMMUNICATE_SPAWN_TOKEN=" + env_token)
    cmd += env_args + claude_argv
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def spawn_cc_new(cwd: str, prompt: str, spawn_token: str = None,
                 permission_mode: str = "standard"):
    """Spawn a NEW interactive CC in cwd (for create_collaborator /
    spawn_collaborator). `claude <prompt>` (no -p) processes the prompt then
    enters the REPL (stays alive). permission_mode (HP-10/D4): "standard"
    default - the spawned CC decides permissions normally; "bypass" adds
    --dangerously-skip-permissions (unattended automation opt-in). cwd is
    set via Popen (T25). spawn_token (HP-04) is injected into the child
    environment so the SessionStart hook can bind the session to its spawn
    request (plan A, D8)."""
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "claude"]
                        + _permission_argv(permission_mode) + [prompt],
                        cwd=cwd, env=_child_env(spawn_token))
    else:
        _tmux_spawn(cwd, [_claude_bin()] + _permission_argv(permission_mode)
                    + [prompt], env_token=spawn_token)


def spawn_cc_resume(session_id: str, prompt: str, cwd: str = None,
                    permission_mode: str = "bypass"):
    """Resume an existing CC session by id (for evoke). Same session_id
    restored. `claude --resume <id> <prompt>` enters the REPL, processes the
    prompt, stays alive. cwd MUST be the session's original cwd (T25).
    permission_mode (HP-10/D4): "bypass" default - resume of an established
    session is not a new trust decision (R8); pass "standard" to override.
    `--resume` restores the conversation, NOT the process cwd, so set cwd
    here explicitly (Popen on Windows, -c on tmux)."""
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "claude", "--resume", session_id]
                        + _permission_argv(permission_mode) + [prompt],
                        cwd=cwd, env=_child_env())
    else:
        _tmux_spawn(cwd or "", [_claude_bin(), "--resume", session_id]
                    + _permission_argv(permission_mode) + [prompt])
