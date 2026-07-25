# Wave 1 — 正确性核心（HP-06 → HP-01 → HP-02 → HP-03）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除三类已坐实的正确性风险——非法输入直达路径/删除（C5）、同毫秒覆盖/时钟回拨/partial write（PB-1/2/3、C1）、retry 重复副作用（C6）——落地：集中验证层 + 版本化消息记录（message_id + per-store sequence + 原子发布）+ per-store cursor ACK（listen_v2）+ operation_id/journal 幂等。

**Architecture:** 单线程 kernel 是所有 mutation 的汇聚点，天然是 sequence allocator 与 journal 持有者。新增 5 个小模块：`result.py`（错误码枚举，D7）、`validation.py`（唯一验证层，HP-06）、`fileutil.py`（原子写 + fsync 原语，C1）、`message_record.py`（信封 schema + 原子发布，HP-01/D6）、`operation_journal.py`（幂等 journal，HP-03）。除 result/validation 外，**新模块全部纯化**（路径经参数注入，不 import paths），故 conftest fixture 无需为新模块加 reload。消息文件双格式并存：writer 只写新 `<seq>__<from>__<to>__<mid>.json`，reader 双读旧 `.md` + 新 `.json`（deprecation window ≥1 release）。

**Tech Stack:** Python 3.10+，pytest，psutil（既有）。无新依赖。

**Repo layout（相对 `<repo>` = 仓库根）：**
- 被测/被改源码：`v2_win/cc-communicate/server/` + **同步** `v2_wsl/cc-communicate/server/`（parity gate 强制等价）
- 测试树：`<repo>/tests/`（repo 级，不在 plugin 树内）
- parity 工具：`<repo>/tools/check_parity.py`
- 文档：`v2_*/cc-communicate/skills/cc-communicate/SKILL.md`（plugin 树内，双域同步）

## Global Constraints

- **双域同步**：凡改动 `v2_win/cc-communicate/` 内文件，同一改动必须落到 `v2_wsl/cc-communicate/`；每个 Task 收尾跑 `py -3 tools/check_parity.py` 必须 PASS。
- **测试隔离**：测试绝不写任一真实 plugin `data/`——全部经 `CC_COMMUNICATE_DATA_DIR` 隔离到 `tmp_path`（Gate 0 既有机制）。
- **Python 调用**：用 `py -3`（Windows 上 `python`/`python3` 是坏 Store stub）。从 `<repo>` 根跑 `py -3 -m pytest`。
- **TDD**：每个 Task 先写失败测试（RED），再实现（GREEN），提交前全套件绿。
- **legacy 兼容**：旧 `.md` 消息可读至少一个 release；旧 `listen`/`query_my_ACK_timestamp` 保留并标记 legacy；`send_message` 返回串保持 `"message_sent at <ts>"` 前缀（`connect` 用 `rsplit("at ",1)` 解析它）。
- **新模块纯化**：`fileutil.py`/`message_record.py`/`operation_journal.py` 不得 import paths/conversations；所有路径经函数参数注入（fixture 隔离因此成立）。
- **已确认签名（实现者直接照用，源自代码核实）**：
  - `kernel_api.register_conversation(alive_conversations: dict, sid_a: str, sid_b: str)`
  - `kernel_api.send_message(alive_conversations, fromid, toid, message) -> str`（Task 2 改签名，见 Task 2）
  - `kernel_api.listen_scan(acked_timestamps, sid, acked_ts) -> {"messages":[{"time","from_id","message"}...], "watermark": int}`
  - `conversations.conv_dir(sid_a, sid_b)`、`conversations.SEP == "__"`、`conversations.parse_pipe_filename(name) -> (ts, fromid, toid) | None`
  - `kernel._dispatch(function, args)`、`kernel.drain_queue()`；模块级 state：`kernel.sessions`、`kernel.alive_conversations`、`kernel.acked_timestamps`
  - `machine_identity.load_or_create() -> {"type","id","claude_bin"}`
  - conftest `server` fixture：暴露 `server.paths/server.conversations/server.kernel_api/server.kernel/...` + `server.data_root`
- **T# 记录**：执行中若发现新 bug，按惯例记入 `tested&2betest.md` §1。
- **提交规范**：`feat(HP-xx): ...` / `test(HP-xx): ...` / `fix: ...`，每个 Task 1–3 个 commit。

---

### Task 1: HP-06 验证层 + result.py 错误码 + 测试基建吸收项

**Files:**
- Create: `v2_win/cc-communicate/server/result.py` + `v2_wsl/...`（下同，凡 server 文件均双域）
- Create: `v2_win/cc-communicate/server/validation.py`
- Modify: `v2_win/cc-communicate/server/conversations.py`（conv_dir/pipe_filename 强制验证）
- Modify: `v2_win/cc-communicate/server/kernel.py`（dispatch 验证表）
- Modify: `v2_win/cc-communicate/server/kernel_api.py`（withdraw containment）
- Modify: `v2_win/cc-communicate/server/mcp_server.py`（入口验证）
- Modify: `v2_win/cc-communicate/server/proc.py:54`（except 加 AttributeError，carry-forward）
- Modify: `tests/conftest.py`（reload 列表 += result/validation/proc/machine_identity，carry-forward）
- Modify: `tools/check_parity.py`（non-allowlisted compared==0 拒绝，carry-forward）
- Modify: `tests/parity/test_parity.py`（only-allowlisted 负例，carry-forward）
- Create: `tests/unit/test_validation.py`
- Create: `tests/unit/test_legacy_format_lock.py`
- Create: `tests/unit/test_session_ctrl_end_replay.py`

**Interfaces:**
- Consumes: 现有 conversations/kernel/kernel_api/mcp_server/proc。
- Produces（后续任务依赖的精确名字）:
  - `result.Code.INVALID_ARGUMENT`（等 7 个码）、`result.ok(data)`、`result.err(code, message, data=None)`
  - `validation.InvalidArgumentError(ValueError)`（str(e) 以 `"INVALID_ARGUMENT: "` 开头）
  - `validation.validate_session_id/validate_message_id/validate_operation_id/validate_store_id(value) -> str`（失败 raise）
  - `validation.validate_message_size(message) -> str`、`validation.MAX_INLINE_BYTES`（默认 1 MiB，env `CC_COMMUNICATE_MAX_INLINE_BYTES`）
  - `validation.validate_cwd(value) -> str`、`validation.validate_cursors(value) -> dict`（Task 3 消费）
  - `validation.resolve_under(root, *parts) -> str`
  - `conversations.conv_dir/pipe_filename` 对非法 id raise `InvalidArgumentError`

- [ ] **Step 1: 写失败测试（3 个新测试文件）**

`tests/unit/test_validation.py`:
```python
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
    assert r == "conversation withdrawn"
    assert not os.path.isdir(d)
    assert os.path.isdir(str(server.paths.CONVERSATIONS_DIR))  # 根还在
    # 非法 id：raise，且根目录毫发无损
    for bad in ("..", "a__b", "../..", "conversations"):
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
            ka.send_message(convs, bad, "bob", "x")
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
    assert os.listdir(str(server.paths.QUEUE_DIR)) == []  # 请求已消费


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
    assert resp == {"request_id": "r2", "result": "ok", "error": None}
```

`tests/unit/test_legacy_format_lock.py`:
```python
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
```

`tests/unit/test_session_ctrl_end_replay.py`:
```python
"""Carry-forward：end 事件回放（完整 session 生命周期 start -> end -> restart）。"""
import json
import os


def _write_event(ctrl_dir, name, ev):
    with open(os.path.join(str(ctrl_dir), name), "w", encoding="utf-8") as f:
        json.dump(ev, f)


def test_end_event_replay_lifecycle(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    start = {"event": "start", "event_ts": 1000, "session_id": "sess-1",
             "pid": 4242, "cwd": "/tmp/x", "start_time": "2026-07-24T10:00:00",
             "source": "hook"}
    end = {"event": "end", "event_ts": 2000, "session_id": "sess-1", "pid": 4242}
    _write_event(server.paths.SESSION_CTRL_DIR, "0000000001000_start.json", start)
    k._seen_events.clear(); k.sessions.clear(); k.alive_sessions.clear()
    k.process_session_ctrl_event()
    assert "sess-1" in k.alive_sessions
    _write_event(server.paths.SESSION_CTRL_DIR, "0000000002000_end.json", end)
    k.process_session_ctrl_event()
    assert "sess-1" not in k.alive_sessions
    assert k.sessions["sess-1"]["ended_at"] == 2000
    # restart：清空内存，从 sessions.json 恢复，ended_at 持久
    k.sessions.clear(); k.alive_sessions.clear(); k._seen_events.clear()
    k._load_sessions()
    assert k.sessions["sess-1"]["ended_at"] == 2000
    assert "sess-1" not in k.alive_sessions
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3 -m pytest tests/unit/test_validation.py tests/unit/test_legacy_format_lock.py tests/unit/test_session_ctrl_end_replay.py -v`
Expected: 多数 FAIL——`server` fixture 的 reload 列表没有 `validation`/`result`（AttributeError: SimpleNamespace has no attribute 'validation'）；`test_dispatch_rejects_invalid_args` 中响应是 result 而非 INVALID_ARGUMENT error；`test_v03_*` 中部分断言（keys 集合、文件名 regex）可能已过——以实际输出为准记录 RED 证据。

- [ ] **Step 3: 新建 `server/result.py`（双域）**

```python
"""Structured result/error codes (D7; minimal Wave 1 form).

Wave 1 lands the code enum + minimal ok/err constructors so HP-06 validation
failures carry a stable, machine-checkable code. The full response envelope
(arrives in Wave 2 / HP-07) will wrap every tool result; until then tools keep
returning legacy strings/dicts, and validation failures surface as error
strings prefixed with the code ("INVALID_ARGUMENT: ...") so callers can branch
on the code prefix WITHOUT parsing natural language.
"""
from __future__ import annotations


class Code:
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    PEER_UNREACHABLE = "PEER_UNREACHABLE"
    TIMEOUT = "TIMEOUT"
    CONFLICT = "CONFLICT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    INTERNAL = "INTERNAL"


def ok(data=None) -> dict:
    return {"ok": True, "code": None, "data": data}


def err(code: str, message: str, data=None) -> dict:
    return {"ok": False, "code": code, "message": message, "data": data}
```

- [ ] **Step 4: 新建 `server/validation.py`（双域）**

