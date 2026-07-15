"""Machine identity for cross-realm routing (v2.2 Amd7, wsl2_core_plan §3.1.1).

Each machine has {type, id, claude_bin}:
  type:      win-host | wsl-<distro> | linux-unknown  (auto-detected)
  id:        uuid4 (persistent, generated once)
  claude_bin: absolute path to the claude binary (Linux only; see C13). None on
              Windows where `claude` is on PATH.

machine_identity.json is generated on kernel init (or first MCP-tool use) if
absent, then reused. The MCP server reads it to know its own type (for routing
and listen.py) and to stamp the `machine` field on local sessions; spawn.py
reads claude_bin so WSL spawns the Linux claude, not the Windows one.
"""
from __future__ import annotations

import json
import os
import platform
import time
import uuid

from paths import MACHINE_IDENTITY_FILE, SERVER_DATA_DIR, DATA_DIR


def detect_type() -> str:
    """Auto-detect the machine type."""
    if os.name == "nt":
        return "win-host"
    try:
        with open("/proc/version", encoding="utf-8") as f:
            ver = f.read().lower()
    except OSError:
        ver = ""
    if "microsoft" in ver:
        distro = (os.environ.get("WSL_DISTRO_NAME") or "wsl").strip().lower() or "wsl"
        return f"wsl-{distro}"
    return "linux-unknown"


def _detect_claude_bin() -> str | None:
    """Absolute path to this process's claude binary ancestor (Linux), or None.

    On WSL, `which claude` returns the Windows version (C13); the Linux claude
    lives at e.g. /home/<user>/.npm-global/bin/claude and is not on the default
    PATH. We resolve it from our own process tree (we are a descendant of the
    claude that launched us)."""
    if os.name == "nt":
        return None
    # Imported lazily to avoid any import-cycle at module load.
    from proc import claude_binary_path
    return claude_binary_path(os.getpid())


def _atomic_write(obj: dict):
    os.makedirs(SERVER_DATA_DIR, exist_ok=True)
    tmp = MACHINE_IDENTITY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, MACHINE_IDENTITY_FILE)


def load_or_create() -> dict:
    """Return this machine's identity dict, creating the file on first call.

    An existing file missing the optional claude_bin field is upgraded in place
    (so a WSL kernel started before this field existed picks it up)."""
    try:
        with open(MACHINE_IDENTITY_FILE, encoding="utf-8") as f:
            ident = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        ident = None
    if not isinstance(ident, dict) or "type" not in ident or "id" not in ident:
        ident = {"type": detect_type(), "id": str(uuid.uuid4())}
    # Upgrade: ensure claude_bin is present on Linux (None on Windows).
    if "claude_bin" not in ident:
        ident["claude_bin"] = _detect_claude_bin()
        _atomic_write(ident)
    return ident


def local_type() -> str:
    """Convenience: this machine's type string (win-host / wsl-ubuntu / ...)."""
    return load_or_create().get("type", "unknown")


def wsl_distro_name() -> str | None:
    """The raw WSL distro name (e.g. 'Ubuntu'), or None if not WSL."""
    if os.name == "nt":
        return None
    try:
        with open("/proc/version", encoding="utf-8") as f:
            if "microsoft" not in f.read().lower():
                return None
    except OSError:
        return None
    return os.environ.get("WSL_DISTRO_NAME") or None


def to_peer_perspective(my_native_data_dir: str, peer_type: str) -> str:
    """Convert MY data_dir (native) into the PEER's perspective, so the peer can
    do file I/O on my queue. Used during the handshake (v2.1 #W11, v2.2 Amd7):
    drive letter and distro are DERIVED dynamically (not hardcoded to C: /
    Ubuntu) so the plugin is portable across drives / distros."""
    my_type = detect_type()
    if my_type == "win-host" and peer_type.startswith("wsl-"):
        p = my_native_data_dir.replace("\\", "/")
        if len(p) >= 2 and p[1] == ":":
            return "/mnt/" + p[0].lower() + p[2:]
        return p
    if my_type.startswith("wsl-") and peer_type == "win-host":
        distro = wsl_distro_name() or "Ubuntu"
        return "//wsl.localhost/" + distro + my_native_data_dir
    return my_native_data_dir  # same-OS or unknown: native works for file I/O


def _system_info() -> dict:
    return {"platform": platform.platform(), "hostname": platform.node()}


def build_self_entry(peer_type: str) -> dict:
    """This machine's registry entry, for sending to a peer during the handshake
    (v2.2 Amd7). `peer_type` selects how data_dir is expressed (the peer's
    perspective). Includes everything the peer needs: data_dir (for queue file
    I/O) and wake_interpreter + wake_script_native + distro (for cross-machine
    wake exec, Amd8)."""
    ident = load_or_create()
    my_type = ident.get("type", detect_type())
    return {
        "type": my_type,
        "id": ident.get("id"),
        "data_dir": to_peer_perspective(DATA_DIR, peer_type),          # peer's perspective (file I/O on our queue)
        "data_dir_native": DATA_DIR,                                    # our own perspective (peer runs wake exec here)
        "wake_interpreter": "python.exe" if os.name == "nt" else "python3",
        "wake_script_native": os.path.join(os.path.dirname(DATA_DIR), "server", "wake_kernel.py"),
        "distro": wsl_distro_name(),                                    # raw WSL distro name, or None on host
        "system_info": _system_info(),
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }
