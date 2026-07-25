"""HP-01: 版本化 record、唯一 message_id、单调 sequence、原子发布、双 reader。"""
import json
import os
import re
import time

import pytest


def _seq_state():
    return {"schema_version": 1, "store_id": "store-test", "last_allocated": 0}


def _send(ka, convs, seq, fromid, toid, text, mid=None):
    return ka.send_message(convs, seq, "store-test", fromid, toid, text, mid)


def _pipe_files(server, a="alice", b="bob"):
    d = server.conversations.conv_dir(a, b)
    return sorted(os.listdir(os.path.join(d, "pipe")))


def test_burst_same_ms_no_overwrite(server, monkeypatch):
    """1000 次同毫秒同向 send：无覆盖、无丢失、seq 单调、message_id 唯一。
    （1000 × 2 fsync：Windows 上约 10–30s，属预期——这是 HP-01 验收量级。）"""
    monkeypatch.setattr(time, "time", lambda: 1700000000.0)
    ka = server.kernel_api
    convs, seq = {}, _seq_state()
    ka.register_conversation(convs, "alice", "bob")
    for i in range(1000):
        r = _send(ka, convs, seq, "alice", "bob", f"m{i}")
        assert r.startswith("message_sent at ")
    files = _pipe_files(server)
    assert len(files) == 1000
    seqs, mids = set(), set()
    for f in files:
        m = re.fullmatch(r"(\d{20})__alice__bob__([0-9a-f]{32})\.json", f)
        assert m, f
        seqs.add(int(m.group(1)))
        mids.add(m.group(2))
    assert seqs == set(range(1, 1001))
    assert len(mids) == 1000
    # 信封完整
    with open(os.path.join(server.conversations.conv_dir("alice", "bob"),
                           "pipe", files[0]), encoding="utf-8") as fh:
        rec = json.load(fh)
    assert rec["schema_version"] == 1
    assert rec["store_id"] == "store-test"
    assert rec["from_session"] == "alice" and rec["to_session"] == "bob"
    assert rec["kind"] == "text" and isinstance(rec["created_at_ms"], int)
    assert set(rec["payload"]) == {"text"}


def test_clock_backward_still_sequence_ordered(server, monkeypatch):
    """时钟回拨：listen 仍按 sequence 有序（不按 created_at_ms）。"""
    ka = server.kernel_api
    convs, acked, seq = {}, {}, _seq_state()
    ka.register_conversation(convs, "alice", "bob")
    monkeypatch.setattr(time, "time", lambda: 2000.0)
    _send(ka, convs, seq, "alice", "bob", "first")
    monkeypatch.setattr(time, "time", lambda: 1000.0)  # 回拨
    _send(ka, convs, seq, "alice", "bob", "second")
    res = ka.listen_scan(acked, "bob", 0)
    assert [m["message"] for m in res["messages"]] == ["first", "second"]
    assert [m["sequence"] for m in res["messages"]] == [1, 2]
    assert all(m["store_id"] == "store-test" for m in res["messages"])


def test_reader_never_sees_partial(server):
    """遗留 tmp 文件与 malformed .json 对 reader 不可见、不崩溃。"""
    ka = server.kernel_api
    convs, acked, seq = {}, {}, _seq_state()
    ka.register_conversation(convs, "alice", "bob")
    _send(ka, convs, seq, "alice", "bob", "good")
    d = server.conversations.conv_dir("alice", "bob")
    pipe = os.path.join(d, "pipe")
    with open(os.path.join(pipe, "00000000000000000009__alice__bob__abcd.json.tmp.999"), "w") as f:
        f.write('{"partial":')
    with open(os.path.join(pipe, "not-a-record.json"), "w") as f:
        f.write("{partial")
    res = ka.listen_scan(acked, "bob", 0)
    assert [m["message"] for m in res["messages"]] == ["good"]


