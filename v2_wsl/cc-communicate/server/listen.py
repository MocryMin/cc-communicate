"""Merged keep_listen listener (v2.2 Amd3; replaces arm_poller + listen_poller.py
+ collect_messages as a CC-facing tool).

Run by CC as:  python listen.py <session_id> [timeout_seconds]
(inside a Bash call with run_in_background: true). Exits 0 with collected
messages as JSON on stdout when an undelivered message addressed to session_id
appears; exits 2 on timeout.

Semantics (v2.1 §3.4.4 / #W5/#W6):
  - any-undelivered: triggers as soon as ANY undelivered pipe message addressed
    to session_id exists (no baseline). Archiving pipe->log prevents re-trigger.
  - direction-specific: only collects messages where toid == session_id.
  - settle 3s: on detecting candidates, wait 3s before reading (defends against
    a writer mid-write), then read + archive only the initially-detected files.
  - fixed 2s poll (no exponential backoff - #W13).

Routing (Phase 2): scans local conversations/ always; on a WSL machine also
scans each registered peer's conversations/ (read-only, via the peer's
data_dir). Archiving a message that lives in a REMOTE conversations/ is
delegated to that peer's kernel via call_remote("collect_messages") - we are
read-only on the peer's conversations (#W7). Local archiving is direct file
I/O (no kernel needed - #W5).
"""
from __future__ import annotations

import json
import os
import sys
import time

from paths import CONVERSATIONS_DIR, MACHINE_INFO_LOG_DIR
import conversations

SETTLE = 3.0
POLL = 2.0


def _peer_conv_roots():
    """[(entry, conv_dir)] for each registered peer, conv_dir in our perspective."""
    roots = []
    try:
        names = os.listdir(MACHINE_INFO_LOG_DIR)
    except (FileNotFoundError, OSError):
        return roots
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(MACHINE_INFO_LOG_DIR, name), encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        dd = entry.get("data_dir")
        if dd:
            roots.append((entry, os.path.join(dd, "conversations")))
    return roots


def _scan_root(root, sid):
    """Undelivered pipe files in root where toid == sid: [(conv, fname, path)]."""
    found = []
    try:
        entries = os.listdir(root)
    except (FileNotFoundError, PermissionError, OSError):
        return found
    for name in entries:
        parts = name.split(conversations.SEP)
        if len(parts) != 2 or sid not in parts:
            continue
        pipe = os.path.join(root, name, "pipe")
        if not os.path.isdir(pipe):
            continue
        try:
            files = os.listdir(pipe)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        for fname in files:
            parsed = conversations.parse_pipe_filename(fname)
            if parsed and parsed[2] == sid:
                found.append((name, fname, os.path.join(pipe, fname)))
    return found


def _archive_local(cands, sid):
    """Read + archive (pipe->log) local candidates. Returns message dicts."""
    messages = []
    for conv, fname, path in cands:
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        parsed = conversations.parse_pipe_filename(fname)
        messages.append({
            "time": parsed[0] if parsed else 0,
            "from_id": parsed[1] if parsed else "",
            "message": content,
        })
        log = os.path.join(CONVERSATIONS_DIR, conv, "log")
        try:
            os.makedirs(log, exist_ok=True)
            os.replace(path, os.path.join(log, fname))
        except OSError:
            pass
    return messages


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: listen.py <session_id> [timeout]\n")
        sys.exit(2)
    sid = sys.argv[1]
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
    deadline = time.time() + timeout
    peers = _peer_conv_roots()

    while True:
        local_cands = _scan_root(CONVERSATIONS_DIR, sid)
        remote_groups = []  # [(entry, cands)]
        for entry, conv_dir in peers:
            c = _scan_root(conv_dir, sid)
            if c:
                remote_groups.append((entry, c))

        if local_cands or remote_groups:
            time.sleep(SETTLE)  # defend against a writer mid-write
            messages = _archive_local(local_cands, sid)
            for entry, _cands in remote_groups:
                # We are read-only on the peer's conversations (#W7): delegate
                # archive to the peer kernel. collect_messages returns the
                # archived messages (content included) - use them as delivered.
                try:
                    import rpc_client
                    got = rpc_client.call_remote(entry, "collect_messages", {"session_id": sid})
                    if isinstance(got, list):
                        messages.extend(got)
                except Exception:
                    pass
            messages.sort(key=lambda m: m.get("time", 0))
            print(json.dumps(messages, ensure_ascii=False))
            sys.exit(0)

        if time.time() >= deadline:
            sys.exit(2)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
