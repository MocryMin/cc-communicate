# Gate 0 — 测试基线 + Data-Root Override + Parity Gate 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可重复的自动化测试基线（隔离临时 data root）、`CC_COMMUNICATE_DATA_DIR` override（paths.py + paths.js 双边）、win/wsl parity gate——为 Wave 1 的高危协议变更提供回归安全网。

**Architecture:** 在 `<repo>/tests/` 建 pytest 套件。关键机制：`paths.py`/`paths.js` 的 `DATA_DIR` 支持 `CC_COMMUNICATE_DATA_DIR` override（`SESSION_CTRL_DIR` 等全部由 `DATA_DIR` 派生，override 一处即级联）；conftest fixture 通过「设 env + 按依赖序 reload 路径相关模块」把每个测试绑定到独立 `tmp_path`。parity gate 用 sha256 比对两棵 plugin 树，allow-list 仅平台入口。

**Tech Stack:** Python 3.10+，pytest，psutil（插件既有 runtime dep），Node（parity 不需要；session_ctrl replay 测试直接写 event JSON，不起 node）。

**Repo layout（相对 `<repo>` = 本仓库根，含 `v2_win/`、`v2_wsl/`）：**
- 测试树：`<repo>/tests/`（新建，**不属于任一 plugin 树**，故不参与 parity）
- 被测源码：`<repo>/v2_win/cc-communicate/server/`（parity gate 保证 `v2_wsl` 等价）
- parity 工具：`<repo>/tools/check_parity.py`（新建，repo 级，不在 plugin 树内）

## Global Constraints

- 测试**绝不**写任一已安装 plugin 的真实 `data/`——全部经 `CC_COMMUNICATE_DATA_DIR` 隔离到 `tmp_path`。
- 被测模块以顶层名互 import（`import conversations`、`from paths import ...`），故 conftest 必须把 `v2_win/cc-communicate/server` 放到 `sys.path[0]`。
- 默认对 `v2_win` 跑套件；环境变量 `CC_TEST_SERVER_DIR` 可切到 `v2_wsl` 重跑（parity gate 保证等价）。
- 只固化**外部契约与已确认不变量**（见下），不固定内部轮询次数、日志文本、精确 sleep。
- 改动两域同步：`paths.py` 改 `v2_win` 后，同一改动落到 `v2_wsl`（parity gate 会强制）。
- 依赖：`pytest>=7`、`psutil`。从 `<repo>` 根运行 `pytest`。

已确认的签名（实现者直接照用，源自代码核实）：
- `kernel_api.register_conversation(alive_conversations: dict, sid_a: str, sid_b: str)`
- `kernel_api.send_message(alive_conversations: dict, fromid: str, toid: str, message: str) -> str`（返回 `"message_sent at <ts>"`）
- `kernel_api.listen_scan(acked_timestamps: dict, sid: str, acked_ts: int) -> dict`（返回 `{"messages":[{"time","from_id","message"}...], "watermark": int}`）
- `kernel_api.upload_ack_timestamp(acked_timestamps: dict, sid: str, ts: int) -> int`
- `kernel_api.query_ack_timestamp(acked_timestamps: dict, sid: str) -> int`
- `kernel._save_sessions()/_load_sessions()/_save_alive_convs()/_load_alive_convs()/_save_ack_timestamps()/_load_ack_timestamps()/process_session_ctrl_event()`
- `conversations.conv_dir(sid_a, sid_b)`、`conversations.SEP == "__"`
- 模块级 state：`kernel.sessions`、`kernel.alive_conversations`、`kernel.acked_timestamps`

---

### Task 1: Data-Root Override（paths.py + paths.js 双边）+ override 测试

**Files:**
- Modify: `v2_win/cc-communicate/server/paths.py:16-19`
- Modify: `v2_win/cc-communicate/scripts/lib/paths.js:12-14`
- Modify: `v2_wsl/cc-communicate/server/paths.py`（同 v2_win 的改动）
- Modify: `v2_wsl/cc-communicate/scripts/lib/paths.js`（同 v2_win 的改动）
- Create: `tests/conftest.py`
- Create: `tests/unit/test_data_root_override.py`

**Interfaces:**
- Consumes: 现有 `paths.py`/`paths.js` 的 `DATA_DIR` 常量。
- Produces: `CC_COMMUNICATE_DATA_DIR` 环境变量 override；`tests/conftest.py` 提供 `SERVER_DIR` 常量并把 server 放上 `sys.path`（后续所有任务依赖）。

