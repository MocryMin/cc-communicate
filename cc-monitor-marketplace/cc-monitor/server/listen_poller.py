"""Background poller for keep_listen (core_plan "用户函数 4", part b).

Run by CC as:  python <plugin_root>/server/listen_poller.py <session_id>
(inside a Bash call with run_in_background: true). Exits 0 when a new message
addressed to session_id arrives (CC's harness injects a <task-notification> on
exit, waking CC), or 2 on timeout.

Reads its config from data/server/poller_<session_id>.json (written by
arm_poller): baseline (undelivered message count at arm time), deadline. Each
cycle, recounts undelivered messages for session_id across ALL conversation
folders (conversations.count_undelivered); count > baseline => new message =>
exit 0. Re-scanning all folders means a conversation folder appearing after
arming (a new partner's first message) is still detected. Backoff
5s -> 10s -> ... -> 5min, capped by deadline.

Python instead of the plan's bash so the user can audit it and paths resolve
cross-platform via paths.py (no Git-Bash Windows-path quirks). Token economy is
unchanged: CC issues one background Bash call, the poller consumes no tokens
while running, only its EXIT wakes CC.
"""
from __future__ import annotations

import json
import os
import sys
import time

from paths import SERVER_DATA_DIR
import conversations

_BACKOFF_START = 5.0
_BACKOFF_MAX = 300.0


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: listen_poller.py <session_id>\n")
        sys.exit(2)
    sid = sys.argv[1]
    config_path = os.path.join(SERVER_DATA_DIR, f"poller_{sid}.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        sys.exit(2)  # no/invalid config -> nothing to watch

    baseline = cfg.get("baseline", 0)
    deadline = cfg.get("deadline", 0)

    sleep = _BACKOFF_START
    while True:
        # Check FIRST, so an already-present new message exits 0 immediately.
        if conversations.count_undelivered(sid) > baseline:
            sys.exit(0)
        remaining = deadline - time.time()
        if remaining <= 0:
            sys.exit(2)  # timeout
        time.sleep(min(sleep, remaining))
        sleep = min(sleep * 2, _BACKOFF_MAX)


if __name__ == "__main__":
    main()