```python
"""Single validation layer for all external input (HP-06).

Rules:
  - Failure RAISES InvalidArgumentError (code INVALID_ARGUMENT) - we NEVER
    silently sanitize into another valid id (two invalid ids could then map to
    the same path).
  - Invoked at BOTH trust boundaries: MCP tool entry (mcp_server) and kernel
    dispatch (kernel._dispatch - which also covers remote RPC requests, since
    a peer's call_remote lands in this same queue).
  - conversations.conv_dir/pipe_filename enforce session-id validation as the
    deepest defense: no path is ever constructed from an unvalidated id.

Session ids: real CC session ids are UUIDs; synthetic/test ids are restricted
slugs. Both fit ^[A-Za-z0-9-]{1,128}$ - no underscores (so SEP '__' can never
appear inside an id), no slashes, no dots, no control chars. cwd gets NO
character whitelist (real cwds contain CJK chars and spaces - this repo's own
path does) - only absolute + existing-directory checks.
"""
from __future__ import annotations

import os
import re

from result import Code

MAX_ID_LEN = 128
# At least one alphanumeric (so "-" / "---" are rejected), then only
# [A-Za-z0-9-] up to the cap - no underscores, slashes, dots, control chars.
_ID_RE = re.compile(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9-]{1,128}$")
# Inline payload cap (D5 value, enforced at send entry; HP-09 owns the rest of
# the resource policy in Wave 3).
MAX_INLINE_BYTES = int(os.environ.get("CC_COMMUNICATE_MAX_INLINE_BYTES",
                                      str(1024 * 1024)))


class InvalidArgumentError(ValueError):
    """Raised by every validator. drain_queue serializes type+message into the
    RPC error channel, so clients see 'InvalidArgumentError: INVALID_ARGUMENT: ...'."""
    code = Code.INVALID_ARGUMENT

    def __init__(self, message: str):
        super().__init__(f"{self.code}: {message}")


def _check_id(value, kind: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise InvalidArgumentError(
            f"{kind} must be 1-{MAX_ID_LEN} chars of [A-Za-z0-9-] with at "
            f"least one alphanumeric (no underscores, slashes, dots or "
            f"control chars); got {value!r}")
    return value


def validate_session_id(value) -> str:
    return _check_id(value, "session_id")


def validate_message_id(value) -> str:
    return _check_id(value, "message_id")


def validate_operation_id(value) -> str:
    return _check_id(value, "operation_id")


def validate_store_id(value) -> str:
    return _check_id(value, "store_id")


def validate_message_size(message) -> str:
    if not isinstance(message, str):
        raise InvalidArgumentError(
            f"message must be a str; got {type(message).__name__}")
    n = len(message.encode("utf-8"))
    if n > MAX_INLINE_BYTES:
        raise InvalidArgumentError(
            f"message is {n} bytes, over the {MAX_INLINE_BYTES}-byte inline cap "
            f"(CC_COMMUNICATE_MAX_INLINE_BYTES); use artifact_refs instead")
    return message


def validate_cwd(value) -> str:
    """Absolute + existing directory. NO character whitelist (CJK/space ok)."""
    if not isinstance(value, str) or not value:
        raise InvalidArgumentError(f"cwd must be a non-empty str; got {value!r}")
    if not os.path.isabs(value):
        raise InvalidArgumentError(f"cwd must be absolute; got {value!r}")
    if not os.path.isdir(value):
        raise InvalidArgumentError(
            f"cwd is not an existing directory; got {value!r}")
    return value


def validate_cursors(value) -> dict:
    """Per-store cursor map {store_id: sequence:int>=0} (consumed by HP-02)."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidArgumentError(
            f"cursors must be a dict of store_id->sequence; "
            f"got {type(value).__name__}")
    out = {}
    for k, v in value.items():
        validate_store_id(k)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise InvalidArgumentError(
                f"cursor for store {k!r} must be an int >= 0; got {v!r}")
        out[k] = v
    return out


def resolve_under(root: str, *parts: str) -> str:
    """Join + realpath; the result MUST stay strictly under root."""
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(base, *parts))
    if target == base or not target.startswith(base + os.sep):
        raise InvalidArgumentError(
            f"resolved path {target!r} escapes its allowed root {base!r}")
    return target
```

- [ ] **Step 5: conversations.py 强制验证（双域）**

`conversations.py` 顶部 import 区加：
```python
from validation import validate_session_id
```
`conv_dir` 改为：
```python
def conv_dir(sid_a: str, sid_b: str) -> str:
    """Canonical conversation directory for the pair (order-independent: the
    two sids are sorted before joining). Validates both ids (HP-06) - no path
    is ever built from an unvalidated id."""
    a, b = sorted([validate_session_id(sid_a), validate_session_id(sid_b)])
    return os.path.join(CONVERSATIONS_DIR, a + SEP + b)
```
`pipe_filename` 改为：
```python
def pipe_filename(fromid: str, toid: str, ts: int) -> str:
    """Filename for a pipe message: <ts:013d>__<fromid>__<toid>.md.
    ts-first so lex sort = chronological. Validates ids (HP-06)."""
    validate_session_id(fromid)
    validate_session_id(toid)
    return f"{int(ts):013d}{SEP}{fromid}{SEP}{toid}.md"
```
注意：`parse_pipe_filename`/`count_undelivered` 保持宽容（reader 不是信任边界，malformed 返回 None/跳过）。

- [ ] **Step 6: kernel.py dispatch 验证表（双域）**

`kernel.py` import 区加 `import validation`。`_dispatch` 函数开头加：
```python
# HP-06: per-function arg validators applied at the dispatch trust boundary.
# Covers local AND remote RPC (a peer's call_remote lands in this same queue).
# Validators run only on args that are present and non-None; required-ness is
# still enforced by the args["..."] lookups below.
_ARG_VALIDATORS = {
    "query_session": {"session_id": validation.validate_session_id},
    "check_alive": {"session_id": validation.validate_session_id},
    "query_conversations": {"session_id": validation.validate_session_id},
    "send_message": {"fromid": validation.validate_session_id,
                     "toid": validation.validate_session_id,
                     "message": validation.validate_message_size},
    "register_conversation": {"sid_a": validation.validate_session_id,
                              "sid_b": validation.validate_session_id},
    "unregister_conversation": {"sid_a": validation.validate_session_id,
                                "sid_b": validation.validate_session_id},
    "withdraw": {"fromid": validation.validate_session_id,
                 "toid": validation.validate_session_id},
    "evoke": {"session_id": validation.validate_session_id},
    "collect_messages": {"session_id": validation.validate_session_id},
    "listen_scan": {"sid": validation.validate_session_id},
    "query_ack_timestamp": {"sid": validation.validate_session_id},
    "upload_ack_timestamp": {"sid": validation.validate_session_id},
    "spawn_cc_new": {"cwd": validation.validate_cwd},
    "spawn_cc_resume": {"session_id": validation.validate_session_id,
                        "cwd": validation.validate_cwd},
    "create_conversation_folder": {"id1": validation.validate_session_id,
                                   "id2": validation.validate_session_id},
}


def _validate_args(function: str, args: dict):
    for arg, validator in _ARG_VALIDATORS.get(function, {}).items():
        if arg in args and args[arg] is not None:
            validator(args[arg])
```
`_dispatch` 第一行（`if function == "query_session":` 之前）插入：
```python
    _validate_args(function, args)
```

- [ ] **Step 7: kernel_api.withdraw containment（双域）**

`kernel_api.py` import 区加 `import validation`。`withdraw` 的 `init_connect` 分支改为：
```python
def withdraw(alive_conversations: dict, fromid: str, toid: str, init_connect: int = 0) -> str:
    if init_connect:
        # HP-06 destructive containment: conv_dir already validated both ids;
        # re-verify the resolved target is strictly under CONVERSATIONS_DIR and
        # IS the canonical pair dir before rmtree.
        d = conversations.conv_dir(fromid, toid)
        target = validation.resolve_under(CONVERSATIONS_DIR, os.path.basename(d))
        if os.path.isdir(target):
            shutil.rmtree(target)
        unregister_conversation(alive_conversations, fromid, toid)
        return "conversation withdrawn"
    ...（其余分支不变）
```
（`CONVERSATIONS_DIR` 已从 paths import，无需新增。）

- [ ] **Step 8: mcp_server.py 入口验证（双域）**

`mcp_server.py` import 区加 `import validation`。在 `mcp = FastMCP(...)` 后加 helper：
```python
def _entry_error(*checks):
    """Run MCP-entry validators (HP-06). `checks` are (validator, value) pairs.
    Returns the INVALID_ARGUMENT error string, or None when all pass. Kernel
    dispatch validates again - defense in depth, and remote RPC never passes
    through here."""
    try:
        for validator, value in checks:
            validator(value)
    except validation.InvalidArgumentError as e:
        return str(e)
    return None
```
对下列工具，在函数体第一行插入对应检查（模式统一，示例为 send_message）：
```python
@mcp.tool()
def send_message(fromid: str, toid: str, message: str) -> str:
    """...原有 docstring..."""
    err = _entry_error((validation.validate_session_id, fromid),
                       (validation.validate_session_id, toid),
                       (validation.validate_message_size, message))
    if err:
        return err
    return user_functions.send_message(fromid, toid, message)
```
逐工具的检查表（其余照此模式）：
- `query_session`/`check_alive`/`query_conversations`/`evoke`/`listen`/`query_my_ACK_timestamp`：`(validate_session_id, session_id)`
- `register_conversation`/`unregister_conversation`：`(validate_session_id, sid_a)`, `(validate_session_id, sid_b)`
- `withdraw`：`(validate_session_id, fromid)`, `(validate_session_id, toid)`
- `connect`：`(validate_session_id, caller_sid)`, `(validate_session_id, target_sid)`
- `close_connection`：`(validate_session_id, session_id)`, `(validate_session_id, toid)`
- `create_collaborator`：`(validate_session_id, caller_sid)`, `(validate_cwd, cwd)`

- [ ] **Step 9: proc.py parse_start_time 加固（双域，carry-forward）**

`proc.py:54` 把：
```python
    except (ValueError, TypeError):
```
改为：
```python
    except (ValueError, TypeError, AttributeError):
        # AttributeError: non-str truthy input (e.g. a float epoch) has no
        # .strip() - treat as unparseable instead of crashing (Wave 1
        # hardening; today's producers write ISO strings or null only).
```

- [ ] **Step 10: conftest.py fixture reload 列表（carry-forward）**

`tests/conftest.py` 的 reload 循环改为：
```python
    for name in ("paths", "result", "validation", "proc", "conversations",
                 "spawn", "machine_identity", "kernel_api", "kernel"):
        mods[name] = importlib.reload(importlib.import_module(name))
```
（`result`/`validation` 是纯模块，reload 无害；`machine_identity`/`proc` 是 Wave 1 测试要触碰的 path 绑定/进程模块——carry-forward 要求。）

- [ ] **Step 11: parity 工具 non-allowlisted compared==0 拒绝（carry-forward）**

`tools/check_parity.py` 的 `main()` 中，把：
```python
    win, wsl = _files(WIN), _files(WSL)
    if not win and not wsl:
        print("PARITY FAIL: 0 files found in either tree - refusing to pass "
              "having compared nothing (trees: %s, %s)" % (WIN, WSL))
        return 1
    problems = []
    for rel in sorted(set(win) | set(wsl)):
        if rel in ALLOWLIST:
            continue
```
改为：
```python
    win, wsl = _files(WIN), _files(WSL)
    compared = [rel for rel in sorted(set(win) | set(wsl)) if rel not in ALLOWLIST]
    if not compared:
        # Vacuous-pass guard: covers empty trees AND trees reduced to only
        # allowlisted files (0 meaningful compares must never print OK).
        print("PARITY FAIL: 0 non-allowlisted files to compare - refusing to "
              "pass having compared nothing (trees: %s, %s)" % (WIN, WSL))
        return 1
    problems = []
    for rel in compared:
```
并把末尾 `print("PARITY OK (%d files compared, ...)" % (len(win), ...))` 改为 `% (len(compared), ...)`。