- [ ] **Step 1: 写失败的 override 测试 + 最小 conftest（sys.path）**

`tests/conftest.py`:
```python
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# 被测 realm 的 server 目录。parity gate（tests/parity）证明两树等价，故测一个即
# 覆盖两个；设 CC_TEST_SERVER_DIR 可对另一 realm 重跑。
SERVER_DIR = Path(os.environ.get(
    "CC_TEST_SERVER_DIR",
    REPO_ROOT / "v2_win" / "cc-communicate" / "server",
)).resolve()
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
```

`tests/unit/test_data_root_override.py`:
```python
import importlib
import os


def test_data_dir_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CC_COMMUNICATE_DATA_DIR", str(tmp_path))
    import paths
    importlib.reload(paths)
    assert paths.DATA_DIR == os.path.abspath(str(tmp_path))
    assert paths.SESSION_CTRL_DIR == os.path.join(paths.DATA_DIR, "session_ctrl")
    assert paths.CONVERSATIONS_DIR == os.path.join(paths.DATA_DIR, "conversations")


def test_data_dir_default_when_unset(monkeypatch):
    monkeypatch.delenv("CC_COMMUNICATE_DATA_DIR", raising=False)
    import paths
    importlib.reload(paths)
    assert paths.DATA_DIR == os.path.join(paths.PLUGIN_ROOT, "data")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_data_root_override.py -v`
Expected: FAIL（`test_data_dir_override` 中断言不成立——`paths.py` 尚不识别该 env var，`DATA_DIR` 仍指向 plugin 根）。`test_data_dir_default_when_unset` 应通过。

- [ ] **Step 3: 实现 paths.py override（v2_win）**

`v2_win/cc-communicate/server/paths.py` 把 16-19 行：
```python
# --- shared with paths.js (keep in sync) ------------------------------------
DATA_DIR         = os.path.join(PLUGIN_ROOT, 'data')
SESSION_CTRL_DIR = os.path.join(DATA_DIR, 'session_ctrl')   # append-only event log (lower layer writes)
DEBUG_FILE       = os.path.join(DATA_DIR, 'debug.log')
```
改为：
```python
# --- shared with paths.js (keep in sync) ------------------------------------
# CC_COMMUNICATE_DATA_DIR overrides the data root (tests / custom installs, HP-11).
# SESSION_CTRL_DIR/DEBUG_FILE and every upper-layer dir derive from DATA_DIR, so
# overriding this single constant cascades everywhere. Keep in sync with paths.js.
_data_root_override = os.environ.get('CC_COMMUNICATE_DATA_DIR')
DATA_DIR         = os.path.abspath(_data_root_override) if _data_root_override else os.path.join(PLUGIN_ROOT, 'data')
SESSION_CTRL_DIR = os.path.join(DATA_DIR, 'session_ctrl')   # append-only event log (lower layer writes)
DEBUG_FILE       = os.path.join(DATA_DIR, 'debug.log')
```

- [ ] **Step 4: 实现 paths.js override（v2_win）**

`v2_win/cc-communicate/scripts/lib/paths.js` 把 12-14 行：
```js
const DATA_DIR         = path.join(PLUGIN_ROOT, 'data');
const SESSION_CTRL_DIR = path.join(DATA_DIR, 'session_ctrl'); // append-only event log
const DEBUG_FILE       = path.join(DATA_DIR, 'debug.log');
```
改为：
```js
// CC_COMMUNICATE_DATA_DIR overrides the data root (tests / custom installs, HP-11).
// SESSION_CTRL_DIR/DEBUG_FILE derive from DATA_DIR, so overriding cascades. Keep in
// sync with server/paths.py.
const _dataRootOverride = process.env.CC_COMMUNICATE_DATA_DIR;
const DATA_DIR         = _dataRootOverride ? path.resolve(_dataRootOverride) : path.join(PLUGIN_ROOT, 'data');
const SESSION_CTRL_DIR = path.join(DATA_DIR, 'session_ctrl'); // append-only event log
const DEBUG_FILE       = path.join(DATA_DIR, 'debug.log');
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/unit/test_data_root_override.py -v`
Expected: PASS（两个测试都过）。

- [ ] **Step 6: 把同一改动同步到 v2_wsl 两文件**

对 `v2_wsl/cc-communicate/server/paths.py` 与 `v2_wsl/cc-communicate/scripts/lib/paths.js` 施加与 Step 3/4 完全相同的编辑（parity 要求 byte-identical）。

