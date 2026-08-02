"""HP-09: RESOURCE_EXHAUSTED activation (inline cap) - entry envelope + validator."""
import pytest
from result import Code

import mcp_server


def test_over_limit_entry_is_resource_exhausted(server):
    big = "x" * (server.validation.MAX_INLINE_BYTES + 1)
    r = mcp_server.send_message("a", "b", big)
    assert r["ok"] is False
    assert r["code"] == Code.RESOURCE_EXHAUSTED
    assert r["retryable"] is False
    assert r["data"] == {"limit_bytes": server.validation.MAX_INLINE_BYTES,
                         "actual_bytes": len(big.encode("utf-8"))}


def test_validate_message_size_raises_resource_exhausted(server):
    v = server.validation
    with pytest.raises(v.ResourceExhaustedError) as ei:
        v.validate_message_size("x" * (v.MAX_INLINE_BYTES + 1))
    assert ei.value.code == Code.RESOURCE_EXHAUSTED
    assert ei.value.data == {"limit_bytes": v.MAX_INLINE_BYTES,
                             "actual_bytes": v.MAX_INLINE_BYTES + 1}


def test_validate_message_size_inline_ok(server):
    v = server.validation
    assert v.validate_message_size("hi" * 100) == "hi" * 100


def test_validate_message_size_non_str_still_invalid(server):
    v = server.validation
    with pytest.raises(v.InvalidArgumentError):
        v.validate_message_size(42)


# ---------- HP-09: backpressure (per-pair unacked cap) ----------

def test_send_backlog_cap_blocks(server):
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    ka.MAX_BACKLOG = 0
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    r = ka.send_message(k.alive_conversations, {}, "store", "a", "b", "hi")
    assert r == {"sent": False, "reason": "backlog full",
                 "backlog": {"unacked": 0, "cap": 0}}


def test_send_backlog_cap_releases_after_drain(server):
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    ka.MAX_BACKLOG = 1
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    r1 = ka.send_message(k.alive_conversations, {}, "store", "a", "b", "m1")
    assert r1["sent"] is True                      # pipe 0 -> 1 (exactly at cap)
    r2 = ka.send_message(k.alive_conversations, {}, "store", "a", "b", "m2")
    assert r2["sent"] is False and r2["backlog"]["unacked"] == 1
    # drain: bob confirms -> listen_scan archives what he's acked
    res = ka.listen_scan({}, "b", r1["ts"])
    assert res["messages"] == []                  # archived, not re-delivered
    r3 = ka.send_message(k.alive_conversations, {}, "store", "a", "b", "m3")
    assert r3["sent"] is True                     # backpressure released


def test_user_functions_backlog_maps_to_resource_exhausted(server, monkeypatch):
    import user_functions
    monkeypatch.setattr(user_functions, "_conv_store", lambda toid: None)
    monkeypatch.setattr(user_functions, "_send", lambda *a, **kw: {
        "sent": False, "reason": "backlog full",
        "backlog": {"unacked": 1000, "cap": 1000}})
    r = user_functions.send_message("a", "b", "hi")
    assert r["ok"] is False and r["code"] == Code.RESOURCE_EXHAUSTED
    assert r["retryable"] is True
    assert r["data"] == {"unacked": 1000, "cap": 1000}


def test_user_functions_not_registered_still_not_found(server, monkeypatch):
    import user_functions
    monkeypatch.setattr(user_functions, "_conv_store", lambda toid: None)
    monkeypatch.setattr(user_functions, "_send", lambda *a, **kw: {
        "sent": False, "reason": "connection not registered"})
    r = user_functions.send_message("a", "b", "hi")
    assert r["ok"] is False and r["code"] == Code.NOT_FOUND
    assert r["retryable"] is False


# ---------- HP-09: backlog_stats (kernel-function observability) ----------

def test_backlog_stats_counts_per_partner(server):
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    k.alive_conversations[("a", "c")] = {"established_at": 1.0}
    ka.send_message(k.alive_conversations, {}, "store", "b", "a", "to-a-1")
    ka.send_message(k.alive_conversations, {}, "store", "b", "a", "to-a-2")
    ka.send_message(k.alive_conversations, {}, "store", "c", "a", "to-a-3")
    ka.send_message(k.alive_conversations, {}, "store", "a", "b", "to-b-1")
    stats = ka.backlog_stats("a")
    assert stats["b"]["unacked"] == 2      # to-a-1, to-a-2 (to-a-3 is from c)
    assert stats["c"]["unacked"] == 1
    assert stats["b"]["bytes"] > 0
    assert ka.backlog_stats("zzz") == {}


def test_backlog_stats_direction_only(server):
    """Only messages ADDRESSED to sid count - sid's own outgoing messages do
    not inflate its backlog."""
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    ka.send_message(k.alive_conversations, {}, "store", "a", "b", "from-a")
    assert ka.backlog_stats("a")["b"]["unacked"] == 0
    assert ka.backlog_stats("b")["a"]["unacked"] == 1


def test_dispatch_routes_backlog_stats(server):
    k = server.kernel
    res = k._dispatch("backlog_stats", {"session_id": "a"})
    assert isinstance(res, dict)
    with pytest.raises(server.validation.InvalidArgumentError):
        k._dispatch("backlog_stats", {"session_id": "../evil"})