def test_counter_gap_never_reuse(server):
    """counter 已持久化到 41 但 41 号消息缺失（崩溃 gap）：下一条仍取 42，绝不复用。"""
    ka = server.kernel_api
    convs, seq = {}, _seq_state()
    ka.register_conversation(convs, "alice", "bob")
    _send(ka, convs, seq, "alice", "bob", "m1")  # seq 1
    seq["last_allocated"] = 41  # 模拟：counter 已推进、消息未发布
    _send(ka, convs, seq, "alice", "bob", "m2")
    files = _pipe_files(server)
    seqs = sorted(int(f.split("__")[0]) for f in files)
    assert seqs == [1, 42]


def test_sequence_self_heal_from_files(server):
    """counter 文件丢失/损坏：启动扫描现存最大 sequence 自愈。"""
    ka = server.kernel_api
    convs, seq = {}, _seq_state()
    ka.register_conversation(convs, "alice", "bob")
    _send(ka, convs, seq, "alice", "bob", "m1")  # seq 1
    _send(ka, convs, seq, "alice", "bob", "m2")  # seq 2
    os.remove(str(server.paths.MESSAGE_SEQUENCE_FILE))
    k = server.kernel
    k.message_sequence.clear()
    k._local_store_id = "store-test"
    k._load_message_sequence()
    assert k.message_sequence["last_allocated"] >= 2
    new_seq = dict(k.message_sequence)
    _send(ka, convs, new_seq, "alice", "bob", "m3")
    files = _pipe_files(server)
    seqs = sorted(int(f.split("__")[0]) for f in files)
    assert seqs == [1, 2, 3]


def test_send_dedup_by_message_id(server, monkeypatch):
    """同一 message_id 重发（retry）：不产第二条，返回原结果（HP-03 的领域幂等键）。"""
    ka = server.kernel_api
    convs, seq = {}, _seq_state()
    ka.register_conversation(convs, "alice", "bob")
    r1 = _send(ka, convs, seq, "alice", "bob", "hello", mid="m" + "0" * 31)
    r2 = _send(ka, convs, seq, "alice", "bob", "hello", mid="m" + "0" * 31)
    assert r1 == r2
    assert len(_pipe_files(server)) == 1


def test_legacy_md_dual_read(server):
    """双 reader：手工构造的 v0.3 .md 与新 .json 同见；legacy 排前（它早于升级）。"""
    ka = server.kernel_api
    convs, acked, seq = {}, {}, _seq_state()
    ka.register_conversation(convs, "alice", "bob")
    d = server.conversations.ensure_conv_dir("alice", "bob")
    with open(os.path.join(d, "pipe", "0000000000042__alice__bob.md"), "w",
              encoding="utf-8") as f:
        f.write("old-legacy")
    _send(ka, convs, seq, "alice", "bob", "new-record")
    res = ka.listen_scan(acked, "bob", 0)
    assert [m["message"] for m in res["messages"]] == ["old-legacy", "new-record"]
    legacy, record = res["messages"]
    assert legacy["sequence"] is None and legacy["message_id"] is None
    assert record["sequence"] == 1 and record["message_id"]
    # legacy 仍按 timestamp ACK 归档（v1 语义在 deprecation window 内不变）
    # record 的 created_at_ms >> 42，只用 42 不归档 record→重投。用 listen 返回的
    # watermark（=max time，覆盖全部消息）归档全部 → pipe 清空。
    res2 = ka.listen_scan(acked, "bob", res["watermark"])
    assert res2["messages"] == []
    log_files = os.listdir(os.path.join(d, "log"))
    assert "0000000000042__alice__bob.md" in log_files


def test_collect_messages_dual_read(server):
    ka = server.kernel_api
    convs, seq = {}, _seq_state()
    ka.register_conversation(convs, "alice", "bob")
    d = server.conversations.ensure_conv_dir("alice", "bob")
    with open(os.path.join(d, "pipe", "0000000000042__alice__bob.md"), "w",
              encoding="utf-8") as f:
        f.write("old")
    _send(ka, convs, seq, "alice", "bob", "new")
    out = ka.collect_messages("bob")
    assert [m["message"] for m in out] == ["old", "new"]
    assert out[1]["sequence"] == 1