- [ ] **Step 7: Commit**

```bash
git add v2_win/cc-communicate/server/paths.py v2_win/cc-communicate/scripts/lib/paths.js \
        v2_wsl/cc-communicate/server/paths.py v2_wsl/cc-communicate/scripts/lib/paths.js \
        tests/conftest.py tests/unit/test_data_root_override.py
git commit -m "feat(HP-00/HP-11): CC_COMMUNICATE_DATA_DIR override (paths.py+paths.js, both realms)"
```

---

### Task 2: 测试脚手架（server fixture + pytest.ini）+ smoke test

**Files:**
- Modify: `tests/conftest.py`（追加 fixture）
- Create: `pytest.ini`
- Test: `tests/unit/test_scaffolding_smoke.py`

**Interfaces:**
- Consumes: Task 1 的 `SERVER_DIR`、override。
- Produces: `server` fixture——返回 `SimpleNamespace(data_root, paths, conversations, spawn, kernel_api, kernel)`，把每个测试绑定到独立 `tmp_path`（后续 Task 3 全部依赖）。

- [ ] **Step 1: 写失败的 smoke test**

`tests/unit/test_scaffolding_smoke.py`:
```python
def test_server_fixture_isolates_data_root(server):
    # fixture 把 DATA_DIR 绑到本测试独立 tmp_path，且各模块绑定一致
    assert server.paths.DATA_DIR == __import__("os").path.abspath(str(server.data_root))
    # kernel_api 与 conversations 的 CONVERSATIONS_DIR 都落在同一隔离 root 下
    assert server.kernel_api.CONVERSATIONS_DIR == server.conversations.CONVERSATIONS_DIR
    assert str(server.data_root) in server.kernel_api.CONVERSATIONS_DIR
    # 隔离 root 初始为空（不污染真实 plugin data/）
    assert list(server.data_root.iterdir()) == []
```

- [ ] **Step 2: 跑测试确认失败（fixture 不存在）**

Run: `pytest tests/unit/test_scaffolding_smoke.py -v`
Expected: ERROR/FAIL（`server` fixture 未定义）。

- [ ] **Step 3: 在 conftest.py 追加 server fixture + 写 pytest.ini**

`tests/conftest.py` 在现有内容后追加：
```python
import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """把 cc-communicate server 绑定到本测试独立的 tmp data root。

    设 CC_COMMUNICATE_DATA_DIR 后按依赖序 reload 路径相关模块，使 import 时绑定
    的路径常量（DATA_DIR/CONVERSATIONS_DIR/...）重新解析到 tmp_path。返回命名空间
    暴露重载后的模块与该 root。kernel_api 函数以 state dict 为首参，直接调用即可。"""
    monkeypatch.setenv("CC_COMMUNICATE_DATA_DIR", str(tmp_path))
    mods = {}
    for name in ("paths", "conversations", "spawn", "kernel_api", "kernel"):
        mods[name] = importlib.reload(importlib.import_module(name))
    return SimpleNamespace(data_root=tmp_path, **mods)
```

`pytest.ini`（`<repo>` 根）:
```ini
[pytest]
testpaths = tests
addopts = -v
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/test_scaffolding_smoke.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py pytest.ini tests/unit/test_scaffolding_smoke.py
git commit -m "test(HP-00): pytest scaffolding + isolated-data-root server fixture"
```

---

### Task 3: 锁定 v0.3 关键行为（roundtrip / cancel-redelivery / restart-recovery / session-replay）

**Files:**
- Test: `tests/unit/test_message_roundtrip.py`
- Test: `tests/unit/test_cancel_redelivery.py`
- Test: `tests/unit/test_kernel_restart.py`
- Test: `tests/unit/test_session_ctrl_replay.py`

**Interfaces:**
- Consumes: Task 2 的 `server` fixture；Global Constraints 列的签名。
- Produces: 可重复证明「取消不丢消息」「kernel restart 可恢复」的基线测试（HP-00 验收核心）。

- [ ] **Step 1: 写四个失败测试文件**

