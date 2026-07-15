"""WSL-side machine registration script (v2.2 Amd7 / wsl2_core_plan §3.1.2).

Run on WSL:  python3 .../server/machine_sign_up.py
Performs the 4-way C:/ handshake with the host's machine_add.py, then writes
the host's entry to THIS machine's data/machine_info_log/. Standalone script
(NOT a kernel function); the kernel never touches machine_info_log during the
handshake.

Handshake (WSL side does steps 1 + 3):
  1. write C:\\cc_signup_<my_id>.json  (our entry, host-perspective data_dir)
  3. poll C:\\cc_echo_<my_id>.json     (host's entry) -> register host, write
     C:\\cc_success_<my_id>.json, delete echo.

Timeout 60s for the host's echo. Requires the host to be running machine_add.py.
"""
from __future__ import annotations

import json
import os
import sys
import time

import machine_identity
from paths import MACHINE_INFO_LOG_DIR

# C:\ root as seen from WSL (/mnt/c).
HANDSHAKE_DIR = "/mnt/c"


def _p(name, ident):
    return os.path.join(HANDSHAKE_DIR, f"cc_{name}_{ident['id']}.json")


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _cleanup_self(ident):
    for name in ("signup", "echo", "success"):
        try:
            os.remove(_p(name, ident))
        except OSError:
            pass


def main():
    ident = machine_identity.load_or_create()
    _cleanup_self(ident)  # remove our own residue from a prior failed run

    self_entry = machine_identity.build_self_entry("win-host")
    print("signing up (writing C:\\cc_signup_%s.json)..." % ident["id"])
    try:
        _write_json(_p("signup", ident), self_entry)
    except OSError as e:
        print("failed: cannot write to %s (%s)" % (HANDSHAKE_DIR, e))
        sys.exit(1)

    print("shaking hand (polling for host echo)...")
    deadline = time.time() + 60
    host_entry = None
    while time.time() < deadline:
        host_entry = _read_json(_p("echo", ident))
        if host_entry:
            break
        time.sleep(1)

    if not host_entry:
        _cleanup_self(ident)
        print("failed: no echo from host within 60s (is machine_add.py running on the host?)")
        sys.exit(1)

    os.makedirs(MACHINE_INFO_LOG_DIR, exist_ok=True)
    _write_json(os.path.join(MACHINE_INFO_LOG_DIR, f"{host_entry['id']}.json"), host_entry)

    _write_json(_p("success", ident), {"ok": True, "id": ident["id"]})
    try:
        os.remove(_p("echo", ident))
    except OSError:
        pass

    print("success! registered host %s (type=%s, data_dir=%s)" % (
        host_entry.get("id"), host_entry.get("type"), host_entry.get("data_dir")))


if __name__ == "__main__":
    main()
