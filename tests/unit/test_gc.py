"""HP-08: safe-GC whitelist. pipe/ (unacked) and log/ (conversation records)
are NEVER touched - structural (enumerated roots + path guardrail), not
convention."""
import json
import os
import time


def _make_file(path, age_seconds):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"k": "v"}, f)
    old = time.time() - age_seconds
    os.utime(path, (old, old))


def _write_pending(server, token, created_ms):
    d = server.paths.PENDING_SPAWN_DIR
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, token + ".json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "spawn_token": token,
                   "created_at_ms": created_ms}, f)


def test_collect_only_whitelisted(server):
    # old whitelisted files -> candidates
    _make_file(os.path.join(server.data_root, "session_ctrl", "e1.json"), 8 * 86400)
    _make_file(os.path.join(server.data_root, "queue", "responses", "r1.json"), 8 * 86400)
    _write_pending(server, "t-old", int((time.time() - 2 * 3600) * 1000))
    # fresh whitelisted -> NOT candidates
    _make_file(os.path.join(server.data_root, "session_ctrl", "e2.json"), 60)
    _write_pending(server, "t-fresh", int(time.time() * 1000))
    # decoys in the NEVER-TOUCH dirs (old mtime on purpose)
    _make_file(os.path.join(server.data_root, "conversations", "a__b", "pipe", "m1.json"), 90 * 86400)
    _make_file(os.path.join(server.data_root, "conversations", "a__b", "log", "m2.json"), 90 * 86400)
    got = server.cleanup.collect_candidates()
    assert sorted(k for k, v in got.items() if v) == \
        ["pending_spawn", "responses", "session_ctrl"]
    assert len(got["session_ctrl"]) == 1
    assert got["session_ctrl"][0].endswith("e1.json")
    assert len(got["responses"]) == 1
    assert len(got["pending_spawn"]) == 1
    assert got["pending_spawn"][0].endswith("t-old.json")


def test_run_gc_deletes_only_candidates(server):
    _make_file(os.path.join(server.data_root, "session_ctrl", "e1.json"), 8 * 86400)
    _make_file(os.path.join(server.data_root, "conversations", "a__b", "pipe", "m1.json"), 90 * 86400)
    res = server.cleanup.run_gc()
    assert res["deleted"] == 1
    assert res["violations"] == []
    assert not os.path.exists(os.path.join(server.data_root, "session_ctrl", "e1.json"))
    assert os.path.exists(os.path.join(server.data_root, "conversations", "a__b", "pipe", "m1.json"))


def test_run_gc_dry_run_deletes_nothing(server):
    _make_file(os.path.join(server.data_root, "session_ctrl", "e1.json"), 8 * 86400)
    res = server.cleanup.run_gc(dry_run=True)
    assert res["dry_run"] is True
    assert res["deleted"] == 0
    assert os.path.exists(os.path.join(server.data_root, "session_ctrl", "e1.json"))


def test_run_gc_violation_guardrail(server, monkeypatch):
    """Defense in depth: even a crafted candidate that contains pipe/log
    components is skipped + reported, never deleted."""
    bad = os.path.join(server.data_root, "conversations", "a__b", "pipe", "m1.json")
    _make_file(bad, 90 * 86400)
    monkeypatch.setattr(server.cleanup, "collect_candidates",
                        lambda: {"session_ctrl": [bad]})
    res = server.cleanup.run_gc()
    assert res["violations"] == [bad]
    assert res["deleted"] == 0
    assert os.path.exists(bad)


def test_pending_marker_ttl_fresh_and_expired(server):
    fresh = os.path.join(server.paths.PENDING_SPAWN_DIR, "t1.json")
    old = os.path.join(server.paths.PENDING_SPAWN_DIR, "t2.json")
    _write_pending(server, "t1", int(time.time() * 1000))
    _write_pending(server, "t2", int((time.time() - 2 * 3600) * 1000))
    assert server.cleanup.pending_marker_expired(fresh) is False
    assert server.cleanup.pending_marker_expired(old) is True


def test_pending_marker_mtime_fallback(server):
    """Markers without created_at_ms (older producers) fall back to file
    mtime - fresh mtime stays fresh."""
    p = os.path.join(server.paths.PENDING_SPAWN_DIR, "t1.json")
    os.makedirs(server.paths.PENDING_SPAWN_DIR, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "spawn_token": "t1"}, f)
    assert server.cleanup.pending_marker_expired(p) is False   # fresh mtime
    old = time.time() - 2 * 3600
    os.utime(p, (old, old))
    assert server.cleanup.pending_marker_expired(p) is True    # old mtime


def test_gc_due_and_state(server):
    g = server.cleanup
    assert g.gc_due(None) is True
    assert g.gc_due(time.time()) is False
    assert g.gc_due(time.time() - g.GC_INTERVAL_SECONDS - 1) is True
    g.save_last_gc_run(time.time())
    assert g.maybe_run_gc() is None              # just ran -> skip
    g.save_last_gc_run(time.time() - g.GC_INTERVAL_SECONDS - 1)
    res = g.maybe_run_gc()                       # due -> runs
    assert res is not None and "deleted" in res