`tests/unit/test_message_roundtrip.py`:
```python
import os


def test_register_send_listen_ack_roundtrip(server):
    ka = server.kernel_api
    convs, acked = {}, {}
    ka.register_conversation(convs, "alice", "bob")
    r = ka.send_message(convs, "alice", "bob", "hello")
    assert r.startswith("message_sent at ")

    # bob 以 acked_ts=0 listen -> peek 到消息但不归档
    res = ka.listen_scan(acked, "bob", 0)
    assert [m["message"] for m in res["messages"]] == ["hello"]
    assert res["messages"][0]["from_id"] == "alice"
    wm = res["watermark"]

    # bob 确认 watermark -> 再次 listen 归档该消息且无新消息
    res2 = ka.listen_scan(acked, "bob", wm)
    assert res2["messages"] == []

    # 消息已从 pipe/ 移到 log/
    d = server.conversations.conv_dir("alice", "bob")
    assert os.listdir(os.path.join(d, "pipe")) == []
    assert len(os.listdir(os.path.join(d, "log"))) == 1


def test_send_requires_registration(server):
    ka = server.kernel_api
    convs = {}
    r = ka.send_message(convs, "alice", "bob", "hi")
    assert r == "failed, connection not registered"
```

`tests/unit/test_cancel_redelivery.py`:
```python
def test_cancel_listen_redelivers(server):
    ka = server.kernel_api
    convs, acked = {}, {}
    ka.register_conversation(convs, "alice", "bob")
    ka.send_message(convs, "alice", "bob", "m1")

    # 第一次 listen：CC peek 到消息（但在确认前被 cancel）
    res1 = ka.listen_scan(acked, "bob", 0)
    assert [m["message"] for m in res1["messages"]] == ["m1"]

    # CC 取消后未推进 watermark，以相同 acked_ts=0 重 listen -> 消息重投，不丢
    res2 = ka.listen_scan(acked, "bob", 0)
    assert [m["message"] for m in res2["messages"]] == ["m1"]
```

`tests/unit/test_kernel_restart.py`:
```python
def test_kernel_restart_recovers_state(server):
    k = server.kernel
    k.sessions.update({"s1": {"session_id": "s1", "pid": 123}})
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    k.acked_timestamps["s1"] = 42
    k._save_sessions()
    k._save_alive_convs()
    k._save_ack_timestamps()

    # 模拟重启：清空内存态，从磁盘恢复
    k.sessions.clear()
    k.alive_conversations.clear()
    k.acked_timestamps.clear()
    k._load_sessions()
    k._load_alive_convs()
    k._load_ack_timestamps()

    assert "s1" in k.sessions
    assert ("a", "b") in k.alive_conversations
    assert k.acked_timestamps["s1"] == 42
```

`tests/unit/test_session_ctrl_replay.py`:
```python
import json
import os


def test_session_ctrl_start_replay(server):
    k = server.kernel
    ev_dir = server.paths.SESSION_CTRL_DIR
    os.makedirs(ev_dir, exist_ok=True)
    sid = "sess-xyz"
    event = {
        "event": "start", "event_ts": 1000, "session_id": sid, "pid": 999,
        "cwd": "/tmp/x", "start_time": 1700000000.0, "source": None,
    }
    with open(os.path.join(ev_dir, "start_1000_%s.json" % sid), "w", encoding="utf-8") as f:
        json.dump(event, f)

    k.process_session_ctrl_event()
    assert sid in k.sessions
    assert k.sessions[sid]["pid"] == 999
```

- [ ] **Step 2: 跑四个测试确认行为（应全部通过——它们锁定的是 v0.3 现有正确行为）**

Run: `pytest tests/unit/test_message_roundtrip.py tests/unit/test_cancel_redelivery.py tests/unit/test_kernel_restart.py tests/unit/test_session_ctrl_replay.py -v`
Expected: PASS。
> 注意：这些测试固化的是**当前正确行为**（HP-00「固化现状」），所以它们现在就应通过——它们的价值是 Wave 1 改动时的回归网。若某个失败，说明对现状理解有误，先修正理解（不是改实现）。

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_message_roundtrip.py tests/unit/test_cancel_redelivery.py \
        tests/unit/test_kernel_restart.py tests/unit/test_session_ctrl_replay.py
git commit -m "test(HP-00): lock v0.3 behaviors (roundtrip, cancel-redelivery, restart-recovery, session-replay)"
```

---

### Task 4: Parity Gate（win/wsl 源码一致性）

**Files:**
- Create: `tools/check_parity.py`
- Test: `tests/parity/test_parity.py`

**Interfaces:**
- Consumes: `v2_win/cc-communicate` 与 `v2_wsl/cc-communicate` 两棵树。
- Produces: `tools/check_parity.py`（exit 0=parity OK / 1=FAIL）；pytest wrapper 断言其通过。后续每次改两域都必须过此 gate。

- [ ] **Step 1: 写 parity 工具 + 失败测试**

`tools/check_parity.py`:
```python
"""HP-13-B: 若 v2_win 与 v2_wsl plugin 源码在 allow-list 之外有差异则失败。

allow-list 只放真正的平台入口（默认仅 .mcp.json）。运行时数据/缓存/VCS 不参与
比对。 ALLOWLIST 的每一项都必须有理由；首次运行若报告其它合法平台文件，把它
们连同理由加入 ALLOWLIST 后再跑绿。"""
import hashlib
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WIN = REPO / "v2_win" / "cc-communicate"
WSL = REPO / "v2_wsl" / "cc-communicate"