- [ ] **Step 12: tests/parity/test_parity.py 加 only-allowlisted 负例**

在现有 `test_parity_fails_on_empty_trees` 后加：
```python
def test_parity_fails_when_only_allowlisted_files(tmp_path, monkeypatch):
    """Both trees reduced to only-allowlisted files -> 0 meaningful compares
    -> FAIL (carry-forward: the old len(win) count included allowlisted files)."""
    sys.path.insert(0, str(TOOLS))
    import check_parity
    win, wsl = tmp_path / "win", tmp_path / "wsl"
    win.mkdir()
    wsl.mkdir()
    (win / ".mcp.json").write_text("{}")
    (wsl / ".mcp.json").write_text("{}")
    monkeypatch.setattr(check_parity, "WIN", win)
    monkeypatch.setattr(check_parity, "WSL", wsl)
    assert check_parity.main() == 1
```
（import 机制沿用该文件现有的 `sys.path.insert(0, str(TOOLS)); import check_parity`；`sys` 与 `TOOLS` 文件顶部已有。）

- [ ] **Step 13: 全套件 + parity 跑绿**

Run: `py -3 -m pytest -v` → 全部 PASS（含 Gate 0 既有 10 个 + 新增）。
Run: `py -3 tools/check_parity.py` → `PARITY OK`。

- [ ] **Step 14: Commit**

```bash
git add v2_win v2_wsl tests tools
git commit -m "feat(HP-06): validation layer + result codes + destructive containment

- result.py: code enum (D7, Wave 1 minimal)
- validation.py: single validator/resolve layer, INVALID_ARGUMENT on failure
- conv_dir/pipe_filename enforce ids; dispatch arg table; MCP entry checks
- withdraw(init_connect=1) resolve_under containment before rmtree
- carry-forward: proc AttributeError, fixture reload proc/machine_identity,
  parity non-allowlisted compared==0 guard, end-event replay + format-lock tests"
```

---
### Task 2: HP-01 版本化 message record + per-store sequence + 原子发布 + 双 reader

**Files:**
- Create: `v2_win/cc-communicate/server/fileutil.py`（双域，下同）
- Create: `v2_win/cc-communicate/server/message_record.py`
- Modify: `v2_win/cc-communicate/server/paths.py`（+MESSAGE_SEQUENCE_FILE）
- Modify: `v2_win/cc-communicate/server/conversations.py`（parse_any_pipe_filename 双格式 dispatcher）
- Modify: `v2_win/cc-communicate/server/kernel_api.py`（send_message 重写、listen_scan/collect_messages/withdraw 双 reader、_atomic_write_json 委托 fileutil）
- Modify: `v2_win/cc-communicate/server/kernel.py`（message_sequence 状态 + self-heal 加载 + dispatch 传参 + _atomic_write_json 委托）
- Modify: `v2_win/cc-communicate/server/user_functions.py`（_scan_pipe/_claim_reply 双 reader）
- Modify: `tests/unit/test_legacy_format_lock.py`（改写为 legacy 双 reader 测试）
- Modify: `tests/unit/test_message_roundtrip.py`、`tests/unit/test_cancel_redelivery.py`（send_message 新签名）
- Create: `tests/unit/test_message_record.py`

**Interfaces:**
- Consumes: Task 1 的 validation/result；conftest fixture。
- Produces（Task 3/4 依赖的精确名字）:
  - `fileutil.atomic_write_bytes(path, data)`、`fileutil.atomic_write_json(path, obj, indent=None)`（tmp+flush+fsync+os.replace）
  - `message_record.new_record(store_id, sequence, from_session, to_session, text, kind="text", correlation_id=None, causation_id=None, message_id=None) -> dict`
  - `message_record.record_filename(record) -> str`（`<seq:020d>__<from>__<to>__<mid>.json`）
  - `message_record.parse_record_filename(name) -> (seq, from, to, mid) | None`
  - `message_record.publish(conv_d, record) -> filename`、`message_record.read_record(path) -> dict | None`
  - `conversations.parse_any_pipe_filename(name) -> {"format","ts","from_id","to_id","sequence","message_id"} | None`
  - `kernel_api.send_message(alive_conversations, message_sequence, store_id, fromid, toid, message, message_id=None) -> str`（**签名已变**）
  - `kernel.message_sequence`（模块级 dict）、`kernel._local_store_id`（str）、`kernel._load_message_sequence()/_save_message_sequence()`
  - `paths.MESSAGE_SEQUENCE_FILE`

**格式决策（对提案的显式偏离，已论证）：** 文件名为 `<seq:020d>__<from>__<to>__<message_id>.json`（提案是 `<seq>__<mid>.json`）。把 from/to 嵌进文件名让 pipe 扫描/filter 无需打开每个文件（listen_scan、_claim_reply、count_undelivered 都按 to/from 过滤）；信封仍是唯一事实源，文件名字段是路由缓存。id 白名单（Task 1）保证 id 内无 `__`，4 段切分无歧义。

- [ ] **Step 1: 写失败测试 `tests/unit/test_message_record.py`**

```python
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
    res2 = ka.listen_scan(acked, "bob", 42)
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3 -m pytest tests/unit/test_message_record.py -v`
Expected: FAIL——`ka.send_message` 现签名只收 4 参（TypeError），`MESSAGE_SEQUENCE_FILE`/`_load_message_sequence` 不存在。

- [ ] **Step 3: 新建 `server/fileutil.py`（双域，纯模块）**

```python
"""Atomic file-write primitives with fsync (C1 / HP-01).

tmp file in the SAME directory -> flush -> fsync -> os.replace. Correctness
anchor across realms (NTFS / WSL ext4 / DrvFs /mnt/c / 9P //wsl.localhost):
the reader NEVER sees a partial file because the final path appears via atomic
rename. Crash-durability of the rename itself varies by filesystem (fsync on
DrvFs/9P is weak); recovery is anchored in the persistent sequence counter +
message_id dedup (master plan R2). Stale .tmp.<pid> residue is ignored by all
readers (suffix-based scans never match it).
"""
from __future__ import annotations

import json
import os


def atomic_write_bytes(path: str, data: bytes):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: str, obj, indent=None):
    atomic_write_bytes(path, json.dumps(obj, indent=indent).encode("utf-8"))
```

- [ ] **Step 4: 新建 `server/message_record.py`（双域，纯模块）**

```python
"""Versioned message record (envelope) + atomic publish (HP-01, D6).

Record schema v1:
  {schema_version, message_id, store_id, sequence, from_session, to_session,
   kind, correlation_id, causation_id, created_at_ms, payload: {text}}

  - sequence: per-store monotonic number allocated by the store's single
    kernel thread - the ONLY ordering/ACK unit (HP-02 builds cursors on it).
    Gaps are allowed (crash between counter persist and publish); a sequence
    is NEVER reused.
  - message_id: uuid4 hex - end-to-end identity and the dedup unit (HP-03).
    Embedded in the final filename, so even a sequence bug cannot overwrite an
    existing message.
  - created_at_ms: display/diagnostic ONLY - never a correctness field.

Filename: <sequence:020d>__<from>__<to>__<message_id>.json
  from/to are embedded so pipe scans filter WITHOUT opening every file (a
  deliberate deviation from the proposal's <seq>__<mid>.json; the envelope
  stays the source of truth - filename fields are a routing cache). Validated
  ids contain no '__' (HP-06), so the 4-field split is unambiguous.

Pure module: every path comes in as a parameter (test isolation).
"""
from __future__ import annotations

import json
import os
import time
import uuid

import fileutil

SCHEMA_VERSION = 1
RECORD_SUFFIX = ".json"


def new_record(store_id: str, sequence: int, from_session: str, to_session: str,
               text: str, kind: str = "text", correlation_id=None,
               causation_id=None, message_id: str = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": message_id or uuid.uuid4().hex,
        "store_id": store_id,
        "sequence": int(sequence),
        "from_session": from_session,
        "to_session": to_session,
        "kind": kind,
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "created_at_ms": int(time.time() * 1000),
        "payload": {"text": text},
    }


def record_filename(record: dict) -> str:
    return "%020d__%s__%s__%s%s" % (
        record["sequence"], record["from_session"], record["to_session"],
        record["message_id"], RECORD_SUFFIX)


def parse_record_filename(name: str):
    """-> (sequence:int, from_session, to_session, message_id) or None."""
    if not name.endswith(RECORD_SUFFIX):
        return None
    parts = name[:-len(RECORD_SUFFIX)].split("__")
    if len(parts) != 4:
        return None
    seq_s, from_s, to_s, mid = parts
    try:
        seq = int(seq_s)
    except ValueError:
        return None
    if not mid:
        return None
    return seq, from_s, to_s, mid


def publish(conv_d: str, record: dict) -> str:
    """Atomically publish the record into <conv_d>/pipe/. Returns filename."""
    fname = record_filename(record)
    fileutil.atomic_write_json(os.path.join(conv_d, "pipe", fname), record)
    return fname


def read_record(path: str):
    """json load; None on ANY failure (missing/partial/malformed/non-dict)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None
```

- [ ] **Step 5: paths.py + conversations.py（双域）**

`paths.py` 在 `ACK_TIMESTAMPS_FILE` 行后加：
```python
MESSAGE_SEQUENCE_FILE = os.path.join(SERVER_DATA_DIR, 'message_sequence.json')  # per-store monotonic counter (HP-01)
```

`conversations.py` import 区加 `import message_record`，文件末尾加：
```python
def parse_any_pipe_filename(name: str):
    """Normalize BOTH pipe filename formats (reader-side dispatcher, HP-01).

    Legacy v0.3: <ts:013d>__<from>__<to>.md
        -> {"format": "legacy", "ts": int, "from_id", "to_id",
            "sequence": None, "message_id": None}
    Record v1:   <seq:020d>__<from>__<to>__<message_id>.json
        -> {"format": "record", "ts": None, "from_id", "to_id",
            "sequence": int, "message_id": str}
    Malformed -> None. Tolerant by design: this is a reader, not a boundary.
    """
    if name.endswith(".md"):
        parsed = parse_pipe_filename(name)
        if not parsed:
            return None
        ts, fromid, toid = parsed
        return {"format": "legacy", "ts": ts, "from_id": fromid,
                "to_id": toid, "sequence": None, "message_id": None}
    parsed = message_record.parse_record_filename(name)
    if not parsed:
        return None
    seq, fromid, toid, mid = parsed
    return {"format": "record", "ts": None, "from_id": fromid,
            "to_id": toid, "sequence": seq, "message_id": mid}
```

- [ ] **Step 6: kernel_api.py 改造（双域）**

