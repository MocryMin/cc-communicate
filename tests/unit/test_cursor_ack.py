"""HP-02: per-store cursor ACK（listen_v2 + query_my_cursors）。"""
import os

import pytest


LOCAL = "store-local-a"
HOST = "store-host-b"


def _seq_state():
    return {"schema_version": 1, "store_id": LOCAL, "last_allocated": 0}


def _send3(ka, convs, seq):
    ka.register_conversation(convs, "alice", "bob")
    for t in ("m1", "m2", "m3"):
        ka.send_message(convs, seq, LOCAL, "alice", "bob", t)


def test_cursor_archives_only_acked(server):
    ka = server.kernel_api
    convs, seq = {}, _seq_state()
    _send3(ka, convs, seq)
    cur = {}
    r = ka.listen_scan_v2(cur, LOCAL, "bob", 2)
    assert [m["payload"]["text"] for m in r["messages"]] == ["m3"]
    assert r["next_cursor"] == 3 and r["store_id"] == LOCAL
    d = server.conversations.conv_dir("alice", "bob")
    log_files = os.listdir(os.path.join(d, "log"))
    assert len(log_files) == 2  # seq 1,2 已归档
    assert len(os.listdir(os.path.join(d, "pipe"))) == 1
    assert cur == {"bob": {LOCAL: 2}}


def test_cursor_state_per_store_independent(server):
    """ACK store A 不影响 store B 的 cursor 记录。"""
    ka = server.kernel_api
    convs, seq = {}, _seq_state()
    _send3(ka, convs, seq)
    cur = {"bob": {HOST: 99}}  # 另一个 store 的既有 cursor
    ka.listen_scan_v2(cur, LOCAL, "bob", 2)
    assert cur["bob"] == {HOST: 99, LOCAL: 2}


def test_cancel_redelivery_v2(server):
    """不推进 cursor（调用方取消）：本批消息下次原样重投，且不归档。"""
    ka = server.kernel_api
    convs, seq = {}, _seq_state()
    _send3(ka, convs, seq)
    cur = {}
    r1 = ka.listen_scan_v2(cur, LOCAL, "bob", 0)
    assert len(r1["messages"]) == 3
    d = server.conversations.conv_dir("alice", "bob")
    assert len(os.listdir(os.path.join(d, "pipe"))) == 3  # 未确认不归档
    r2 = ka.listen_scan_v2(cur, LOCAL, "bob", 0)
    assert [m["message_id"] for m in r2["messages"]] == \
           [m["message_id"] for m in r1["messages"]]


def test_upload_cursor_idempotent_no_regress(server):
    ka = server.kernel_api
    cur = {}
    ka.upload_cursor(cur, LOCAL, "bob", 5)
    ka.upload_cursor(cur, LOCAL, "bob", 5)
    r = ka.upload_cursor(cur, LOCAL, "bob", 3)  # 更小不得回退
    assert r == {LOCAL: 5}
    assert cur == {"bob": {LOCAL: 5}}


def test_cursor_restart_recovery(server):
    server.paths.ensure_runtime_dirs()
    ka = server.kernel_api
    k = server.kernel
    cur = k.cursors
    cur.clear()
    ka.upload_cursor(cur, LOCAL, "bob", 7)  # 立即持久化
    cur.clear()  # 模拟 kernel 重启丢内存
    k._load_cursors()
    assert k.cursors == {"bob": {LOCAL: 7}}


def test_legacy_md_invisible_to_v2(server):
    """显式迁移点：legacy .md 不进 v2；v1 listen 仍可见（deprecation window）。"""
    ka = server.kernel_api
    convs = {}
    ka.register_conversation(convs, "alice", "bob")
    d = server.conversations.ensure_conv_dir("alice", "bob")
    with open(os.path.join(d, "pipe", "0000000000042__alice__bob.md"), "w",
              encoding="utf-8") as f:
        f.write("old")
    cur = {}
    r = ka.listen_scan_v2(cur, LOCAL, "bob", 0)
    assert r["messages"] == [] and r["next_cursor"] == 0
    acked = {}
    r1 = ka.listen_scan(acked, "bob", 0)
    assert [m["message"] for m in r1["messages"]] == ["old"]