ALLOWLIST = {".mcp.json"}  # 平台 MCP command（win: python / wsl: python3）
EXCLUDE_DIRS = {"data", "__pycache__", ".git", ".pytest_cache", "node_modules"}
EXCLUDE_SUFFIXES = {".pyc", ".log"}


def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _files(root: Path) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1] in EXCLUDE_SUFFIXES:
                continue
            full = Path(dirpath) / fn
            out[full.relative_to(root).as_posix()] = full
    return out


def main() -> int:
    win, wsl = _files(WIN), _files(WSL)
    problems = []
    for rel in sorted(set(win) | set(wsl)):
        if rel in ALLOWLIST:
            continue
        if rel not in win:
            problems.append("only in wsl: " + rel)
        elif rel not in wsl:
            problems.append("only in win: " + rel)
        elif _hash(win[rel]) != _hash(wsl[rel]):
            problems.append("differs: " + rel)
    if problems:
        print("PARITY FAIL:")
        for p in problems:
            print("  " + p)
        return 1
    print("PARITY OK (%d files compared, allowlist=%s)" % (len(win), sorted(ALLOWLIST)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`tests/parity/test_parity.py`:
```python
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_win_wsl_parity():
    r = subprocess.run([sys.executable, str(REPO / "tools" / "check_parity.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, "parity gate failed:\n" + r.stdout + r.stderr
```

- [ ] **Step 2: 跑 parity gate，必要时收敛 ALLOWLIST**

Run: `python tools/check_parity.py`
Expected: 若输出 `PARITY OK` 则直接进 Step 3。若 `PARITY FAIL` 且报告的是**合法平台文件**（如 `hooks/hooks.json` 因 node 路径、`.mcp.json` 因解释器），把该项**连同理由**加入 `ALLOWLIST` 后重跑至绿；若报告的是**本应对称的源码差异**（server/scripts 不同），则是真实分叉，需先对齐两域再跑绿。最终 ALLOWLIST 必须最小且每项有注释理由。

- [ ] **Step 3: 跑 pytest wrapper 确认通过**

Run: `pytest tests/parity/test_parity.py -v`
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add tools/check_parity.py tests/parity/test_parity.py
git commit -m "test(HP-13-B): win/wsl parity gate (hash compare, minimal allowlist)"
```

---

## 完成判定（Gate 0 → Wave 1 的推进门槛）

- `pytest` 单命令从 `<repo>` 根跑绿（unit + parity）。
- 所有测试经 `CC_COMMUNICATE_DATA_DIR` 隔离到 `tmp_path`，不触碰任一真实 plugin `data/`。
- 「取消不丢消息」「kernel restart 可恢复」由 `test_cancel_redelivery.py`/`test_kernel_restart.py` 可重复证明。
- parity gate 绿：win/wsl 非平台文件 byte-identical。
- 对 `v2_wsl` 重跑套件：`CC_TEST_SERVER_DIR=<repo>/v2_wsl/cc-communicate/server pytest` 全绿。

## Self-Review 记录

- **Spec 覆盖**：HP-00（测试基线 + override + 锁定行为）→ Task 1/2/3；HP-13-B（parity gate）→ Task 4；HP-11 的 override 部分 → Task 1。HP-00 验收的「远程唤醒已退出 kernel」「win/wsl 源码等价」分别归 Wave 1 integration 与 Task 4 parity（Gate 0 不重复起真实 kernel 子进程，留待 Wave 1 integration 层）。
- **Placeholder 扫描**：无 TBD/TODO；每个代码步骤含完整代码；ALLOWLIST 的「首次运行收敛」是有判据的发现步骤（非占位）。
- **类型一致性**：fixture 产出 `server.{paths,conversations,spawn,kernel_api,kernel,data_root}`；各测试引用一致；`listen_scan` 返回键 `messages`/`watermark` 与断言一致。