import 区改为加（`import validation` 在 Task 1 已加，此处不重复）：
```python
import fileutil
import message_record
```
并把 `from paths import CONVERSATIONS_DIR, SERVER_DATA_DIR, PLUGIN_ROOT, ACK_TIMESTAMPS_FILE` 一行改为：
```python
from paths import CONVERSATIONS_DIR, SERVER_DATA_DIR, PLUGIN_ROOT, ACK_TIMESTAMPS_FILE, MESSAGE_SEQUENCE_FILE
```
`_atomic_write_json` 委托 fileutil（保留名字，调用点不动）：
```python
def _atomic_write_json(path: str, obj):
    fileutil.atomic_write_json(path, obj)
```

`send_message` 重写（**替换整个函数**）：
```python
def send_message(alive_conversations: dict, message_sequence: dict, store_id: str,
                 fromid: str, toid: str, message: str, message_id: str = None) -> str:
    """HP-01: allocate a per-store sequence, wrap the text in a v1 record,
    atomically publish. The sequence counter is persisted BEFORE the message
    (a crash in between leaves a gap - allowed; a sequence is never reused).
    HP-03 dedup: a retry carrying the same message_id returns the ORIGINAL
    result without publishing a duplicate. Return string keeps the legacy
    'message_sent at <created_at_ms>' shape (connect parses it)."""
    a, b = sorted([fromid, toid])
    if (a, b) not in alive_conversations:
        return "failed, connection not registered"
    d = conversations.ensure_conv_dir(fromid, toid)
    if message_id:
        found = _find_message_file(d, message_id)
        if found:
            rec = message_record.read_record(found)
            ts = rec.get("created_at_ms", 0) if rec else 0
            return f"message_sent at {ts}"
    seq = int(message_sequence.get("last_allocated", 0)) + 1
    message_sequence["last_allocated"] = seq
    message_sequence["store_id"] = store_id
    fileutil.atomic_write_json(MESSAGE_SEQUENCE_FILE, message_sequence)
    rec = message_record.new_record(store_id, seq, fromid, toid, message,
                                    message_id=message_id)
    message_record.publish(d, rec)
    return f"message_sent at {rec['created_at_ms']}"


def _find_message_file(conv_d: str, message_id: str):
    """Locate a published message by message_id (filename suffix) in pipe/ or
    log/. Returns the full path, or None. O(files) - fine at this scale."""
    suffix = "__" + message_id + ".json"
    for sub in ("pipe", "log"):
        d = os.path.join(conv_d, sub)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if name.endswith(suffix):
                return os.path.join(d, name)
    return None
```

先加共享的 dual-reader 构建器（listen_scan 与 collect_messages 共用，避免逐字重复一个逻辑块）：
```python
def _read_pipe_message(src: str, info: dict):
    """Build the normalized message dict for one pipe file (dual reader,
    HP-01), or None to skip (malformed/partial/undecodable - C5). Shared by
    listen_scan and collect_messages. The '_sort' key orders legacy .md first
    (they predate the upgrade), then records by SEQUENCE - never by
    created_at_ms (clock backward must not reorder, PB-2)."""
    if info["format"] == "record":
        rec = message_record.read_record(src)
        if not rec:
            return None
        return {"time": rec.get("created_at_ms", 0),
                "from_id": rec.get("from_session"),
                "message": (rec.get("payload") or {}).get("text"),
                "message_id": rec.get("message_id"),
                "sequence": rec.get("sequence"),
                "store_id": rec.get("store_id"),
                "_sort": (1, 0, rec.get("sequence") or 0)}
    try:
        with open(src, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    return {"time": info["ts"], "from_id": info["from_id"],
            "message": content, "message_id": None,
            "sequence": None, "store_id": None,
            "_sort": (0, info["ts"], 0)}


def _archive(src: str, log_dir: str, fname: str):
    """pipe -> log, best-effort (shared by the scans)."""
    os.makedirs(log_dir, exist_ok=True)
    try:
        os.replace(src, os.path.join(log_dir, fname))
    except OSError:
        pass
```

`listen_scan` 双 reader 化（**替换整个函数**；v1 timestamp ACK 语义在 deprecation window 内不变）：
```python
def listen_scan(acked_timestamps: dict, sid: str, acked_ts: int) -> dict:
    """LEGACY timestamp-ACK scan (kept for the deprecation window, HP-01 dual
    reader via _read_pipe_message). Archive rule is unchanged:
    (to==sid, time<=acked_ts) moves pipe->log; for records the record's
    created_at_ms stands in for the timestamp until HP-02 cursors take over.
    Record message dicts carry message_id/sequence/store_id; legacy entries
    carry them as None."""
    if acked_ts and acked_ts > acked_timestamps.get(sid, 0):
        acked_timestamps[sid] = acked_ts
    messages = []
    try:
        entries = os.listdir(CONVERSATIONS_DIR)
    except FileNotFoundError:
        return {"messages": [], "watermark": acked_ts}
    for name in entries:
        parts = name.split(conversations.SEP)
        if len(parts) != 2 or sid not in parts:
            continue
        pipe = os.path.join(CONVERSATIONS_DIR, name, "pipe")
        log = os.path.join(CONVERSATIONS_DIR, name, "log")
        if not os.path.isdir(pipe):
            continue
        for fname in os.listdir(pipe):
            info = conversations.parse_any_pipe_filename(fname)
            if not info or info["to_id"] != sid:
                continue
            src = os.path.join(pipe, fname)
            msg = _read_pipe_message(src, info)
            if msg is None:
                continue
            if msg["time"] <= acked_ts:
                _archive(src, log, fname)
                continue
            messages.append(msg)
    messages.sort(key=lambda m: m["_sort"])
    for m in messages:
        del m["_sort"]
    watermark = max([m["time"] for m in messages], default=acked_ts)
    return {"messages": messages, "watermark": watermark}
```
注意：record 的 `_sort` 用 sequence——这是 PB-2 修复在 v1 读取路径的体现；watermark 仍取 max time（v1 语义）。

`collect_messages` 双 reader 化（**替换整个函数**；保持「读出即归档」语义）：
```python
def collect_messages(session_id: str) -> list:
    """Read all undelivered pipe messages addressed to session_id, move them to
    log/, return sorted (legacy .md first, then records by sequence - see
    _read_pipe_message). Used by close_connection (drain) and the remote
    _archive_reply path."""
    result = []
    try:
        entries = os.listdir(CONVERSATIONS_DIR)
    except FileNotFoundError:
        return result
    for name in entries:
        parts = name.split(conversations.SEP)
        if len(parts) != 2 or session_id not in parts:
            continue
        conv_d = os.path.join(CONVERSATIONS_DIR, name)
        pipe = os.path.join(conv_d, "pipe")
        log = os.path.join(conv_d, "log")
        if not os.path.isdir(pipe):
            continue
        for fname in os.listdir(pipe):
            info = conversations.parse_any_pipe_filename(fname)
            if not info or info["to_id"] != session_id:
                continue
            src = os.path.join(pipe, fname)
            msg = _read_pipe_message(src, info)
            if msg is None:
                continue
            result.append(msg)
            _archive(src, log, fname)
    result.sort(key=lambda m: m["_sort"])
    for m in result:
        del m["_sort"]
    return result
```

`withdraw` 非 init 分支双 reader 化（**替换 `else` 之后整段**，init_connect 分支保持 Task 1 版本）：
```python
    d = conversations.conv_dir(fromid, toid)
    pipe = os.path.join(d, "pipe")
    try:
        files = os.listdir(pipe)
    except FileNotFoundError:
        return "no messages"
    candidates = []
    for f in files:
        info = conversations.parse_any_pipe_filename(f)
        if not info or info["from_id"] != fromid:
            continue
        # "latest": records order by sequence, legacy by ts; records always
        # postdate legacy, so rank records above any legacy.
        key = (1, info["sequence"]) if info["format"] == "record" else (0, info["ts"])
        candidates.append((key, f))
    if not candidates:
        return f"no messages from {fromid}"
    candidates.sort(key=lambda x: x[0])
    os.remove(os.path.join(pipe, candidates[-1][1]))
    return f"withdrew latest message from {fromid}"
```

- [ ] **Step 7: kernel.py message_sequence 状态（双域）**

import 区加 `import fileutil`。模块级 state 区（`acked_timestamps` 行后）加：
```python
message_sequence: dict = {}  # HP-01: {"schema_version","store_id","last_allocated"}
_local_store_id: str = "unknown"
```
`_atomic_write_json` 委托：
```python
def _atomic_write_json(path: str, obj):
    fileutil.atomic_write_json(path, obj, indent=2)
```
在 `_save_ack_timestamps` 后加：
```python
def _load_message_sequence():
    """Load the persistent per-store sequence counter (HP-01), self-healing to
    max(persisted, max sequence found in any pipe/log file) so a lost/corrupt
    counter NEVER causes sequence reuse."""
    import conversations as _conv
    data = _read_json(MESSAGE_SEQUENCE_FILE)
    state = {"schema_version": 1, "store_id": _local_store_id, "last_allocated": 0}
    if isinstance(data, dict) and isinstance(data.get("last_allocated"), int):
        state["last_allocated"] = max(0, data["last_allocated"])
        if data.get("store_id"):
            state["store_id"] = data["store_id"]
    found = 0
    try:
        entries = os.listdir(CONVERSATIONS_DIR)
    except FileNotFoundError:
        entries = []
    for name in entries:
        for sub in ("pipe", "log"):
            d = os.path.join(CONVERSATIONS_DIR, name, sub)
            if not os.path.isdir(d):
                continue
            for fname in os.listdir(d):
                info = _conv.parse_any_pipe_filename(fname)
                if info and info["sequence"] is not None:
                    found = max(found, info["sequence"])
    state["last_allocated"] = max(state["last_allocated"], found)
    message_sequence.clear()
    message_sequence.update(state)
    log.info("loaded message_sequence: last_allocated=%d (healed from files=%d)",
             state["last_allocated"], found)


def _save_message_sequence():
    _atomic_write_json(MESSAGE_SEQUENCE_FILE, message_sequence)
```
`kernel.py` 顶部 paths import 行加 `MESSAGE_SEQUENCE_FILE` 与 `CONVERSATIONS_DIR`（若未已 import）。`main()` 中：
- `_local_machine_type = machine_identity.load_or_create()...` 行后加：
```python
    _local_store_id = machine_identity.load_or_create().get("id", "unknown")
```
  并把函数顶部 `global` 声明改为 `global _last_activity, _local_machine_type, _local_store_id`。
- `_load_ack_timestamps()` 行后加 `_load_message_sequence()`。
- `finally:` 里 `_save_ack_timestamps()` 行后加 `_save_message_sequence()`。
`_dispatch` 的 send_message 分支改为：
```python
    if function == "send_message":
        return kernel_api.send_message(
            alive_conversations, message_sequence, _local_store_id,
            args["fromid"], args["toid"], args["message"], args.get("message_id"))
```
`_ARG_VALIDATORS["send_message"]` 加一项 `"message_id": validation.validate_message_id`（仅在存在且非 None 时校验，Step 6 的表机制已保证）。

- [ ] **Step 8: user_functions.py 双 reader（双域）**

