"""HP-04: token -> sid map; pending marker; claim idempotency; start-event binding.
HP-08: pending-spawn marker TTL (expired markers count as absent)."""
import json
import os
import time


def _write_pending(server, token, created_ms=None):
    d = server.paths.PENDING_SPAWN_DIR
    os.makedirs(d, exist_ok=True)
    marker = {"schema_version": 1, "spawn_token": token}
    if created_ms is not None:
        marker["created_at_ms"] = created_ms
    with open(os.path.join(d, token + ".json"), "w", encoding="utf-8") as f:
        json.dump(marker, f)


def test_find_session_by_token(server):
    ka = server.kernel_api
    toks = {"t1": "s1"}
    assert ka.find_session_by_token(toks, "t1") == "s1"
    assert ka.find_session_by_token(toks, "nope") is None


def test_has_pending_spawn(server):
    ka = server.kernel_api
    assert ka.has_pending_spawn("t1") is False
    _write_pending(server, "t1")
    assert ka.has_pending_spawn("t1") is True


def test_claim_pending_spawn(server):
    ka = server.kernel_api
    toks = {}
    _write_pending(server, "t1")
    r = ka.claim_pending_spawn(toks, "t1", "s1")
    assert r == {"claimed": True, "session_id": "s1"}
    assert ka.find_session_by_token(toks, "t1") == "s1"
    assert ka.has_pending_spawn("t1") is False  # claim consumes the marker


def test_claim_pending_spawn_idempotent(server):
    ka = server.kernel_api
    toks = {"t1": "s1"}
    r = ka.claim_pending_spawn(toks, "t1", "s2")
    assert r == {"claimed": True, "session_id": "s1"}  # keeps the FIRST binding


def test_claim_without_pending_rejected(server):
    ka = server.kernel_api
    assert ka.claim_pending_spawn({}, "t1", "s1") == \
        {"claimed": False, "reason": "no pending spawn for token"}


def test_spawn_cc_new_writes_pending_and_token(server, monkeypatch):
    ka = server.kernel_api
    spawned = {}
    monkeypatch.setattr(server.spawn, "spawn_cc_new",
                        lambda cwd, prompt, spawn_token=None:
                        spawned.update(cwd=cwd, token=spawn_token))
    r = ka.spawn_cc_new("/tmp", "prompt", spawn_token="t1")
    assert r == {"spawned": True, "spawn_token": "t1"}
    assert spawned["token"] == "t1"
    assert os.path.isfile(os.path.join(server.paths.PENDING_SPAWN_DIR, "t1.json"))
    # no token -> no pending file, spawn_token None
    r2 = ka.spawn_cc_new("/tmp", "prompt")
    assert r2["spawn_token"] is None
    assert not os.path.exists(os.path.join(server.paths.PENDING_SPAWN_DIR, "None.json"))


def test_handle_start_binds_token(server):
    """Plan A: a start event carrying spawn_token populates the kernel map."""
    k = server.kernel
    ka = server.kernel_api
    k.spawn_tokens.clear()
    k.alive_sessions.clear()
    _write_pending(server, "t1")
    ev = {"event": "start", "event_ts": 1, "session_id": "s1", "pid": 11,
          "cwd": "/tmp", "start_time": None, "spawn_token": "t1"}
    k._handle_start(ev, "s1")
    assert k.spawn_tokens.get("t1") == "s1"
    assert ka.has_pending_spawn("t1") is False  # plan-A bind consumes the marker
    # end event releases the token
    k._handle_end(ev, "s1")
    assert k.spawn_tokens.get("t1") is None


def test_handle_start_no_token_no_binding(server):
    k = server.kernel
    k.spawn_tokens.clear()
    k.alive_sessions.clear()
    ev = {"event": "start", "event_ts": 1, "session_id": "s1", "pid": 11,
          "cwd": "/tmp", "start_time": None}
    k._handle_start(ev, "s1")
    assert k.spawn_tokens == {}


# ---------- HP-08: pending-spawn marker TTL ----------
# A poisoned marker (kernel crash in the write-window: marker written, child
# never spawned, no start event) must expire - otherwise same-token retries
# never re-spawn (Wave-2 deferred minor).


def test_has_pending_spawn_expired_marker(server):
    ka = server.kernel_api
    _write_pending(server, "t1", int((time.time() - 2 * 3600) * 1000))
    assert ka.has_pending_spawn("t1") is False


def test_claim_expired_marker_rejected(server):
    ka = server.kernel_api
    _write_pending(server, "t1", int((time.time() - 2 * 3600) * 1000))
    assert ka.claim_pending_spawn({}, "t1", "s1") == \
        {"claimed": False, "reason": "no pending spawn for token"}


def test_spawn_after_expiry_writes_fresh_marker(server, monkeypatch):
    ka = server.kernel_api
    monkeypatch.setattr(server.spawn, "spawn_cc_new", lambda *a, **kw: None)
    _write_pending(server, "t1", int((time.time() - 2 * 3600) * 1000))
    assert ka.has_pending_spawn("t1") is False
    ka.spawn_cc_new("/tmp", "prompt", spawn_token="t1")
    assert ka.has_pending_spawn("t1") is True  # fresh marker replaced the stale one
