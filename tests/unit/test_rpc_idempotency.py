"""HP-03: operation_id 跨 retry 稳定、journal 幂等重放、领域幂等键。"""
import json
import os

import pytest


LOCAL = "store-local-a"


def _seq_state():
    return {"schema_version": 1, "store_id": LOCAL, "last_allocated": 0}


def _write_request(queue_dir, name, req):
    with open(os.path.join(str(queue_dir), name), "w", encoding="utf-8") as f:
        json.dump(req, f)


def _read_response(resp_dir, rid):
    with open(os.path.join(str(resp_dir), rid + ".json"), encoding="utf-8") as f:
        return json.load(f)


def _pipe_files(server, a="alice", b="bob"):
    d = server.conversations.conv_dir(a, b)
    return os.listdir(os.path.join(d, "pipe"))


def test_dispatch_path_roundtrip(server):
    """Carry-forward：RPC dispatch 路径测试（queue -> drain -> response）。"""
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    k.operation_journal.clear()
    _write_request(server.paths.QUEUE_DIR, "0000000000001_a.json",
                   {"request_id": "d1", "function": "register_conversation",
                    "args": {"sid_a": "alice", "sid_b": "bob"}})
    k.drain_queue()
    resp = _read_response(server.paths.QUEUE_RESPONSES_DIR, "d1")
    assert resp == {"request_id": "d1", "result": {"ok": True}, "error": None}
    assert [f for f in os.listdir(str(server.paths.QUEUE_DIR)) if f.endswith(".json")] == []
    assert ("alice", "bob") in k.alive_conversations


def test_send_retry_same_operation_id_single_delivery(server):
    """人为丢弃首个 response 后的 retry（同 operation_id、新 request_id）：
    只发一条；两次响应相同（journal 重放）。"""
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    k.alive_conversations.clear(); k.operation_journal.clear()
    k.alive_conversations[("alice", "bob")] = {}
    req = {"function": "send_message",
           "args": {"fromid": "alice", "toid": "bob", "message": "hi",
                    "message_id": "m" + "0" * 31},
           "operation_id": "op-" + "1" * 8}
    _write_request(server.paths.QUEUE_DIR, "0000000000001_a.json",
                   dict(req, request_id="r1"))
    k.drain_queue()
    resp1 = _read_response(server.paths.QUEUE_RESPONSES_DIR, "r1")
    _write_request(server.paths.QUEUE_DIR, "0000000000002_b.json",
                   dict(req, request_id="r2"))  # retry: 新 rid、同 op
    k.drain_queue()
    resp2 = _read_response(server.paths.QUEUE_RESPONSES_DIR, "r2")
    assert resp1["result"] == resp2["result"]
    assert len(_pipe_files(server)) == 1


def test_send_dedup_by_message_id_distinct_operations(server):
    """不同 operation、同 message_id（上层自己重试）：领域键去重，仍只一条。"""
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    k.alive_conversations.clear(); k.operation_journal.clear()
    k.alive_conversations[("alice", "bob")] = {}
    base = {"function": "send_message",
            "args": {"fromid": "alice", "toid": "bob", "message": "hi",
                     "message_id": "m" + "0" * 31}}
    _write_request(server.paths.QUEUE_DIR, "0000000000001_a.json",
                   dict(base, request_id="r1", operation_id="op-aaaaaaaa"))
    _write_request(server.paths.QUEUE_DIR, "0000000000002_b.json",
                   dict(base, request_id="r2", operation_id="op-bbbbbbbb"))
    k.drain_queue()
    assert len(_pipe_files(server)) == 1
    assert _read_response(server.paths.QUEUE_RESPONSES_DIR, "r1")["result"] == \
           _read_response(server.paths.QUEUE_RESPONSES_DIR, "r2")["result"]


def test_journal_survives_kernel_restart(server):
    """journal 持久化：内存清空 + 重新 load 后，同 op 仍重放不重发。"""
    server.paths.ensure_runtime_dirs()
    import operation_journal as oj
    k = server.kernel
    k.alive_conversations.clear(); k.operation_journal.clear()
    k.alive_conversations[("alice", "bob")] = {}
    req = {"function": "send_message",
           "args": {"fromid": "alice", "toid": "bob", "message": "hi",
                    "message_id": "m" + "0" * 31},
           "operation_id": "op-" + "2" * 8}
    _write_request(server.paths.QUEUE_DIR, "0000000000001_a.json",
                   dict(req, request_id="r1"))
    k.drain_queue()
    assert len(_pipe_files(server)) == 1
    k.operation_journal.clear()  # 模拟重启丢内存
    k.operation_journal.update(oj.load(str(server.paths.OPERATION_JOURNAL_FILE)))
    _write_request(server.paths.QUEUE_DIR, "0000000000002_b.json",
                   dict(req, request_id="r2"))
    k.drain_queue()
    assert len(_pipe_files(server)) == 1