`_scan_pipe` 改为：
```python
def _scan_pipe(pipe_dir, want_toid):
    out = []
    try:
        files = os.listdir(pipe_dir)
    except (FileNotFoundError, PermissionError, OSError):
        return out
    for fname in files:
        info = conversations.parse_any_pipe_filename(fname)
        if info and info["to_id"] == want_toid:
            out.append((fname, os.path.join(pipe_dir, fname), info))
    return out
```
`_claim_reply` 改为（ts 对 record 取自信封 created_at_ms；内容对 record 取自 payload.text）：
```python
def _claim_reply(pipe_dir, caller, target, conv_remote, hello_ts=0):
    """Scan pipe_dir once for target's reply (toid==caller, fromid==target).
    Returns the reply content (archiving the file), or None. Stale messages
    (ts <= hello_ts) are skipped (C3). Dual reader (HP-01): for records the
    envelope supplies ts/content."""
    for fname, path, info in _scan_pipe(pipe_dir, caller):
        if info["from_id"] != target:
            continue
        if info["format"] == "record":
            rec = message_record.read_record(path)
            if not rec:
                continue
            ts = rec.get("created_at_ms", 0)
            content = (rec.get("payload") or {}).get("text")
            if content is None:
                continue
        else:
            ts = info["ts"]
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue
        if ts <= hello_ts:
            continue  # C3: stale
        _archive_reply(conv_remote, caller, fname, path)
        return content
    return None
```
import 区加 `import message_record`。

- [ ] **Step 9: 更新既有测试（签名迁移 + legacy lock 改写）**

`tests/unit/test_message_roundtrip.py`、`tests/unit/test_cancel_redelivery.py`、`tests/unit/test_validation.py::test_fuzz_no_escape`：所有 `ka.send_message(convs, "alice", "bob", ...)`（fuzz 用例里是 `ka.send_message(convs, bad, "bob", "x")`）调用改为新签名 `ka.send_message(convs, seq_state, "store-test", <fromid>, <toid>, <text>)`，测试文件顶部加：
```python
def _seq_state():
    return {"schema_version": 1, "store_id": "store-test", "last_allocated": 0}
```
并在每个用例里 `seq_state = _seq_state()`（fixture 级也行，保持文件风格一致即可）。其余断言不变（roundtrip 的 pipe/log 计数、cancel-redelivery 的重投语义对 record 格式同样成立——这正是要锁的行为）。

`tests/unit/test_legacy_format_lock.py` **改写**（writer 已切换；本文件变为「手工构造 legacy 文件测双 reader」）：
```python
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
```

- [ ] **Step 10: 全套件 + parity 跑绿**

Run: `py -3 -m pytest -v` → 全 PASS。
Run: `py -3 tools/check_parity.py` → PARITY OK。
（若 Gate 0 的 `test_scaffolding_smoke` 等因 fixture 未变而直接过，属预期。）

- [ ] **Step 11: Commit**

```bash
git add v2_win v2_wsl tests
git commit -m "feat(HP-01): versioned message record + per-store sequence + atomic publish

- fileutil.py: tmp+flush+fsync+os.replace primitive
- message_record.py: v1 envelope + atomic publish (pure module)
- send_message: sequence allocation, counter persisted first (gap-allowed,
  never-reused), message_id dedup, legacy-compatible return string
- dual readers: listen_scan/collect_messages/withdraw/_claim_reply read both
  .md and .json; records order by sequence (PB-2), legacy first
- kernel: message_sequence state with self-heal load (max of counter/files)"
```

---
### Task 3: HP-02 per-store cursor（listen_v2 + query_my_cursors）

**Files:**
- Modify: `v2_win/cc-communicate/server/paths.py`（+CURSORS_FILE，双域，下同）
- Modify: `v2_win/cc-communicate/server/kernel_api.py`（listen_scan_v2/query_cursors/upload_cursor）
- Modify: `v2_win/cc-communicate/server/kernel.py`（cursors 状态 + dispatch + 验证表）
- Modify: `v2_win/cc-communicate/server/user_functions.py`（_store_ids/listen_v2/query_my_cursors/close_connection cursors 参数）
- Modify: `v2_win/cc-communicate/server/mcp_server.py`（listen_v2/query_my_cursors 工具、close_connection 参数、legacy 标记）
- Modify: `v2_win/cc-communicate/skills/cc-communicate/SKILL.md`（cursor ACK 章节 + legacy 迁移说明，双域）
- Create: `tests/unit/test_cursor_ack.py`

**Interfaces:**
- Consumes: Task 2 的 record 格式/`parse_any_pipe_filename`/`fileutil`；Task 1 的 `validation.validate_cursors`。
- Produces（Wave 2 依赖）:
  - `kernel_api.listen_scan_v2(cursors_state: dict, store_id: str, sid: str, acked_seq: int) -> {"store_id","messages":[record...],"next_cursor": int}`
  - `kernel_api.query_cursors(cursors_state, sid) -> dict`、`kernel_api.upload_cursor(cursors_state, store_id, sid, seq) -> dict`
  - `user_functions.listen_v2(session_id, cursors=None, timeout=30) -> {"messages","next_cursors"}`、`user_functions.query_my_cursors(session_id) -> dict`
  - `kernel.cursors`（模块级 dict：sid -> {store_id: seq}）、`paths.CURSORS_FILE`
  - MCP 工具：`listen_v2(session_id, cursors=None, timeout=30)`、`query_my_cursors(session_id)`；`close_connection(session_id, toid, acked_ts=0, cursors=None)`

**核心语义（验收锚点）：**
- 归档规则只允许 `record.store_id == 本 store AND record.sequence <= cursors[本store]`；**绝不跨 store 比 sequence，绝不用 created_at_ms 做正确性判断**。
- cursor ACK = 「调用方已把这批 transport message 持久接收」，不代表上层任务完成（SKILL 必须写明：先持久化再推进 cursor）。
- legacy `.md` 对 v2 **不可见**（显式迁移点：v0.3 在途消息经 legacy listen 排空；cursor state 不从 ack_timestamps 猜测性转换）。
- `query_my_cursors` 合并 local + host 两个 store 的 cursor map（跨 realm）。

- [ ] **Step 1: 写失败测试 `tests/unit/test_cursor_ack.py`**

```python
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
    convs, cursors = {}, _seq_state()
    _send3(ka, convs, cursors)
    cur = {}
    r = ka.listen_scan_v2(cur, LOCAL, "bob", 2)
    assert [m["payload"]["text"] for m in r["messages"]] == ["m3"]
    assert r["next_cursor"] == 3 and r["store_id"] == LOCAL
    d = server.conversations.conv_dir("alice", "bob")
    assert len(os.listdir(os.path.join(d, "log"))) == 2  # seq 1,2 已归档
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
    uf = server.user_functions if hasattr(server, "user_functions") else None
    if uf is None:
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
    assert [m["payload"]["text"] for m in r["messages"]] == ["local-msg"]
    assert r["next_cursors"] == {LOCAL: 6}  # HOST 未被推进（无消息、无记录）
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
    assert uf.query_my_cursors("bob") == {LOCAL: 6, HOST: 3}


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
```

注意：`user_functions` 不在 Gate 0 fixture 的 reload 列表（它 import 时绑定 `CONVERSATIONS_DIR` 等）。本任务的 uf 测试全部 monkeypatch rpc/machine 层、不碰真实路径，故直接 `importlib.import_module("user_functions")` 即可；**不要**改 fixture（避免影响既有测试）。若 implementer 发现 `user_functions` import 链有副作用导致收集失败，停下来报告（DONE_WITH_CONCERNS），不要自行重构 fixture。

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3 -m pytest tests/unit/test_cursor_ack.py -v`
Expected: FAIL——`listen_scan_v2`/`query_cursors`/`upload_cursor`/`_load_cursors`/`listen_v2`/`query_my_cursors` 不存在；`close_connection` 不收 `cursors`。

- [ ] **Step 3: paths.py + kernel_api.py cursor 函数（双域）**

`paths.py` 在 `MESSAGE_SEQUENCE_FILE` 行后加：
```python
CURSORS_FILE = os.path.join(SERVER_DATA_DIR, 'cursors.json')  # per-sid per-store cursors (HP-02)
```

`kernel_api.py` import 行加 `CURSORS_FILE`（from paths import ...），文件末尾加：
```python
# ---------- listening: per-store cursor ACK (HP-02) ----------
# Cursor semantics: a cursor says "the caller has DURABLY received everything
# up to this sequence FROM THIS STORE" - transport-level receipt, NOT upper-
# layer task completion. Archive rule is ONLY:
#     record.store_id == this store AND record.sequence <= cursor[this store]
# Never compare sequences across stores; never use created_at_ms for
# correctness. Legacy .md files are INVISIBLE here (explicit migration point:
# v0.3 in-flight messages drain via the legacy listen during the deprecation
# window; cursor state never converts old timestamps).

def listen_scan_v2(cursors_state: dict, store_id: str, sid: str, acked_seq: int) -> dict:
    """Atomic (kernel-thread) per-store cursor scan. acked_seq is the caller's
    confirmed cursor FOR THIS STORE. Archives confirmed records (pipe->log),
    peeks newer ones (no archive), updates the in-memory cursor map (persisted
    on upload/close/exit). Cancel-safe: only what a PRIOR call confirmed is
    archived."""
    try:
        acked_seq = int(acked_seq or 0)
    except (TypeError, ValueError):
        raise validation.InvalidArgumentError(f"cursor must be an int; got {acked_seq!r}")
    if acked_seq < 0:
        raise validation.InvalidArgumentError(f"cursor must be >= 0; got {acked_seq}")
    per = cursors_state.setdefault(sid, {})
    if acked_seq > per.get(store_id, 0):
        per[store_id] = acked_seq
    messages = []
    try:
        entries = os.listdir(CONVERSATIONS_DIR)
    except FileNotFoundError:
        return {"store_id": store_id, "messages": [], "next_cursor": acked_seq}
    for name in entries:
        parts = name.split(conversations.SEP)
        if len(parts) != 2 or sid not in parts:
            continue
        pipe = os.path.join(CONVERSATIONS_DIR, name, "pipe")
        log = os.path.join(CONVERSATIONS_DIR, name, "log")
        if not os.path.isdir(pipe):
            continue
        for fname in os.listdir(pipe):
            info = conversations.parse_any_pipe_filename(fname)
            if not info or info["format"] != "record" or info["to_id"] != sid:
                continue  # legacy .md invisible to v2 (migration point)
            src = os.path.join(pipe, fname)
            rec = message_record.read_record(src)
            if not rec:
                continue  # C5: skip malformed/partial
            seq = rec.get("sequence")
            if not isinstance(seq, int):
                continue
            if seq <= acked_seq:
                os.makedirs(log, exist_ok=True)
                try:
                    os.replace(src, os.path.join(log, fname))
                except OSError:
                    pass
                continue
            messages.append(rec)
    messages.sort(key=lambda r: r["sequence"])
    next_cursor = max([m["sequence"] for m in messages], default=acked_seq)
    return {"store_id": store_id, "messages": messages, "next_cursor": next_cursor}


