"""HP-08 / T38: spawned CCs must not inherit CC-internal env that breaks
resume. CLAUDE_CODE_CHILD_SESSION -> transcript saving off -> non-resumable.
Only CC-internal vars are stripped; user config is preserved."""
import os


def test_child_env_strips_child_session_and_injects_token(server, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CC_COMMUNICATE_SPAWN_TOKEN", "stale")
    monkeypatch.setenv("ANTHROPIC_MODEL", "x")  # user vars are NOT stripped
    env = server.spawn._child_env("tok-1")
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert env["CC_COMMUNICATE_SPAWN_TOKEN"] == "tok-1"
    assert env["ANTHROPIC_MODEL"] == "x"


def test_child_env_no_token(server, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    env = server.spawn._child_env()
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert "CC_COMMUNICATE_SPAWN_TOKEN" not in env


def test_spawn_cc_new_passes_sanitized_env(server, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    captured = {}
    monkeypatch.setattr(server.spawn, "_detached_popen",
                        lambda cmd_args, cwd=None, env=None:
                        captured.update(cmd=cmd_args, env=env))
    server.spawn.spawn_cc_new("/tmp", "prompt", spawn_token="t1")
    assert "CLAUDE_CODE_CHILD_SESSION" not in captured["env"]
    assert captured["env"]["CC_COMMUNICATE_SPAWN_TOKEN"] == "t1"


def test_spawn_cc_resume_passes_sanitized_env(server, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    captured = {}
    monkeypatch.setattr(server.spawn, "_detached_popen",
                        lambda cmd_args, cwd=None, env=None:
                        captured.update(env=env))
    server.spawn.spawn_cc_resume("s1", "prompt", "/tmp")
    assert "CLAUDE_CODE_CHILD_SESSION" not in captured["env"]


def test_tmux_spawn_strips_child_session(server, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    captured = {}
    monkeypatch.setattr(server.spawn.subprocess, "Popen",
                        lambda *a, **kw: captured.update(args=a[0]))
    server.spawn._tmux_spawn("/tmp", ["/bin/claude", "p"], env_token="t1")
    args = captured["args"]
    assert args[0] == "tmux"
    assert "env" in args and "-u" in args
    assert "CLAUDE_CODE_CHILD_SESSION" in args
    assert "CC_COMMUNICATE_SPAWN_TOKEN=t1" in args