def test_register_idempotent_via_journal(server):
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    k.alive_conversations.clear(); k.operation_journal.clear()
    req = {"function": "register_conversation",
           "args": {"sid_a": "alice", "sid_b": "bob"}, "operation_id": "op-reg-1"}
    _write_request(server.paths.QUEUE_DIR, "0000000000001_a.json",
                   dict(req, request_id="r1"))
    _write_request(server.paths.QUEUE_DIR, "0000000000002_b.json",
                   dict(req, request_id="r2"))
    k.drain_queue()
    assert _read_response(server.paths.QUEUE_RESPONSES_DIR, "r1")["result"] == {"ok": True}
    assert _read_response(server.paths.QUEUE_RESPONSES_DIR, "r2")["result"] == {"ok": True}
    assert list(k.alive_conversations) == [("alice", "bob")]
    assert k.operation_journal["op-reg-1"]["status"] == "completed"


def test_withdraw_by_message_id_idempotent(server):
    """按 message_id 撤回：精确目标；重复调用返回 already-done 而不是误删。"""
    server.paths.ensure_runtime_dirs()
    ka, k = server.kernel_api, server.kernel
    k.alive_conversations.clear(); k.operation_journal.clear()
    k.alive_conversations[("alice", "bob")] = {}
    seq = _seq_state()
    ka.send_message(k.alive_conversations, seq, LOCAL, "alice", "bob", "m1",
                    "a" * 32)
    ka.send_message(k.alive_conversations, seq, LOCAL, "alice", "bob", "m2",
                    "b" * 32)
    r1 = ka.withdraw(k.alive_conversations, "alice", "bob", 0, message_id="a" * 32)
    assert "withdrew" in r1["detail"]
    files = _pipe_files(server)
    assert len(files) == 1 and files[0].endswith("__" + "b" * 32 + ".json")
    r2 = ka.withdraw(k.alive_conversations, "alice", "bob", 0, message_id="a" * 32)
    assert "no message" in r2["reason"]  # already-done，不报错、不误删 m2
    assert len(_pipe_files(server)) == 1


def test_operation_id_written_to_queue_files(server, tmp_path):
    """local 与 remote 提交都携带稳定 operation_id。"""
    server.paths.ensure_runtime_dirs()
    rc = server.rpc_client
    rid = rc._submit("send_message", {"x": 1}, operation_id="op-local-1")
    files = [f for f in os.listdir(str(server.paths.QUEUE_DIR)) if f.endswith(".json")]
    assert len(files) == 1
    with open(os.path.join(str(server.paths.QUEUE_DIR), files[0]),
              encoding="utf-8") as f:
        req = json.load(f)
    assert req["operation_id"] == "op-local-1" and req["request_id"] == rid
    rqueue = tmp_path / "rqueue"
    rid2 = rc._submit_remote(str(rqueue), "send_message", {"x": 1},
                             operation_id="op-remote-1")
    files2 = os.listdir(str(rqueue))
    assert len(files2) == 1
    with open(os.path.join(str(rqueue), files2[0]), encoding="utf-8") as f:
        req2 = json.load(f)
    assert req2["operation_id"] == "op-remote-1" and req2["request_id"] == rid2


def test_call_reuses_operation_id_across_attempts(server, monkeypatch):
    """call() 的两次 attempt 复用同一 operation_id（request_id 各新）。"""
    rc = server.rpc_client  # fixture 已 reload（tmp 路径）
    seen = []
    monkeypatch.setattr(rc, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setattr(rc, "ensure_core", lambda: True)
    monkeypatch.setattr(rc, "_submit",
                        lambda fn, args, operation_id=None: seen.append(operation_id) or "rid")
    monkeypatch.setattr(rc, "_consume_response", lambda rid: None)
    monkeypatch.setattr(rc.time, "sleep", lambda s: None)
    with pytest.raises(rc.KernelError):
        rc.call("send_message", {"x": 1}, timeout=0.01, operation_id="op-stable")
    assert seen == ["op-stable", "op-stable"]