def query_cursors(cursors_state: dict, sid: str) -> dict:
    """This kernel's stored cursor map for sid (usually one store entry - each
    kernel persists only cursors for ITS OWN store). user_functions merges
    across machines."""
    return dict(cursors_state.get(sid, {}))


def upload_cursor(cursors_state: dict, store_id: str, sid: str, seq: int) -> dict:
    """Persist the caller's cursor for THIS store (max-merge; never regresses).
    Written through to cursors.json immediately (close is infrequent), so it
    survives a later kernel crash. Returns sid's stored map for this kernel."""
    try:
        seq = int(seq or 0)
    except (TypeError, ValueError):
        raise validation.InvalidArgumentError(f"cursor must be an int; got {seq!r}")
    if seq < 0:
        raise validation.InvalidArgumentError(f"cursor must be >= 0; got {seq}")
    per = cursors_state.setdefault(sid, {})
    if seq > per.get(store_id, 0):
        per[store_id] = seq
    try:
        fileutil.atomic_write_json(
            CURSORS_FILE, {"schema_version": 1, "sessions": cursors_state})
    except OSError:
        pass
    return dict(per)
```

- [ ] **Step 4: kernel.py cursors 状态 + dispatch（双域）**

模块级 state 区加：
```python
cursors: dict = {}  # HP-02: sid -> {store_id: confirmed sequence} (persisted)
```
paths import 行加 `CURSORS_FILE`。`_save_message_sequence` 后加：
```python
def _load_cursors():
    """Reload per-sid per-store cursors (HP-02). Fresh start when absent -
    cursor state is NEVER converted from legacy ack_timestamps (explicit
    migration point)."""
    data = _read_json(CURSORS_FILE)
    if not isinstance(data, dict):
        return
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        return
    for sid, per in sessions.items():
        if not isinstance(per, dict):
            continue
        clean = {str(k): int(v) for k, v in per.items()
                 if isinstance(v, int) and not isinstance(v, bool) and v >= 0}
        if clean:
            cursors[sid] = clean
    log.info("loaded cursors.json: %d sids", len(cursors))


def _save_cursors():
    _atomic_write_json(CURSORS_FILE, {"schema_version": 1, "sessions": cursors})
```
`main()`：`_load_message_sequence()` 行后加 `_load_cursors()`；`finally:` 里 `_save_message_sequence()` 行后加 `_save_cursors()`。
`_ARG_VALIDATORS` 加：
```python
    "listen_scan_v2": {"sid": validation.validate_session_id},
    "query_cursors": {"sid": validation.validate_session_id},
    "upload_cursor": {"sid": validation.validate_session_id},
```
`_dispatch` 加分支（放在 `upload_ack_timestamp` 分支后）：
```python
    if function == "listen_scan_v2":
        return kernel_api.listen_scan_v2(cursors, _local_store_id, args["sid"],
                                         args.get("cursor", 0))
    if function == "query_cursors":
        return kernel_api.query_cursors(cursors, args["sid"])
    if function == "upload_cursor":
        return kernel_api.upload_cursor(cursors, _local_store_id, args["sid"],
                                        args.get("seq", 0))
```

- [ ] **Step 5: user_functions.py（双域）**

在 `query_my_ACK_timestamp` 后加：
```python
# ---------- cursor-ACK listening (HP-02; preferred over legacy listen) ----------

def _store_ids():
    """(local_store_id, host_entry). host_entry is None when we ARE the host
    (then all our convs are local). The host's store id is its registry id."""
    local_id = machine_identity.load_or_create().get("id")
    return local_id, _host_entry()


def listen_v2(session_id: str, cursors: dict = None, timeout: int = 30) -> dict:
    """BLOCKING listen with PER-STORE cursors (HP-02). `cursors` maps
    store_id -> confirmed sequence ({} or None the first time; recover with
    query_my_cursors after compact/restart). Each store is scanned with ONLY
    its own cursor - cursors are never merged or compared across stores.
    Returns {messages, next_cursors}. Cancel-safe: the kernel archives only
    what you confirmed via the cursors you passed. Persist the messages to
    YOUR store first, THEN advance cursors (transport receipt != task done).
    NEVER fall back to the timestamp `listen` once you use cursors."""
    cursors = dict(cursors or {})
    local_id, host = _store_ids()
    deadline = time.time() + timeout
    while time.time() < deadline:
        messages = []
        next_cursors = dict(cursors)
        try:
            r = rpc_client.call("listen_scan_v2",
                                {"sid": session_id, "cursor": cursors.get(local_id, 0)})
        except Exception:
            r = None  # transient kernel issue -> treat as empty, retry
        if isinstance(r, dict):
            messages.extend(r.get("messages") or [])
            nc = r.get("next_cursor", 0)
            if nc > next_cursors.get(local_id, 0):
                next_cursors[local_id] = nc
        if host is not None:
            hid = host.get("id")
            rr = rpc_client.call_remote(host, "listen_scan_v2",
                                        {"sid": session_id, "cursor": cursors.get(hid, 0)})
            if isinstance(rr, dict):
                messages.extend(rr.get("messages") or [])
                nc = rr.get("next_cursor", 0)
                if nc > next_cursors.get(hid, 0):
                    next_cursors[hid] = nc
        if messages:
            # Display-only sort (created_at_ms is NOT a correctness field);
            # per-store order is by sequence, cross-store order is undefined.
            messages.sort(key=lambda m: (m.get("created_at_ms", 0),
                                         m.get("store_id") or "",
                                         m.get("sequence", 0)))
            return {"messages": messages, "next_cursors": next_cursors}
        time.sleep(_LISTEN_POLL)
    return {"messages": [], "next_cursors": cursors}


def query_my_cursors(session_id: str) -> dict:
    """Recover your per-store cursors, merged across this machine + the host
    (each kernel persists only its own store's cursors)."""
    local_id, host = _store_ids()
    out = {}
    try:
        r = rpc_client.call("query_cursors", {"sid": session_id})
    except Exception:
        r = None
    if isinstance(r, dict):
        out.update(r)
    if host is not None:
        rr = rpc_client.call_remote(host, "query_cursors", {"sid": session_id})
        if isinstance(rr, dict):
            out.update(rr)
    return out
```
`close_connection` 签名与上传段改为：
```python
def close_connection(session_id: str, toid: str, acked_ts: int = 0,
                     cursors: dict = None) -> dict:
    """...（原 docstring 保留，末尾加：）Also uploads per-store cursors when
    given (HP-02): each cursor goes ONLY to the kernel owning that store;
    unknown store ids are ignored. Legacy acked_ts upload is unchanged..."""
    conv_remote = _conv_store(toid)
    notice = (...)
    # 1. upload the caller's legacy watermark (unchanged)
    try:
        rpc_client.call("upload_ack_timestamp", {"sid": session_id, "ts": acked_ts})
    except Exception:
        pass
    # 1b. upload per-store cursors (HP-02) - each to its owning kernel only
    if cursors:
        local_id, host = _store_ids()
        host_id = host.get("id") if host else None
        for store_id, seq in cursors.items():
            try:
                if store_id == local_id:
                    rpc_client.call("upload_cursor", {"sid": session_id, "seq": seq})
                elif host_id and store_id == host_id:
                    rpc_client.call_remote(host, "upload_cursor",
                                           {"sid": session_id, "seq": seq})
                # unknown store ids are ignored by design
            except Exception:
                pass
    # 2. ...（notify + unregister 段不变）
```

- [ ] **Step 6: mcp_server.py（双域）**

`listen` 工具 docstring 首行加 `LEGACY (deprecation window): prefer listen_v2.`；`query_my_ACK_timestamp` docstring 首行加 `LEGACY: prefer query_my_cursors.`。在 `query_my_ACK_timestamp` 工具后加两个新工具：
```python
@mcp.tool()
def listen_v2(session_id: str, cursors: dict = None, timeout: int = 30) -> dict:
    """BLOCKING listen with PER-STORE cursors (PREFERRED over legacy listen).
    Pass {} (or query_my_cursors) the first time; on every later call pass the
    `next_cursors` the previous listen_v2 returned - unchanged. Returns
    {messages, next_cursors}. Each message is a record: {message_id, store_id,
    sequence, from_session, to_session, kind, correlation_id, created_at_ms,
    payload:{text}}. Dedup on message_id if you see repeats (at-least-once).
    IMPORTANT: persist the messages to your own store BEFORE you pass the
    advanced cursors back - a cursor means "durably received", not "task
    done". NEVER mix cursor values between stores, and NEVER fall back to the
    timestamp `listen` once you use cursors (silent cross-store mis-archiving
    would return)."""
    err = _entry_error((validation.validate_session_id, session_id),
                       (validation.validate_cursors, cursors))
    if err:
        return {"messages": [], "next_cursors": {}, "error": err}
    return user_functions.listen_v2(session_id, cursors, timeout)


@mcp.tool()
def query_my_cursors(session_id: str) -> dict:
    """Recover your per-store cursors ({store_id: sequence}) from the kernels
    (local + host merged). Call after a compact / long gap / kernel restart,
    then pass the result as `cursors` on your next listen_v2."""
    err = _entry_error((validation.validate_session_id, session_id))
    if err:
        return {"error": err}
    return user_functions.query_my_cursors(session_id)
```
`close_connection` 工具签名改为 `def close_connection(session_id: str, toid: str, acked_ts: int = 0, cursors: dict = None) -> dict:`，docstring 末尾加：`Pass your latest per-store cursors as cursors (from listen_v2 / query_my_cursors) if you are on the v2 protocol; each cursor is uploaded only to the kernel that owns that store.`；入口检查加 `(validation.validate_cursors, cursors)`；转发改为 `return user_functions.close_connection(session_id, toid, acked_ts, cursors)`。

- [ ] **Step 7: SKILL.md（双域）**

在 `## The ACK watermark (read this before listening)` 章节**之前**插入新章节：
```markdown
## The cursor ACK (v2 - PREFERRED; read this before listening)

`listen_v2` ACKs with **per-store cursors**, not timestamps. A cursor says
"everything up to this sequence FROM THIS STORE is durably received".

- **First listen_v2**: call `query_my_cursors(sid)` (or pass `{}`) to get
  `{store_id: sequence}`, then `listen_v2(sid, cursors, timeout)`.
- It returns `{messages, next_cursors}`. Each message is a record:
  `{message_id, store_id, sequence, from_session, to_session, kind,
  correlation_id, created_at_ms, payload: {text}}`.
- **Persist first, then advance**: write the messages to your own store /
  context BEFORE passing the advanced `next_cursors` back. A cursor means
  "durably received", NOT "task done".
- **Next listen_v2**: pass the returned `next_cursors` unchanged. NEVER mix
  cursor values between stores, NEVER reduce them to one number, and NEVER
  fall back to the timestamp `listen` once you use cursors (that silently
  re-enables cross-store mis-archiving).
- **Duplicates are possible** (at-least-once): dedup on `message_id`.
- **If you lose your cursors** (compact / long gap / restart): call
  `query_my_cursors(sid)`.
- **On close**: pass your latest cursors to `close_connection(sid, toid,
  cursors=...)` so the kernels persist them.
- The legacy timestamp `listen` below remains for ONE release to drain
  pre-upgrade `.md` messages; do not use it for new conversations.
```
把原 `## The ACK watermark ...` 标题改为 `## The ACK watermark (LEGACY timestamp mode - being phased out)`，并在其首段后加一句：`This mode is kept for one release to drain pre-upgrade .md messages. New work uses listen_v2 (see "The cursor ACK" above).` 工具参考区（`### Listening`）在 `listen` 条目上方加：
```markdown
- `listen_v2(session_id, cursors=None, timeout=30) -> dict` - PREFERRED.
  BLOCKING. Returns `{messages, next_cursors}`; pass `next_cursors` back
  unchanged on the next call. See "The cursor ACK" above.
- `query_my_cursors(session_id) -> dict` - Recover `{store_id: sequence}`
  after compact/restart.
```
`close_connection` 条目补 `cursors=None` 参数说明。`### Listening` 标题改为 `### Listening (cursor ACK v2 preferred - see "The cursor ACK" above)`。

