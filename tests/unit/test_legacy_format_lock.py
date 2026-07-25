"""Legacy v0.3 .md 双 reader 锁定（HP-01 deprecation window）。

writer 只写 .json record；旧 .md 必须仍可读、可按 timestamp ACK 归档。
本文件全部由手工构造的 legacy 文件驱动，不依赖旧 writer。"""
import os
import re


def _seq_state():
    return {"schema_version": 1, "store_id": "store-test", "last_allocated": 0}


def test_new_writer_format(server):
    """新 writer：.json record，文件名 <seq:020d>__from__to__<mid>.json。"""
    ka = server.kernel_api
    convs = {}
    ka.register_conversation(convs, "alice", "bob")
    r = ka.send_message(convs, _seq_state(), "store-test", "alice", "bob", "hello")
    assert r.startswith("message_sent at ")
    d = server.conversations.conv_dir("alice", "bob")
    files = os.listdir(os.path.join(d, "pipe"))
    assert len(files) == 1
    assert re.fullmatch(r"\d{20}__alice__bob__[0-9a-f]{32}\.json", files[0])


def test_legacy_md_still_readable(server):
    ka = server.kernel_api
    convs, acked = {}, {}
    ka.register_conversation(convs, "alice", "bob")
    d = server.conversations.ensure_conv_dir("alice", "bob")
    with open(os.path.join(d, "pipe", "0000000000042__alice__bob.md"), "w",
              encoding="utf-8") as f:
        f.write("legacy-body")
    res = ka.listen_scan(acked, "bob", 0)
    assert len(res["messages"]) == 1
    m = res["messages"][0]
    assert m["message"] == "legacy-body" and m["from_id"] == "alice"
    assert m["time"] == 42 and m["sequence"] is None
    assert res["watermark"] == 42
    res2 = ka.listen_scan(acked, "bob", res["watermark"])
    assert res2["messages"] == []
    assert os.listdir(os.path.join(d, "pipe")) == []
    assert os.listdir(os.path.join(d, "log")) == ["0000000000042__alice__bob.md"]
