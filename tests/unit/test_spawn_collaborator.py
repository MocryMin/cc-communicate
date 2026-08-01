"""HP-04: spawn_collaborator WorkerHandle + same-token retry + claim + legacy wrapper."""
import time

import pytest
from result import Code
from rpc_client import KernelError

import user_functions


def test_spawn_collaborator_handle(server, monkeypatch):
    """New token: spawn -> poll find_session_by_token -> registered handle."""
    calls = {}

    def call(fn, args=None, timeout=30.0, operation_id=None):
        args = args or {}
        calls[fn] = args
        if fn == "find_session_by_token":
            return None  # first poll: not registered yet
        if fn == "has_pending_spawn":
            return False
        if fn == "spawn_cc_new":
            return {"spawned": True, "spawn_token": args.get("spawn_token")}
        raise KernelError(f"unknown: {fn}")

    monkeypatch.setattr(server.rpc_client, "call", call)
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)
    monkeypatch.setattr(user_functions, "_find_session_by_token",
                        lambda tok, machine=None: None)
    monkeypatch.setattr(user_functions, "_has_pending_spawn",
                        lambda tok, machine=None: False)
    monkeypatch.setattr(user_functions, "_spawn_new",
                        lambda cwd, prompt, tok, machine=None: {"spawned": True})
    monkeypatch.setattr(user_functions, "_worker_handle",
                        lambda sid, tok, cwd, machine=None:
                        {"session_id": sid, "machine_id": "m1", "cwd": cwd,
                         "spawn_token": tok, "connection_status": "registered"})
    # find resolves on the second poll
    state = {"n": 0}
    def find2(tok, machine=None):
        state["n"] += 1
        return "s9" if state["n"] > 1 else None
    monkeypatch.setattr(user_functions, "_find_session_by_token", find2)
    monkeypatch.setattr(user_functions, "time", time)  # no-op guard
    monkeypatch.setattr(user_functions.time, "sleep", lambda s: None)
    r = user_functions.spawn_collaborator("caller", "/tmp", spawn_token="t1")
    assert r["ok"] is True
    assert r["data"]["session_id"] == "s9"
    assert r["data"]["spawn_token"] == "t1"
    assert r["data"]["connection_status"] == "registered"
    assert state["n"] >= 2


def test_spawn_collaborator_same_token_retry_no_respawn(server, monkeypatch):
    """Same-token retry: session already bound -> original handle, NO spawn."""
    spawned = []
    monkeypatch.setattr(server.rpc_client, "call", lambda *a, **k: None)
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)
    monkeypatch.setattr(user_functions, "_find_session_by_token",
                        lambda tok, machine=None: "s9")
    monkeypatch.setattr(user_functions, "_has_pending_spawn",
                        lambda tok, machine=None: True)
    monkeypatch.setattr(user_functions, "_spawn_new",
                        lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(user_functions, "_worker_handle",
                        lambda sid, tok, cwd, machine=None:
                        {"session_id": sid, "machine_id": "m1", "cwd": cwd,
                         "spawn_token": tok, "connection_status": "registered"})
    r = user_functions.spawn_collaborator("caller", "/tmp", spawn_token="t1")
    assert r["ok"] is True and r["data"]["session_id"] == "s9"
    assert spawned == []  # never re-spawned


def test_spawn_collaborator_register_timeout(server, monkeypatch):
    monkeypatch.setattr(server.rpc_client, "call", lambda *a, **k: None)
    monkeypatch.setattr(server.rpc_client, "call_remote", lambda *a, **k: None)
    monkeypatch.setattr(user_functions, "_find_session_by_token",
                        lambda tok, machine=None: None)
    monkeypatch.setattr(user_functions, "_has_pending_spawn",
                        lambda tok, machine=None: True)
    monkeypatch.setattr(user_functions.time, "sleep", lambda s: None)
    # adapted: fake clock advances past the 30s deadline instantly (real time
    # + no-op sleep would spin the registration poll for 30 real seconds)
    clock = {"now": 0.0}
    def _tick():
        clock["now"] += 10.0
        return clock["now"]
    monkeypatch.setattr(user_functions.time, "time", _tick)
    r = user_functions.spawn_collaborator("caller", "/tmp", spawn_token="t1")
    assert r["ok"] is False and r["code"] == Code.TIMEOUT and r["retryable"] is True


def test_claim_pending_spawn_tool(server, monkeypatch):
    monkeypatch.setattr(server.rpc_client, "call",
                        lambda fn, args=None, timeout=30.0, operation_id=None:
                        {"claimed": True, "session_id": args["session_id"]}
                        if fn == "claim_pending_spawn" else None)
    r = user_functions.claim_pending_spawn("t1", "s1")
    assert r["ok"] is True and r["data"] == {"claimed": True, "session_id": "s1"}


def test_create_collaborator_legacy_strings(server, monkeypatch):
    """Legacy wrapper maps the envelope back to today's exact strings."""
    monkeypatch.setattr(user_functions, "spawn_collaborator",
                        lambda *a, **k: {
                            "ok": True, "code": None, "message": None,
                            "data": {"session_id": "s9", "machine_id": "m1",
                                     "cwd": "/tmp", "spawn_token": "t1",
                                     "connection_status": "registered"},
                            "retryable": False})
    monkeypatch.setattr(user_functions, "connect",
                        lambda *a, **k: {
                            "ok": True, "code": None, "message": None,
                            "data": {"connection_id": "c1", "reply": "hello bob",
                                     "established_at_ms": 1, "reused": False},
                            "retryable": False})
    s = user_functions.create_collaborator("caller", "/tmp", hold_time=300)
    assert s == "connect succeed; reply: hello bob"
