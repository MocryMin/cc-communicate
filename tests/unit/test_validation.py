"""HP-06: 集中验证层 + 路径约束 + destructive target 校验。"""
import os

import pytest


# ---------- id 验证 ----------

GOOD_IDS = ["alice", "bob-1", "81e4c033-6720-4763-b45f-decdf75fa3ef", "A" * 128]
BAD_IDS = ["", "../x", "/abs/path", "C:\\x", "a__b", "a.b", "a/b", "a\\b",
           "a\x00b", "a\x1fb", "A" * 129, None, 123, " ", "-", "a" * 0]


def test_validate_session_id_accepts_legit(server):
    v = server.validation
    for sid in GOOD_IDS:
        assert v.validate_session_id(sid) == sid


def test_validate_session_id_rejects_bad(server):
    v = server.validation
    for bad in BAD_IDS:
        with pytest.raises(v.InvalidArgumentError) as ei:
            v.validate_session_id(bad)
        assert str(ei.value).startswith("INVALID_ARGUMENT: ")


def test_conv_dir_enforces_validation(server):
    conv = server.conversations
    with pytest.raises(server.validation.InvalidArgumentError):
        conv.conv_dir("../evil", "bob")
    with pytest.raises(server.validation.InvalidArgumentError):
        conv.pipe_filename("alice", "a__b", 1)
    d = conv.conv_dir("alice", "bob")
    assert os.path.basename(d) == "alice__bob"
    assert os.path.dirname(os.path.abspath(d)) == os.path.abspath(
        str(server.paths.CONVERSATIONS_DIR))


def test_message_size_cap(server, monkeypatch):
    v = server.validation
    monkeypatch.setattr(v, "MAX_INLINE_BYTES", 8)
    assert v.validate_message_size("x" * 8) == "x" * 8
    with pytest.raises(v.InvalidArgumentError):
        v.validate_message_size("x" * 9)
    with pytest.raises(v.InvalidArgumentError):
        v.validate_message_size(123)


def test_validate_cwd_chinese_space_ok(server, tmp_path):
    v = server.validation
    d = tmp_path / "研究生 实习"
    d.mkdir()
    assert v.validate_cwd(str(d)) == str(d)
    with pytest.raises(v.InvalidArgumentError):
        v.validate_cwd("relative/path")
    with pytest.raises(v.InvalidArgumentError):
        v.validate_cwd(str(tmp_path / "nonexistent-dir"))


def test_resolve_under_containment(server, tmp_path):
    v = server.validation
    root = str(tmp_path)
    assert v.resolve_under(root, "a", "b").startswith(os.path.realpath(root))
    with pytest.raises(v.InvalidArgumentError):
        v.resolve_under(root, "..", "escape")
    with pytest.raises(v.InvalidArgumentError):
        v.resolve_under(root)


# ---------- destructive 操作 containment ----------

def test_withdraw_init_connect_containment(server):
    """withdraw(init_connect=1) 只删 canonical pair 目录；非法 id 在 rmtree 前被拒；
    CONVERSATIONS_DIR 根绝不被删。"""
    ka = server.kernel_api
    convs = {}
    ka.register_conversation(convs, "alice", "bob")
    d = server.conversations.ensure_conv_dir("alice", "bob")
    with open(os.path.join(d, "pipe", "0000000000001__alice__bob.md"), "w") as f:
        f.write("hi")
    r = ka.withdraw(convs, "alice", "bob", 1)
    assert r == {"withdrawn": True, "detail": "conversation withdrawn"}
    assert not os.path.isdir(d)
    assert os.path.isdir(str(server.paths.CONVERSATIONS_DIR))  # 根还在
    # 非法 id：raise，且根目录毫发无损
    for bad in ("..", "a__b", "../.."):
        with pytest.raises(server.validation.InvalidArgumentError):
            ka.withdraw(convs, bad, "bob", 1)
    assert os.path.isdir(str(server.paths.CONVERSATIONS_DIR))


