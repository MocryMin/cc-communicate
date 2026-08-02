"""HP-08 / D10: exit looks ONLY at queue/activity/terminate - a registered-
but-idle conversation is NOT a process lease (state persists + reloads)."""
import os
import time


def test_registered_but_idle_exits(server):
    """THE behavior change: registration no longer blocks exit."""
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 0.0
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    k._last_activity = time.monotonic() - 1.0
    assert k._should_exit() is True


def test_fresh_activity_blocks_exit(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 600.0
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    k._last_activity = time.monotonic()
    assert k._should_exit() is False


def test_queue_pending_blocks_exit(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 0.0
    k._last_activity = time.monotonic() - 1.0
    req = os.path.join(server.paths.QUEUE_DIR, "1234_rid.json")
    with open(req, "w", encoding="utf-8") as f:
        f.write("{}")
    assert k._should_exit() is False
    os.remove(req)
    assert k._should_exit() is True


def test_explicit_exit_and_flag_win(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 600.0
    k._last_activity = time.monotonic()
    k._exit_requested = True
    assert k._should_exit() is True
    k._exit_requested = False
    open(server.paths.TERMINATE_FLAG, "w").close()
    assert k._should_exit() is True


def test_exit_decision_second_queue_scan(server):
    """R4: a request that lands in the exit window (between _should_exit and
    the break) restarts the cycle - the second scan is the optimization,
    client retry + _wake_remote the correctness backstop."""
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 0.0
    k._last_activity = time.monotonic() - 1.0
    assert k._exit_decision() is True
    req = os.path.join(server.paths.QUEUE_DIR, "1234_rid.json")
    with open(req, "w", encoding="utf-8") as f:
        f.write("{}")
    assert k._exit_decision() is False  # second scan sees the request
    os.remove(req)


def test_registered_convs_survive_exit_and_restart(server):
    """Acceptance: registered-but-idle kernel exits; a fresh kernel instance
    reloads the registration from disk; send_message still works."""
    k = server.kernel
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    k._IDLE_TIMEOUT = 0.0
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    k._last_activity = time.monotonic() - 1.0
    assert k._should_exit() is True    # can exit while registered
    k._save_alive_convs()              # exit path persists
    k.alive_conversations.clear()      # process gone
    k._load_alive_convs()              # restart reloads
    assert ("a", "b") in k.alive_conversations
    r = ka.send_message(k.alive_conversations, {}, "store", "a", "b", "hi")
    assert r["sent"] is True


def test_dispatch_routes_run_gc(server):
    """run_gc is a kernel function (not an MCP tool): the dispatch boundary
    routes it (Wave-2 lesson: dispatch gaps are silent)."""
    k = server.kernel
    res = k._dispatch("run_gc", {"dry_run": True})
    assert res["dry_run"] is True
    assert "deleted" in res
    res2 = k._dispatch("run_gc", {})
    assert res2["dry_run"] is False
