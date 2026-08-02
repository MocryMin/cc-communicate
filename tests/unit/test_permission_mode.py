"""HP-10 (D4): permission_mode validation + spawn argv splicing."""
import pytest

import mcp_server
import user_functions
from result import Code


def test_validate_permission_mode(server):
    v = server.validation
    assert v.validate_permission_mode("standard") == "standard"
    assert v.validate_permission_mode("bypass") == "bypass"
    for bad in ("root", "", 42, None):
        with pytest.raises(v.InvalidArgumentError):
            v.validate_permission_mode(bad)


def test_permission_argv(server):
    sp = server.spawn
    assert sp._permission_argv("standard") == []
    assert sp._permission_argv("bypass") == ["--dangerously-skip-permissions"]


def test_spawn_cc_new_default_standard_no_flag(server, monkeypatch):
    captured = {}
    monkeypatch.setattr(server.spawn, "_detached_popen",
                        lambda cmd_args, cwd=None, env=None:
                        captured.update(cmd=cmd_args))
    server.spawn.spawn_cc_new("/tmp", "prompt")
    assert "--dangerously-skip-permissions" not in captured["cmd"]


def test_spawn_cc_new_bypass_has_flag(server, monkeypatch):
    captured = {}
    monkeypatch.setattr(server.spawn, "_detached_popen",
                        lambda cmd_args, cwd=None, env=None:
                        captured.update(cmd=cmd_args))
    server.spawn.spawn_cc_new("/tmp", "prompt", permission_mode="bypass")
    assert "--dangerously-skip-permissions" in captured["cmd"]


def test_spawn_cc_resume_default_bypass_has_flag(server, monkeypatch):
    captured = {}
    monkeypatch.setattr(server.spawn, "_detached_popen",
                        lambda cmd_args, cwd=None, env=None:
                        captured.update(cmd=cmd_args))
    server.spawn.spawn_cc_resume("s1", "prompt", "/tmp")
    assert "--dangerously-skip-permissions" in captured["cmd"]


def test_dispatch_spawn_cc_new_permission_mode(server, monkeypatch):
    k = server.kernel
    calls = {}
    monkeypatch.setattr(server.spawn, "spawn_cc_new",
                        lambda cwd, prompt, spawn_token=None,
                        permission_mode="standard":
                        calls.update(mode=permission_mode))
    k._dispatch("spawn_cc_new", {"cwd": str(server.paths.DATA_DIR),
                                 "prompt": "p"})
    assert calls["mode"] == "standard"           # D4 default
    k._dispatch("spawn_cc_new", {"cwd": str(server.paths.DATA_DIR),
                                 "prompt": "p", "permission_mode": "bypass"})
    assert calls["mode"] == "bypass"
    with pytest.raises(server.validation.InvalidArgumentError):
        k._dispatch("spawn_cc_new", {"cwd": str(server.paths.DATA_DIR),
                                     "prompt": "p", "permission_mode": "root"})


def test_dispatch_spawn_cc_resume_permission_mode(server, monkeypatch):
    k = server.kernel
    calls = {}
    monkeypatch.setattr(server.spawn, "spawn_cc_resume",
                        lambda sid, prompt, cwd=None, permission_mode="bypass":
                        calls.update(mode=permission_mode))
    k._dispatch("spawn_cc_resume", {"session_id": "s1", "prompt": "p"})
    assert calls["mode"] == "bypass"             # resume default (D4-b)


def test_mcp_spawn_collaborator_default_standard(server, monkeypatch):
    captured = {}
    monkeypatch.setattr(user_functions, "spawn_collaborator",
                        lambda *a, **k: captured.update(kwargs=k) or {
                            "ok": True, "code": None, "message": None,
                            "data": {"session_id": "s1"},
                            "retryable": False})
    r = mcp_server.spawn_collaborator("caller", str(server.paths.DATA_DIR))
    assert r["ok"] is True
    assert captured["kwargs"]["permission_mode"] == "standard"   # D4 flip
    mcp_server.spawn_collaborator("caller", str(server.paths.DATA_DIR),
                                  permission_mode="bypass")
    assert captured["kwargs"]["permission_mode"] == "bypass"