- [ ] **Step 8: 全套件 + parity 跑绿**

Run: `py -3 -m pytest -v` → 全 PASS。
Run: `py -3 tools/check_parity.py` → PARITY OK。

- [ ] **Step 9: Commit**

```bash
git add v2_win v2_wsl tests
git commit -m "feat(HP-02): per-store cursor ACK (listen_v2 + query_my_cursors)

- cursors.json state (schema_version 1); legacy ack_timestamps untouched
  (explicit migration point - no timestamp->sequence conversion)
- kernel listen_scan_v2/query_cursors/upload_cursor; archive rule is
  store-local sequence<=cursor only
- user_functions listen_v2/query_my_cursors merge local+host stores without
  mixing cursor values; close_connection uploads per-store cursors
- MCP tools listen_v2/query_my_cursors; listen/query_my_ACK_timestamp marked
  LEGACY (one-release deprecation window); SKILL.md cursor ACK chapter"
```

---
### Task 4: HP-03 RPC operation_id + operation journal + 领域幂等

**Files:**
- Create: `v2_win/cc-communicate/server/operation_journal.py`（双域，纯模块，下同）
- Modify: `v2_win/cc-communicate/server/paths.py`（+OPERATION_JOURNAL_FILE）
- Modify: `v2_win/cc-communicate/server/rpc_client.py`（operation_id 贯穿 call/call_remote/submit_remote_noblock）
- Modify: `v2_win/cc-communicate/server/kernel.py`（journal 状态 + drain_queue 查/记）
- Modify: `v2_win/cc-communicate/server/kernel_api.py`（withdraw 按 message_id 模式）
- Modify: `v2_win/cc-communicate/server/user_functions.py`（_send 生成 message_id + operation_id）
- Modify: `v2_win/cc-communicate/server/mcp_server.py`（withdraw 加 message_id 参数）
- Create: `tests/unit/test_rpc_idempotency.py`

**Interfaces:**
- Consumes: Task 2 的 message_id/`_find_message_file`、Task 3 的 upload_cursor。
- Produces（Wave 2 依赖）:
  - `operation_journal.load(path) -> dict`、`save(path, operations)`、`completed_result(operations, operation_id) -> (bool, result)`、`record_completed(operations, operation_id, function, result)`
  - `rpc_client.call/call_remote(..., operation_id=None)`、`submit_remote_noblock(..., operation_id=None)`
  - `kernel.operation_journal`（模块级 dict）、`paths.OPERATION_JOURNAL_FILE`
  - `kernel_api.withdraw(..., message_id=None)`；MCP `withdraw(fromid, toid, init_connect=0, message_id=None)`
  - 队列请求 schema：`{"request_id","function","args","operation_id"?}`（旧 kernel 忽略未知字段，前向兼容）

**范围声明（审核已锁定）：** Wave 1 的 HP-03 = operation_id + journal + 天然幂等 mutation + 经 HP-01 message_id 的 send 去重 + withdraw-by-id；**spawn/evoke 的跨 crash-window 去重随 Wave 2 HP-04（spawn_token）完成**。journal 覆盖的 mutation 集合见 `JOURNALED_FUNCTIONS`；listen 扫描高频且天然幂等（同 cursor 重扫无害），不入 journal。

- [ ] **Step 1: 写失败测试 `tests/unit/test_rpc_idempotency.py`**

```python
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
    assert resp == {"request_id": "d1", "result": "ok", "error": None}
    assert os.listdir(str(server.paths.QUEUE_DIR)) == []
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
    assert _read_response(server.paths.QUEUE_RESPONSES_DIR, "r1")["result"] == "ok"
    assert _read_response(server.paths.QUEUE_RESPONSES_DIR, "r2")["result"] == "ok"
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
    assert "withdrew" in r1
    files = _pipe_files(server)
    assert len(files) == 1 and files[0].endswith("__" + "b" * 32 + ".json")
    r2 = ka.withdraw(k.alive_conversations, "alice", "bob", 0, message_id="a" * 32)
    assert "no message" in r2  # already-done，不报错、不误删 m2
    assert len(_pipe_files(server)) == 1


def test_operation_id_written_to_queue_files(server, tmp_path):
    """local 与 remote 提交都携带稳定 operation_id。"""
    server.paths.ensure_runtime_dirs()
    rc = server.rpc_client if hasattr(server, "rpc_client") else None
    if rc is None:
        import importlib
        rc = importlib.import_module("rpc_client")
    rid = rc._submit("send_message", {"x": 1}, operation_id="op-local-1")
    files = os.listdir(str(server.paths.QUEUE_DIR))
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
    rc = server.rpc_client  # fixture 已 reload（tmp 路径），勿 importlib 裸取真实 paths
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
```

注意：`rpc_client` import 时只绑定 `QUEUE_DIR`/`QUEUE_RESPONSES_DIR`（from paths import）——直接 importlib.import_module 绑定的是**真实** paths。`test_operation_id_written_to_queue_files` 因此必须经 `server.rpc_client`……但 fixture 没有 reload rpc_client。**本任务需要把 fixture 的 reload 列表扩展**：在 `tests/conftest.py` 的列表中 `"machine_identity"` 后插入 `"rpc_client"`（rpc_client from-import QUEUE_DIR/QUEUE_RESPONSES_DIR，须随 paths 重解析；它同时 from check_core import ensure_core——check_core 也绑定 SERVER_DATA_DIR，一并加 `"check_core"` 在 `"rpc_client"` 前）。更新后既有测试须仍全绿。

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3 -m pytest tests/unit/test_rpc_idempotency.py -v`
Expected: FAIL——`operation_journal` 模块不存在、`_submit` 不收 operation_id、`withdraw` 不收 message_id、`call` 无 operation_id 参数、fixture 无 `rpc_client` 属性。

- [ ] **Step 3: 新建 `server/operation_journal.py`（双域，纯模块）**

```python
"""Operation journal (HP-03): bounded, persistent record of completed
mutations keyed by operation_id.

    request_id   = one transport attempt (unique per queue submission)
    operation_id = stable identity of ONE logical operation across all retries

A retry whose operation_id is journaled as completed REPLAYS the recorded
result WITHOUT re-executing the side effect. The journal is the fast path;
the domain objects (message_id in filenames now, spawn_token registry in
Wave 2) are the crash-surviving source of truth - after a crash between
side-effect and journal write, domain dedup (not the journal) prevents a
duplicate. That residual crash-window is documented in the master plan R-list.

Bounds: TTL 24h + max 1000 entries, pruned on every save. Only "completed"
entries are ever recorded in Wave 1; the prune rules never remove a
non-completed entry (future-proof).

Pure module: the journal file path comes in as a parameter (test isolation).
"""
from __future__ import annotations

import json
import time

import fileutil

SCHEMA_VERSION = 1
TTL_MS = 24 * 3600 * 1000
MAX_ENTRIES = 1000


