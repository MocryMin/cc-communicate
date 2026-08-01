"""HP-07: every migrated tool returns the envelope; user_functions parses no strings."""
import inspect

import pytest
from result import Code


def _fake_kernel(server, table):
    """Monkeypatch rpc_client.call to dispatch against an in-memory table of
    kernel functions (dicts). call_remote returns None (no peers).

    Patches user_functions.rpc_client (the exact module object user_functions
    bound at import) and raises user_functions.KernelError: the server fixture
    reloads rpc_client per test, so a test-module-level `from rpc_client import
    KernelError` would be a DIFFERENT class object than the one user_functions
    catches (reload re-executes the module, re-creating the class)."""
    import user_functions as uf
    rc = uf.rpc_client
    KernelError = uf.KernelError

    def call(fn, args=None, timeout=30.0, operation_id=None):
        args = args or {}
        if fn not in table:
            raise KernelError(f"unknown kernel function: {fn}")
        return table[fn](args)
    monkeypatch = server._m
    monkeypatch.setattr(rc, "call", call)
    monkeypatch.setattr(rc, "call_remote", lambda *a, **k: None)


def test_my_session_id_ok(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"session_by_pid": lambda a: "s1"})
    monkeypatch.setattr("proc.resolve_claude", lambda pid: (55, "t"))
    r = user_functions.my_session_id()
    assert r["ok"] is True and r["data"] == "s1"


def test_check_alive_envelope(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"check_alive": lambda a: 1})
    r = user_functions.check_alive("s1")
    assert r == {"ok": True, "code": None, "message": None,
                 "data": 1, "retryable": False}


def test_send_message_envelope(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"send_message": lambda a: {"sent": True,
                     "message_id": "m", "ts": 42, "correlation_id": None},
                     "query_session": lambda a: True})
    r = user_functions.send_message("a", "b", "hi")
    assert r["ok"] is True and r["data"] == {"message_id": "m", "ts": 42}


def test_send_message_not_registered_code(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"send_message": lambda a: {"sent": False,
                     "reason": "connection not registered"},
                     "query_session": lambda a: True})
    r = user_functions.send_message("a", "b", "hi")
    assert r["ok"] is False and r["code"] == Code.NOT_FOUND
    assert r["retryable"] is False


def test_evoke_envelope(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"evoke": lambda a: {"evoked": True, "session_id": "s1"},
                     "query_session": lambda a: "s1"})
    r = user_functions.evoke("s1")
    assert r["ok"] is True and r["data"] == {"evoked": True, "session_id": "s1"}


def test_listen_wrapped_ok(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {"listen_scan": lambda a: {"messages": [], "watermark": 0}})
    # time must ADVANCE or the poll loop never exits: deadline calc, first
    # loop check, then past the deadline.
    ticks = [0.0, 0.0, 2.0]
    monkeypatch.setattr(user_functions.time, "time",
                        lambda: ticks.pop(0) if len(ticks) > 1 else ticks[0])
    monkeypatch.setattr(user_functions, "_LISTEN_POLL", 0)
    r = user_functions.listen("s1", 0, timeout=1)
    assert r["ok"] is True and r["data"]["watermark"] == 0


def test_query_session_unknown_is_ok_none(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    _fake_kernel(server, {})
    monkeypatch.setattr(user_functions, "read_machine_info_log", lambda: [])
    r = user_functions.query_session("s1")
    assert r["ok"] is True and r["data"] is None


def test_kernel_error_maps_internal(server, monkeypatch):
    import user_functions
    server._m = monkeypatch
    def boom(a):
        raise user_functions.KernelError("kernel exploded")
    _fake_kernel(server, {"check_alive": boom})
    r = user_functions.check_alive("s1")
    assert r["ok"] is False and r["code"] == Code.INTERNAL


def test_no_string_parsing_for_control_flow(server):
    """The wave's deliverable: user_functions never branches on message text.

    Scoped to the MIGRATED surface: connect / close_connection /
    create_collaborator stay legacy until Tasks 5/6/9 (connect still has its
    Task-2 BRIDGE `"failed" in str(...)` checks), so their source is excluded
    from the grep. Asserting against the whole file would fail on the
    deliberately-untouched legacy connect - a known plan gap."""
    import user_functions
    src = inspect.getsource(user_functions)
    for legacy in (user_functions.connect,
                   user_functions.close_connection,
                   user_functions.create_collaborator):
        src = src.replace(inspect.getsource(legacy), "", 1)
    assert " in str(" not in src
    assert "startswith(" not in src
    assert "'failed' in" not in src
    assert '"failed" in' not in src
