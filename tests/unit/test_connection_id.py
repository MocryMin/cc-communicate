"""HP-05: connect correlates replies by connection_id; info.json enforces
single active connection; legacy replies (no correlation_id) fall back only
when unambiguous."""
import json
import os
import time

import pytest
from result import Code
from rpc_client import KernelError

import message_record


def _seq_state():
    return {"schema_version": 1, "store_id": "store-test", "last_allocated": 0}


def test_claim_reply_matches_by_correlation_id(server):
    """The deliverable: a reply record whose correlation_id == connection_id is
    claimed even when a foreign newer message sits in the pipe."""
    import user_functions
    d = server.conversations.ensure_conv_dir("alice", "bob")
    pipe = os.path.join(d, "pipe")
    # a FOREIGN message from bob, newer than the hello - must NOT be taken
    foreign = message_record.new_record("store-test", 2, "bob", "alice",
                                        "foreign", correlation_id="other")
    rec = message_record.new_record("store-test", 3, "bob", "alice",
                                    "the reply", correlation_id="conn-1")
    for r in (foreign, rec):
        message_record.publish(d, r)
    got = user_functions._claim_reply(pipe, "alice", "bob", None,
                                      hello_ts=0, connection_id="conn-1")
    assert got == "the reply"
    # only the matched one is archived; foreign stays
    remaining = [f for f in os.listdir(pipe) if f.endswith(".json")]
    assert len(remaining) == 1


def test_claim_reply_legacy_fallback_single_candidate(server):
    """Old worker replies (no correlation_id): accepted only when unambiguous."""
    import user_functions
    d = server.conversations.ensure_conv_dir("alice", "bob")
    pipe = os.path.join(d, "pipe")
    with open(os.path.join(pipe, "0000000000100__bob__alice.md"), "w",
              encoding="utf-8") as f:
        f.write("legacy reply")
    got = user_functions._claim_reply(pipe, "alice", "bob", None,
                                      hello_ts=50, connection_id="conn-1")
    assert got == "legacy reply"


def test_claim_reply_legacy_fallback_refused_when_ambiguous(server):
    import user_functions
    d = server.conversations.ensure_conv_dir("alice", "bob")
    pipe = os.path.join(d, "pipe")
    for i in (100, 101):
        with open(os.path.join(pipe, f"{i:013d}__bob__alice.md"), "w",
                  encoding="utf-8") as f:
            f.write(f"msg{i}")
    got = user_functions._claim_reply(pipe, "alice", "bob", None,
                                      hello_ts=50, connection_id="conn-1")
    assert got is None


def _record_reads(server, monkeypatch, calls):
    """Recording rpc fake for the active-connection tests: the target is alive
    and local, get_connection_info reads the REAL info.json via kernel_api.
    Any unexpected kernel function raises - a mutation attempt would fail the
    test loudly instead of silently proceeding."""
    import user_functions
    KernelError = user_functions.KernelError  # reloaded class (fixture)

    def call(fn, args=None, timeout=30.0, operation_id=None):
        args = args or {}
        calls.append((fn, args))
        if fn == "check_alive":
            return 1
        if fn == "query_session":
            return "bob"
        if fn == "get_connection_info":
            return server.kernel_api.get_connection_info(args["sid_a"],
                                                         args["sid_b"])
        raise KernelError(f"unexpected kernel function: {fn}")

    monkeypatch.setattr(server.rpc_client, "call", call)
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)


def test_connect_conflict_on_second_active(server, monkeypatch):
    """D9: connect with a DIFFERENT connection_id while one is active ->
    CONFLICT before any hello is sent."""
    import user_functions
    ka = server.kernel_api
    ka.activate_connection({}, "alice", "bob", "conn-1")
    calls = []
    _record_reads(server, monkeypatch, calls)
    monkeypatch.setattr(user_functions, "_find_target_machine",
                        lambda sid: (True, None))
    r = user_functions.connect("alice", "bob", connection_id="conn-2",
                               hold_time=1)
    assert r["ok"] is False and r["code"] == Code.CONFLICT
    assert r["data"]["current_connection_id"] == "conn-1"
    # read-only calls only - no hello, no activation, no register/withdraw
    assert [fn for fn, _ in calls] == ["check_alive", "query_session",
                                       "get_connection_info"]


