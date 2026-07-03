"""User-function orchestration (core_plan "用户函数") — MCP tools that compose
kernel functions (+ a local subprocess for the poller). These live in the MCP
server process, NOT the kernel: they call rpc_client.call() for kernel ops and
run listen_poller.py locally for the blocking wait.

Implemented:
  - connect: p2p handshake (query -> check_alive -> evoke if dead -> register +
    send hello -> arm + run poller -> collect reply -> succeed / withdraw on fail)

TODO (later): close_connection, create_collaborator.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import rpc_client
import conversations
from paths import PLUGIN_ROOT

# How long to wait for a revived target to register as alive (evoke ->
# SessionStart hook -> kernel adds to alive_sessions).
_REVIVE_WAIT = 30.0


def connect(caller_sid: str, target_sid: str, hold_time: int = 60) -> str:
    """Establish a p2p connection to target_sid (core_plan "用户函数 2").

    Flow:
      1. query target -> fail if unknown.
      2. check_alive target -> if dead, evoke (claude --resume) + poll until
         alive (up to _REVIVE_WAIT).
      3. register the conversation (so send_message accepts the hello).
      4. send hello (caller -> target).
      5. arm_poller(caller) + run listen_poller.py synchronously (block up to
         hold_time).
      6. on poller exit 0: collect_messages, look for target's reply -> succeed.
         on timeout: withdraw (init_connect=1 cleans up a first-connect) -> fail.

    Blocks up to hold_time waiting for the reply (decision 2a). Deviation from
    plan: register happens BEFORE the hello (so send_message's registration
    check passes), and withdraw on failure cleans up; plan's "register after
    success" would block send_message."""
    # 1. target must exist.
    if not rpc_client.call("query_session", {"session_id": target_sid}):
        return "failed, target session not exists"

    # 2. target must be alive; revive if dead.
    if rpc_client.call("check_alive", {"session_id": target_sid}) != 1:
        ev = rpc_client.call("evoke", {"session_id": target_sid})
        if "failed" in ev:
            return "failed, evoke: " + ev
        deadline = time.time() + _REVIVE_WAIT
        while time.time() < deadline:
            time.sleep(1)
            if rpc_client.call("check_alive", {"session_id": target_sid}) == 1:
                break
        else:
            return "failed, target did not come alive after evoke (waited %ss)" % _REVIVE_WAIT

    # 3. first connect vs reconnect (decides cleanup on failure).
    init_connect = 0 if conversations.find_conv_dir(caller_sid, target_sid) else 1

    # 4. register so send_message accepts the hello.
    rpc_client.call("register_conversation", {"sid_a": caller_sid, "sid_b": target_sid})

    # 5. send hello.
    hello = "connect hello from " + caller_sid
    send_res = rpc_client.call("send_message",
                               {"fromid": caller_sid, "toid": target_sid, "message": hello})
    if "failed" in send_res:
        if init_connect:
            rpc_client.call("withdraw", {"fromid": caller_sid, "toid": target_sid, "init_connect": 1})
        return "failed, send hello: " + send_res

    # 6. arm + run poller (blocking).
    arm = rpc_client.call("arm_poller", {"session_id": caller_sid, "timeout": hold_time})
    if not arm.get("armed"):
        if init_connect:
            rpc_client.call("withdraw", {"fromid": caller_sid, "toid": target_sid, "init_connect": 1})
        return "failed, could not arm poller"
    poller_path = os.path.join(PLUGIN_ROOT, "server", "listen_poller.py")
    try:
        r = subprocess.run([sys.executable, poller_path, caller_sid],
                           capture_output=True, timeout=hold_time + 5)
        poller_exit = r.returncode
    except subprocess.TimeoutExpired:
        poller_exit = 2

    # 7. check for target's reply.
    if poller_exit == 0:
        msgs = rpc_client.call("collect_messages", {"session_id": caller_sid})
        reply = [m for m in msgs if m.get("from_id") == target_sid]
        if reply:
            return "connect succeed; reply: " + reply[0]["message"]
        return "connect failed, poller woke but no reply from target"

    # 8. timeout -> clean up.
    rpc_client.call("withdraw", {"fromid": caller_sid, "toid": target_sid, "init_connect": init_connect})
    return "connect failed, timeout waiting for reply"