def test_mcp_spawn_collaborator_bad_mode_rejected(server):
    r = mcp_server.spawn_collaborator("caller", str(server.paths.DATA_DIR),
                                      permission_mode="root")
    assert r["ok"] is False and r["code"] == Code.INVALID_ARGUMENT


def test_worker_handle_carries_permission_mode(server):
    server.paths.ensure_runtime_dirs()
    h = user_functions._worker_handle("s1", "t1", "/tmp", None,
                                      permission_mode="bypass")
    assert h["permission_mode"] == "bypass"
    h2 = user_functions._worker_handle("s1", "t1", "/tmp", None,
                                       permission_mode="standard")
    assert h2["permission_mode"] == "standard"


def test_evoke_kernel_passes_bypass_default(server, monkeypatch):
    """evoke -> spawn_cc_resume with bypass (D4-b: resume != new trust
    decision); explicit override allowed."""
    ka = server.kernel_api
    calls = {}
    monkeypatch.setattr(server.spawn, "spawn_cc_resume",
                        lambda sid, prompt, cwd=None, permission_mode="bypass":
                        calls.update(mode=permission_mode))
    ka.evoke({"s1": {"cwd": "/tmp"}}, "s1")
    assert calls["mode"] == "bypass"
    ka.evoke({"s1": {"cwd": "/tmp"}}, "s1", permission_mode="standard")
    assert calls["mode"] == "standard"


def test_mcp_evoke_override_param(server, monkeypatch):
    captured = {}
    monkeypatch.setattr(user_functions, "evoke",
                        lambda *a, **k: captured.update(kwargs=k) or {
                            "ok": True, "code": None, "message": None,
                            "data": {"evoked": True},
                            "retryable": False})
    mcp_server.evoke("s1", permission_mode="standard")
    assert captured["kwargs"]["permission_mode"] == "standard"
    r = mcp_server.evoke("s1", permission_mode="root")
    assert r["ok"] is False and r["code"] == Code.INVALID_ARGUMENT


def test_create_collaborator_legacy_bypass_marked(server, monkeypatch):
    monkeypatch.setattr(user_functions, "spawn_collaborator",
                        lambda *a, **k: {
                            "ok": True, "code": None, "message": None,
                            "data": {"session_id": "s9", "machine_id": "m1",
                                     "cwd": "/tmp", "spawn_token": "t1",
                                     "connection_status": "registered",
                                     "permission_mode": "bypass"},
                            "retryable": False})
    monkeypatch.setattr(user_functions, "connect",
                        lambda *a, **k: {
                            "ok": True, "code": None, "message": None,
                            "data": {"connection_id": "c1", "reply": "hello bob",
                                     "established_at_ms": 1, "reused": False},
                            "retryable": False})
    s = user_functions.create_collaborator("caller", "/tmp", hold_time=300)
    assert s.endswith(" ; permission_mode=bypass (legacy)")
    assert s.startswith("connect succeed; reply: hello bob")   # prefix intact


def test_bypass_spawn_logged(server, caplog, monkeypatch):
    """D4 '日志标记': bypass spawns leave a durable kernel-log line."""
    import logging
    ka = server.kernel_api
    monkeypatch.setattr(server.spawn, "spawn_cc_new",
                        lambda *a, **kw: None)
    with caplog.at_level(logging.INFO, logger="cc-communicate.kernel"):
        ka.spawn_cc_new("/tmp", "p", spawn_token="t1",
                        permission_mode="bypass")
    assert any("permission_mode=bypass" in r.message for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="cc-communicate.kernel"):
        ka.spawn_cc_new("/tmp", "p", spawn_token="t2",
                        permission_mode="standard")
    assert not any("permission_mode=bypass" in r.message for r in caplog.records)
