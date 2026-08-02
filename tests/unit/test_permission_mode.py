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