def test_dispatch_listen_scan_v2(server):
    """dispatch 路由 + _local_store_id 注入 + 验证表。"""
    server.paths.ensure_runtime_dirs()
    ka, k = server.kernel_api, server.kernel
    k._local_store_id = LOCAL
    k.cursors.clear()
    convs = k.alive_conversations
    convs.clear()
    ka.register_conversation(convs, "alice", "bob")
    ka.send_message(convs, {"schema_version": 1, "store_id": LOCAL,
                            "last_allocated": 0}, LOCAL, "alice", "bob", "hi")
    import json
    with open(os.path.join(str(server.paths.QUEUE_DIR), "0000000000001_q.json"),
              "w", encoding="utf-8") as f:
        json.dump({"request_id": "r9", "function": "listen_scan_v2",
                   "args": {"sid": "bob", "cursor": 0}}, f)
    k.drain_queue()
    with open(os.path.join(str(server.paths.QUEUE_RESPONSES_DIR), "r9.json"),
              encoding="utf-8") as f:
        resp = json.load(f)
    assert resp["error"] is None
    assert resp["result"]["store_id"] == LOCAL
    assert [m["payload"]["text"] for m in resp["result"]["messages"]] == ["hi"]


# ---------- user_functions 合并路由（monkeypatch 双层 rpc） ----------

def test_listen_v2_merges_stores_without_mixing_cursors(server, monkeypatch):
    """ACK store A 不推进 B；两个 store 各拿各的 cursor；消息合并返回。"""
    import importlib
    uf = importlib.import_module("user_functions")
    calls = {}

    def fake_call(function, args, **kw):
        calls["local"] = (function, dict(args))
        assert args["cursor"] == 5  # local store 的 cursor
        return {"store_id": LOCAL, "next_cursor": 6,
                "messages": [{"sequence": 6, "store_id": LOCAL, "message_id": "x1",
                              "from_session": "alice", "to_session": "bob",
                              "kind": "text", "correlation_id": None,
                              "causation_id": None, "created_at_ms": 100,
                              "payload": {"text": "local-msg"}}]}

    def fake_remote(machine, function, args, **kw):
        calls["host"] = (function, dict(args))
        assert args["cursor"] == 0  # host store 无 cursor 记录 -> 0
        return {"store_id": HOST, "next_cursor": 0, "messages": []}

    monkeypatch.setattr(uf.rpc_client, "call", fake_call)
    monkeypatch.setattr(uf.rpc_client, "call_remote", fake_remote)
    monkeypatch.setattr(uf, "_host_entry", lambda: {"id": HOST, "type": "win-host"})
    monkeypatch.setattr(uf.machine_identity, "load_or_create",
                        lambda: {"id": LOCAL, "type": "wsl-test"})
    r = uf.listen_v2("bob", {LOCAL: 5}, timeout=1)
    assert r["ok"] is True
    assert [m["payload"]["text"] for m in r["data"]["messages"]] == ["local-msg"]
    assert r["data"]["next_cursors"] == {LOCAL: 6}  # HOST 未被推进（无消息、无记录）
    assert calls["local"][0] == "listen_scan_v2" and calls["host"][0] == "listen_scan_v2"


def test_query_my_cursors_merges(server, monkeypatch):
    import importlib
    uf = importlib.import_module("user_functions")
    monkeypatch.setattr(uf.rpc_client, "call",
                        lambda f, a, **kw: {LOCAL: 6} if f == "query_cursors" else None)
    monkeypatch.setattr(uf.rpc_client, "call_remote",
                        lambda m, f, a, **kw: {HOST: 3} if f == "query_cursors" else None)
    monkeypatch.setattr(uf, "_host_entry", lambda: {"id": HOST, "type": "win-host"})
    monkeypatch.setattr(uf.machine_identity, "load_or_create",
                        lambda: {"id": LOCAL, "type": "wsl-test"})
    r = uf.query_my_cursors("bob")
    assert r["ok"] is True and r["data"] == {LOCAL: 6, HOST: 3}


def test_close_connection_uploads_cursors_per_store(server, monkeypatch):
    import importlib
    uf = importlib.import_module("user_functions")
    sent = []
    monkeypatch.setattr(uf.rpc_client, "call",
                        lambda f, a, **kw: sent.append(("local", f, dict(a))) or {})
    monkeypatch.setattr(uf.rpc_client, "call_remote",
                        lambda m, f, a, **kw: sent.append(("host", f, dict(a))) or {})
    monkeypatch.setattr(uf.rpc_client, "submit_remote_noblock",
                        lambda m, f, a=None, **kw: None)
    monkeypatch.setattr(uf, "_host_entry", lambda: {"id": HOST, "type": "win-host"})
    monkeypatch.setattr(uf, "_conv_store", lambda toid: None)
    monkeypatch.setattr(uf.machine_identity, "load_or_create",
                        lambda: {"id": LOCAL, "type": "wsl-test"})
    uf.close_connection("bob", "alice", cursors={LOCAL: 6, HOST: 3, "unknown-store": 1})
    uploads = [s for s in sent if s[1] == "upload_cursor"]
    assert ("local", "upload_cursor", {"sid": "bob", "seq": 6}) in uploads
    assert ("host", "upload_cursor", {"sid": "bob", "seq": 3}) in uploads
    assert all(s[2].get("seq") != 1 for s in uploads)  # unknown store 被忽略
