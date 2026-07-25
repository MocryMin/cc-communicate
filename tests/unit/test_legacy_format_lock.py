"""Carry-forward：在 HP-01 改格式前，收紧对 v0.3 消息格式的断言（回归基线）。

Task 2 会把 writer 切成 .json record；届时本文件改为「手工构造 legacy 文件测
双 reader」。本任务的版本锁定 CURRENT writer 行为。"""
import os
import re


def test_v03_writer_format_locked(server):
    ka = server.kernel_api
    convs = {}
    ka.register_conversation(convs, "alice", "bob")
    r = ka.send_message(convs, "alice", "bob", "hello")
    assert r.startswith("message_sent at ")
    d = server.conversations.conv_dir("alice", "bob")
    files = os.listdir(os.path.join(d, "pipe"))
    assert len(files) == 1
    assert re.fullmatch(r"\d{13}__alice__bob\.md", files[0]), files[0]
    with open(os.path.join(d, "pipe", files[0]), encoding="utf-8") as f:
        assert f.read() == "hello"  # 纯文本，无信封


def test_v03_listen_scan_message_shape_locked(server):
    ka = server.kernel_api
    convs, acked = {}, {}
    ka.register_conversation(convs, "alice", "bob")
    ka.send_message(convs, "alice", "bob", "hello")
    res = ka.listen_scan(acked, "bob", 0)
    assert len(res["messages"]) == 1
    m = res["messages"][0]
    assert set(m.keys()) == {"time", "from_id", "message"}
    assert m["from_id"] == "alice" and m["message"] == "hello"
    assert res["watermark"] == m["time"]