def load(path: str) -> dict:
    """-> {operation_id: entry}. Tolerant: any read problem -> empty journal."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("operations"), dict):
        return data["operations"]
    return {}


def save(path: str, operations: dict):
    _prune(operations)
    fileutil.atomic_write_json(
        path, {"schema_version": SCHEMA_VERSION, "operations": operations})


def completed_result(operations: dict, operation_id: str):
    """-> (hit: bool, result). Only completed entries replay."""
    entry = operations.get(operation_id)
    if isinstance(entry, dict) and entry.get("status") == "completed":
        return True, entry.get("result")
    return False, None


def record_completed(operations: dict, operation_id: str, function: str, result):
    operations[operation_id] = {
        "function": function,
        "status": "completed",
        "result": result,
        "completed_at_ms": int(time.time() * 1000),
    }


def _prune(operations: dict):
    now = int(time.time() * 1000)
    stale = [k for k, e in operations.items()
             if isinstance(e, dict) and e.get("status") == "completed"
             and now - int(e.get("completed_at_ms", 0) or 0) > TTL_MS]
    for k in stale:
        del operations[k]
    if len(operations) > MAX_ENTRIES:
        completed = sorted(
            (int(e.get("completed_at_ms", 0) or 0), k)
            for k, e in operations.items()
            if isinstance(e, dict) and e.get("status") == "completed")
        for _ts, k in completed[:len(operations) - MAX_ENTRIES]:
            del operations[k]
```

- [ ] **Step 4: paths.py + rpc_client.py（双域）**

`paths.py` 加：
```python
OPERATION_JOURNAL_FILE = os.path.join(SERVER_DATA_DIR, 'operation_journal.json')  # HP-03 mutation journal
```

`rpc_client.py` 改动（4 处签名 + req 构造）：
```python
def _submit(function: str, args: dict, operation_id: str = None) -> str:
    rid = uuid.uuid4().hex
    req = {"request_id": rid, "function": function, "args": args}
    if operation_id:
        req["operation_id"] = operation_id   # HP-03: stable across retries
    name = f"{int(time.time() * 1000):013d}_{rid}.json"
    tmp = os.path.join(QUEUE_DIR, name + ".tmp")
    final = os.path.join(QUEUE_DIR, name)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f)
    os.replace(tmp, final)
    return rid


def call(function: str, args: dict | None = None, timeout: float = _DEFAULT_TIMEOUT,
         operation_id: str = None):
    """Call a LOCAL kernel function. Raises KernelError on error/timeout (one
    retry, core_plan #11c). HP-03: both attempts share ONE operation_id
    (generated here when not given) while each gets a fresh request_id, so a
    retry whose first attempt actually executed replays the journaled result
    instead of re-running the side effect."""
    if args is None:
        args = {}
    operation_id = operation_id or uuid.uuid4().hex
    ensure_runtime_dirs()
    last_rid = None
    for attempt in (1, 2):
        if not ensure_core():
            if attempt == 2:
                raise KernelError("kernel not alive; could not start it")
            time.sleep(_POLL_INTERVAL)
            continue
        rid = _submit(function, args, operation_id)
        ...（其余不变）


def _submit_remote(rqueue: str, function: str, args: dict,
                   operation_id: str = None) -> str:
    from machine_identity import local_type
    prefix = local_type().replace("-", "_")
    rid = f"{prefix}_{uuid.uuid4().hex}"
    req = {"request_id": rid, "function": function, "args": args}
    if operation_id:
        req["operation_id"] = operation_id
    ...（其余不变）


def call_remote(machine: dict, function: str, args: dict | None = None,
                timeout: float = _DEFAULT_TIMEOUT, operation_id: str = None):
    """...（原 docstring + 'HP-03: attempts share one operation_id.'）"""
    if args is None:
        args = {}
    operation_id = operation_id or uuid.uuid4().hex
    ensure_runtime_dirs()
    rqueue = os.path.join(machine["data_dir"], "queue")
    rresp = os.path.join(rqueue, "responses")
    for attempt in (1, 2):
        rid = _submit_remote(rqueue, function, args, operation_id)
        ...（其余不变）


def submit_remote_noblock(machine: dict, function: str, args: dict | None = None,
                          operation_id: str = None):
    if args is None:
        args = {}
    ensure_runtime_dirs()
    try:
        rqueue = os.path.join(machine["data_dir"], "queue")
        _submit_remote(rqueue, function, args, operation_id)
    except Exception:
        pass
```

- [ ] **Step 5: kernel.py journal 接入（双域）**

import 区加 `import operation_journal`。paths import 行加 `OPERATION_JOURNAL_FILE`。模块级 state 加：
```python
operation_journal: dict = {}  # HP-03: operation_id -> completed mutation record
```
`_save_cursors` 后加：
```python
def _load_operation_journal():
    operation_journal.clear()
    operation_journal.update(operation_journal_mod.load(OPERATION_JOURNAL_FILE))
    log.info("loaded operation journal: %d entries", len(operation_journal))
```
（import 时用 `import operation_journal as operation_journal_mod` 避免与 state dict 同名冲突。）
`main()`：`_load_cursors()` 行后加 `_load_operation_journal()`。
`drain_queue` **替换整个函数**：
```python
# HP-03: mutations whose retry must not re-execute side effects. High-frequency
# scans (listen_scan/_v2) are naturally idempotent (same cursor rescan is
# harmless) and are excluded to keep journal churn low. spawn/evoke are
# journaled for the rpc-retry window; their cross-crash-window dedup lands with
# HP-04 spawn_token (Wave 2).
_JOURNALED_FUNCTIONS = frozenset({
    "send_message", "register_conversation", "unregister_conversation",
    "withdraw", "create_conversation_folder", "upload_ack_timestamp",
    "upload_cursor", "collect_messages", "spawn_cc_new", "spawn_cc_resume",
    "evoke", "kernel_terminate",
})


def drain_queue() -> bool:
    try:
        files = sorted(os.listdir(QUEUE_DIR))
    except FileNotFoundError:
        return False
    reqs = [f for f in files if f.endswith(".json")]
    for fname in reqs:
        path = os.path.join(QUEUE_DIR, fname)
        try:
            req = _read_json(path)
        except OSError:
            # Transient read error (e.g. AV scan / write race on Windows) - leave
            # the file for the next cycle instead of crashing the kernel. (T12)
            continue
        try:
            if not req or "function" not in req or "request_id" not in req:
                raise ValueError("malformed request")
            function = req["function"]
            op_id = req.get("operation_id")
            journaled = op_id and function in _JOURNALED_FUNCTIONS
            if journaled:
                hit, replay = operation_journal_mod.completed_result(
                    operation_journal, op_id)
                if hit:
                    # HP-03: retry of a completed operation - replay the
                    # recorded result WITHOUT re-executing the side effect.
                    resp = {"request_id": req["request_id"], "result": replay,
                            "error": None}
                    _write_response_and_consume(resp, path)
                    continue
            result = _dispatch(function, req.get("args") or {})
            resp = {"request_id": req["request_id"], "result": result, "error": None}
            if journaled:
                operation_journal_mod.record_completed(
                    operation_journal, op_id, function, result)
                operation_journal_mod.save(OPERATION_JOURNAL_FILE, operation_journal)
        except Exception as e:
            log.exception("error handling request %s", fname)
            resp = {"request_id": req.get("request_id") if req else None,
                    "result": None, "error": f"{type(e).__name__}: {e}"}
        _write_response_and_consume(resp, path)
    return bool(reqs)


def _write_response_and_consume(resp: dict, req_path: str):
    rid = resp["request_id"]
    if rid is not None:
        os.makedirs(QUEUE_RESPONSES_DIR, exist_ok=True)
        _atomic_write_json(os.path.join(QUEUE_RESPONSES_DIR, rid + ".json"), resp)
    try:
        os.remove(req_path)
    except OSError:
        pass
```

- [ ] **Step 6: kernel_api.withdraw 按 message_id（双域）**

`withdraw` 签名改为 `def withdraw(alive_conversations: dict, fromid: str, toid: str, init_connect: int = 0, message_id: str = None) -> str:`，在 init_connect 分支之后、legacy「撤回最新一条」段之前插入：
```python
    if message_id:
        # HP-03: withdraw an EXPLICIT target (retry-safe). The legacy
        # latest-message mode below is non-idempotent by nature and remains
        # for one release only.
        validation.validate_message_id(message_id)
        d = conversations.conv_dir(fromid, toid)
        found = _find_message_file(d, message_id)
        if not found or os.sep + "log" + os.sep in found:
            return f"no message {message_id} (already withdrawn or never existed)"
        try:
            os.remove(found)
        except OSError:
            return f"no message {message_id} (already withdrawn or never existed)"
        return f"withdrew message {message_id}"
```
`kernel.py` dispatch 的 withdraw 分支改为传 `args.get("message_id")`；`_ARG_VALIDATORS["withdraw"]` 加 `"message_id": validation.validate_message_id`。`mcp_server.py` 的 `withdraw` 工具签名加 `message_id: str = None`，docstring 加：`message_id: withdraw that EXACT message (retry-safe; preferred). Without it, legacy mode withdraws fromid's latest undelivered message (non-idempotent, being phased out).`，入口检查加 `(validation.validate_message_id, message_id)`（`message_id` 为 None 时 `_entry_error` 会调 validator(None)——故此处改为手写：`if message_id is not None: err = _entry_error((validation.validate_message_id, message_id))`；fromid/toid 检查不变），rpc args 加 `"message_id": message_id`。

- [ ] **Step 7: user_functions._send 携带 message_id/operation_id（双域）**

import 区加 `import uuid`。`_send` 改为：
```python
def _send(fromid, toid, message, conv_remote) -> str:
    # HP-01/HP-03: one message_id per LOGICAL send, generated here so every
    # funnel (send_message / connect hello / close notice) gets dedup for
    # free. The rpc layer reuses it as the operation_id, so a transport retry
    # replays the journaled result and a domain retry dedups on the filename.
    mid = uuid.uuid4().hex
    args = {"fromid": fromid, "toid": toid, "message": message, "message_id": mid}
    if conv_remote is None:
        return rpc_client.call("send_message", args, operation_id=mid)
    return rpc_client.call_remote(conv_remote, "send_message", args, operation_id=mid)
```

- [ ] **Step 8: conftest.py fixture 扩展 + 全套件 + parity**

`tests/conftest.py` reload 列表改为：
```python
    for name in ("paths", "result", "validation", "proc", "conversations",
                 "spawn", "machine_identity", "check_core", "rpc_client",
                 "kernel_api", "kernel"):
        mods[name] = importlib.reload(importlib.import_module(name))
```
Run: `py -3 -m pytest -v` → 全 PASS。
Run: `py -3 tools/check_parity.py` → PARITY OK。

- [ ] **Step 9: Commit**

```bash
git add v2_win v2_wsl tests
git commit -m "feat(HP-03): operation_id + bounded journal + domain idempotency

- rpc_client: stable operation_id across retry attempts (local + remote +
  noblock); request_id stays per-attempt
- kernel drain_queue: journal consult (replay) / record (completed) for
  _JOURNALED_FUNCTIONS; journal persisted with TTL 24h + 1000-entry cap
- send dedup: journal replay + HP-01 message_id filename dedup
- withdraw by explicit message_id (retry-safe); legacy latest-mode marked
- fixture reloads check_core/rpc_client for path isolation
- NOT in Wave 1 scope (documented): spawn/evoke cross-crash-window dedup
  (Wave 2 HP-04 spawn_token); listen scans (naturally idempotent)"
```

---

## Wave 1 出口清单（SDD 全部任务完成后由负责者执行，非 subagent 任务）

1. **完整回归**：`py -3 -m pytest -v` 全绿；`CC_TEST_SERVER_DIR=<repo>/v2_wsl/cc-communicate/server py -3 -m pytest -v` 全绿（WSL 侧经 git-bash 调用时注意路径转换，沿用 Gate 0 的方式）。
2. **parity gate**：`py -3 tools/check_parity.py` PASS。
3. **Windows live smoke**：真实 plugin 环境（非 tmp）——send → listen_v2 收 record（含 message_id/sequence/store_id）→ close_connection(cursors=...) → kernel 重启后 query_my_cursors 恢复。
4. **WSL/cross-realm live**：WSL caller 对 host store 的 listen_v2 双 store 合并 + 独立 cursor；**R2 验证**：`/mnt/c`（DrvFs）与 `//wsl.localhost`（9P）上 rename 原子性——reader 不见 partial（正确性锚点）；fsync 持久性较弱属已知残留（R2，兜底 = counter + message_id）。
5. **交付说明更新**：向 master plan §4 交付契约追加 Wave 1 已实现项（listen_v2/query_my_cursors、record 信封、INVALID_ARGUMENT、operation_id 幂等）与明确的残留风险（crash-window 重复可检测不静默、legacy listen 一个 release 后删除、spawn/evoke 跨 crash 去重待 Wave 2）。
6. **进度落账**：`.superpowers/sdd/progress.md` 记录每任务 commit 区间、review 结论、Minor findings、carry-forward 到 Wave 2。

## Self-Review 记录（计划作者已核对）

- **Spec 覆盖**：HP-06（Task 1）、HP-01（Task 2）、HP-02（Task 3）、HP-03（Task 4）全条款 → 任务映射完整；D1/D6/D7 落实；7 条 carry-forward 全部吸收（dispatch 测试 T4、幂等测试 T4、end 回放 T1、格式锁定 T1+T2、fixture reload T1+T4、proc AttributeError T1、parity compared 计数 T1）。
- **类型一致性**：`send_message` 新签名在 kernel dispatch / user_functions / 全部测试一致；`listen_scan_v2` 返回 `{"store_id","messages","next_cursor"}` vs `listen_v2` 返回 `{"messages","next_cursors"}`（不同层、不同名，已显式区分）；`parse_any_pipe_filename` 字段名在 kernel_api/user_functions/测试一致；fixture 属性名（server.rpc_client/server.validation 等）与 reload 列表一致。
- **Placeholder 扫描**：无 TBD/TODO；所有代码块为完整可实现代码。