def test_connect_retry_same_id_returns_state(server, monkeypatch):
    """Retry with the SAME connection_id while active -> ok(current state)."""
    import user_functions
    ka = server.kernel_api
    ka.activate_connection({}, "alice", "bob", "conn-1")
    calls = []
    _record_reads(server, monkeypatch, calls)
    monkeypatch.setattr(user_functions, "_find_target_machine",
                        lambda sid: (True, None))
    r = user_functions.connect("alice", "bob", connection_id="conn-1",
                               hold_time=1)
    assert r["ok"] is True and r["data"]["reused"] is True
    assert r["data"]["connection_id"] == "conn-1"
    assert [fn for fn, _ in calls] == ["check_alive", "query_session",
                                       "get_connection_info"]


def test_connect_hello_carries_kind_and_correlation(server, monkeypatch):
    """Hello record: kind='hello', correlation_id == connection_id; reply
    matched by correlation_id; info.json activated on success."""
    import user_functions
    KernelError = user_functions.KernelError  # reloaded class (fixture)
    kernel_ops = {}

    def call(fn, args=None, timeout=30.0, operation_id=None):
        args = args or {}
        kernel_ops[fn] = args
        if fn == "check_alive":
            return 1
        if fn == "query_session":
            return "bob" if args.get("session_id") == "bob" else None
        if fn == "get_connection_info":
            return None  # not active yet - step 4 must proceed
        if fn == "send_message":
            d = server.conversations.ensure_conv_dir(args["fromid"], args["toid"])
            seq = 1
            rec = message_record.new_record(
                "store-test", seq, args["fromid"], args["toid"], args["message"],
                kind=args.get("kind", "text"),
                correlation_id=args.get("correlation_id"),
                message_id=args["message_id"])
            message_record.publish(d, rec)
            kernel_ops["_hello_ts"] = rec["created_at_ms"]
            return {"sent": True, "message_id": rec["message_id"],
                    "ts": rec["created_at_ms"], "correlation_id": args.get("correlation_id")}
        if fn == "register_conversation":
            return {"ok": True}
        if fn == "activate_connection":
            ka = server.kernel_api
            convs = {}
            return ka.activate_connection(convs, args["sid_a"], args["sid_b"],
                                          args["connection_id"])
        if fn == "unregister_conversation":
            return {"ok": True}
        raise KernelError(f"unknown: {fn}")

    monkeypatch.setattr(server.rpc_client, "call", call)
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)
    monkeypatch.setattr(user_functions, "_find_target_machine",
                        lambda sid: (True, None))
    # user_functions binds CONVERSATIONS_DIR at import (its first import in
    # this session was an earlier test, so the binding points at that test's
    # tmp root); the server fixture reloads paths per test but not
    # user_functions. Align the constant with THIS test's data root so
    # connect's pipe scan sees the records the fake publishes.
    monkeypatch.setattr(user_functions, "CONVERSATIONS_DIR",
                        server.paths.CONVERSATIONS_DIR)

    # the peer (bob) receives the hello and replies with the correlation_id
    def peer_reply():
        d = server.conversations.conv_dir("alice", "bob")
        seq = 2
        rec = message_record.new_record(
            "store-test", seq, "bob", "alice", "hello bob here",
            kind="text", correlation_id=kernel_ops["send_message"].get("correlation_id"))
        # pin strictly-newer-than-the-hello (C3: the stale filter skips
        # ts <= hello_ts; wall-clock can land in the same millisecond as the
        # hello because the fake records kernel_ops BEFORE stamping the record)
        rec["created_at_ms"] = kernel_ops["_hello_ts"] + 1
        message_record.publish(d, rec)

    # drive connect in a background thread; bob replies after the hello lands
    import threading
    result = {}

    def do_connect():
        result["r"] = user_functions.connect("alice", "bob", hold_time=15)

    t = threading.Thread(target=do_connect)
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline and "send_message" not in kernel_ops:
        time.sleep(0.05)
    assert "send_message" in kernel_ops, "hello never sent"
    hello_args = kernel_ops["send_message"]
    assert hello_args.get("kind") == "hello"
    conn_id = hello_args.get("correlation_id")
    assert conn_id and conn_id != "conn-1"
    peer_reply()
    t.join(timeout=20)
    r = result["r"]
    assert r["ok"] is True, r
    assert r["data"]["connection_id"] == conn_id
    assert r["data"]["reply"] == "hello bob here"
    assert kernel_ops["activate_connection"]["connection_id"] == conn_id
    # info.json now active
    info = server.kernel_api.get_connection_info("alice", "bob")
    assert info["status"] == "active" and info["connection_id"] == conn_id
