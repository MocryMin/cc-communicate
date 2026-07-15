"""Host-side machine registration script (v2.2 Amd7 / wsl2_core_plan §3.1.2).

Run on the Windows host:  python ...\\server\\machine_add.py
Listens for a WSL machine's signup at C:\\, replies with the host's entry, then
waits for the WSL success signal and registers the WSL machine in THIS host's
data/machine_info_log/. Standalone script (NOT a kernel function).

Handshake (host side does steps 2 + 4):
  2. discover C:\\cc_signup_<wsl_id>.json -> read WSL entry, write
     C:\\cc_echo_<wsl_id>.json (host entry), delete signup.
  4. discover C:\\cc_success_<wsl_id>.json -> register WSL, delete success.

Global timeout 5 min. Each per-step wait 60s, then restarts the listen loop.
"""
from __future__ import annotations

import json
import os
import sys
import time

import machine_identity
from paths import MACHINE_INFO_LOG_DIR

# C:\ root as seen from Windows.
HANDSHAKE_DIR = "C:\\"


def _path(name):
    return os.path.join(HANDSHAKE_DIR, name)


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _list(prefix):
    try:
        return [f for f in os.listdir(HANDSHAKE_DIR)
                if f.startswith(prefix) and f.endswith(".json")]
    except OSError:
        return []


def main():
    # Host's self entry for a WSL peer (data_dir in WSL perspective = /mnt/c/...).
    self_entry = machine_identity.build_self_entry("wsl-")
    print("activated, listening for WSL signup at %s ..." % HANDSHAKE_DIR)

    global_deadline = time.time() + 300
    while time.time() < global_deadline:
        signups = _list("cc_signup_")
        if not signups:
            time.sleep(1)
            continue
        signup_path = _path(signups[0])
        wsl_entry = _read_json(signup_path)
        if not wsl_entry:
            try:
                os.remove(signup_path)
            except OSError:
                pass
            continue
        wsl_id = wsl_entry.get("id")
        if not wsl_id:
            continue

        # Step 2: echo host entry back, delete signup.
        echo_path = _path(f"cc_echo_{wsl_id}.json")
        _write_json(echo_path, self_entry)
        try:
            os.remove(signup_path)
        except OSError:
            pass

        # Step 4: wait for success, then register WSL.
        success_path = _path(f"cc_success_{wsl_id}.json")
        sdeadline = time.time() + 60
        ok = False
        while time.time() < sdeadline:
            if os.path.exists(success_path):
                os.makedirs(MACHINE_INFO_LOG_DIR, exist_ok=True)
                _write_json(os.path.join(MACHINE_INFO_LOG_DIR, f"{wsl_id}.json"), wsl_entry)
                try:
                    os.remove(success_path)
                except OSError:
                    pass
                print("success! registered WSL %s (type=%s, data_dir=%s)" % (
                    wsl_id, wsl_entry.get("type"), wsl_entry.get("data_dir")))
                ok = True
                break
            time.sleep(1)
        if ok:
            return
        # success timeout -> clean echo residue, loop to listen again.
        try:
            os.remove(echo_path)
        except OSError:
            pass

    print("timeout: no WSL registered within 5 min")
    sys.exit(1)


if __name__ == "__main__":
    main()