def test_fuzz_no_escape(server):
    """fuzz：一堆恶意 id 走 send/register/withdraw，全部拒绝且 data root 无新增目录。"""
    ka = server.kernel_api
    convs = {}
    before = set(os.listdir(str(server.data_root))) if os.path.isdir(str(server.data_root)) else set()
    for bad in BAD_IDS:
        if not isinstance(bad, str) or not bad:
            continue
        try:
            # HP-01 signature: convs, seq, store_id, fromid, toid, text
            ka.send_message(convs, {"schema_version":1,"store_id":"t","last_allocated":0}, "t", bad, "bob", "x")
        except server.validation.InvalidArgumentError:
            pass
        try:
            ka.register_conversation(convs, bad, "bob")
        except server.validation.InvalidArgumentError:
            pass
        try:
            ka.withdraw(convs, bad, "bob", 1)
        except server.validation.InvalidArgumentError:
            pass
    # send 对未注册 pair 返回失败串而不是 raise；这里关注的是没有路径逃逸
    conv_root = str(server.paths.CONVERSATIONS_DIR)
    if os.path.isdir(conv_root):
        for name in os.listdir(conv_root):
            assert "__" in name and ".." not in name and "/" not in name
    after = set(os.listdir(str(server.data_root))) if os.path.isdir(str(server.data_root)) else set()
    assert after - before <= {"conversations"}  # 只允许 ensure_conv_dir 的合法创建


# ---------- dispatch 信任边界（remote RPC 也经此） ----------

def _write_request(queue_dir, req):
    import json
    name = "0000000000001_testreq.json"
    with open(os.path.join(str(queue_dir), name), "w", encoding="utf-8") as f:
        json.dump(req, f)
    return name


def test_dispatch_rejects_invalid_args(server):
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    _write_request(server.paths.QUEUE_DIR,
                   {"request_id": "r1", "function": "send_message",
                    "args": {"fromid": "../evil", "toid": "bob", "message": "x"}})
    k.drain_queue()
    resp_path = os.path.join(str(server.paths.QUEUE_RESPONSES_DIR), "r1.json")
    import json
    with open(resp_path, encoding="utf-8") as f:
        resp = json.load(f)
    assert resp["result"] is None
    assert "INVALID_ARGUMENT" in resp["error"]
    assert [f for f in os.listdir(str(server.paths.QUEUE_DIR))
            if f.endswith(".json")] == []  # 请求已消费


def test_dispatch_passes_valid_args(server):
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    _write_request(server.paths.QUEUE_DIR,
                   {"request_id": "r2", "function": "register_conversation",
                    "args": {"sid_a": "alice", "sid_b": "bob"}})
    k.drain_queue()
    import json
    with open(os.path.join(str(server.paths.QUEUE_RESPONSES_DIR), "r2.json"),
              encoding="utf-8") as f:
        resp = json.load(f)
    assert resp == {"request_id": "r2", "result": {"ok": True}, "error": None}


# ---------- spawn entry (create_collaborator) ----------

def test_spawn_entry_peer_cwd_deferred(server):
    """machine given -> peer-perspective cwd must NOT be validated on this
    machine (the peer kernel validates it). T31."""
    v = server.validation
    assert v.validate_spawn_entry("alice", "/home/wsl/project",
                                  {"id": "wsl-1", "type": "wsl-ubuntu"}) is None
    assert v.validate_spawn_entry("alice", "//wsl.localhost/Ubuntu/home/x",
                                  {"id": "wsl-1"}) is None


def test_spawn_entry_local_cwd_still_validated(server):
    """machine None -> the cwd is local and MUST be host-absolute + existing."""
    v = server.validation
    assert v.validate_spawn_entry("alice", "/home/wsl/project", None) is not None
    assert v.validate_spawn_entry("alice", "relative/path", None) is not None
    assert v.validate_spawn_entry("alice", str(server.paths.DATA_DIR), None) is None


def test_spawn_entry_sid_always_validated(server):
    """caller_sid is validated regardless of machine."""
    v = server.validation
    for bad in ("../evil", "a__b", ""):
        assert v.validate_spawn_entry(bad, "C:\\whatever", {"id": "wsl-1"}) is not None


def test_validate_bool(server):
    v = server.validation
    assert v.validate_bool(True) is True
    assert v.validate_bool(False) is False
    with pytest.raises(v.InvalidArgumentError):
        v.validate_bool("yes")
