# cc-communicate WSL2 移植 - 核心设计 (v2)

> **文档定位**：本文件是 cc-communicate 从 Windows-only (v0.1) 扩展到支持
> WSL2 CC 跨 realm 通信的设计文档。`core_plan.md` 是 v0.1 基础设计，本文件
> 是其 v2 扩展。阅读顺序：`core_plan.md` → 本文件。
>
> **验证状态**：本文件中所有技术结论均已实测验证（见 §技术难点）。builder
> 无需重复验证，可直接进入实现。
>
> **目标**：完成后，WSL2 中的 CC 能与主机 CC 通过 cc-communicate 框架实现
> 跨 realm 的互相感知与无缝交流。

---

## 0. 先决架构决策

| 决策 | 结论 | 依据 |
|---|---|---|
| WSL2 CC 类型 | 原生 Linux 进程（非 Windows CC via interop） | `readlink /proc/<pid>/exe` 确认 Linux ELF，路径在 `/home/mocry/` 下 |
| 插件部署方式 | WSL 内独立部署（option B），非共享 `/mnt/c/` | 跨文件系统 I/O 有延迟；kernel 高频轮询需本地 ext4 |
| 阶段划分 | Phase 1: 独立 in-WSL cc-communicate；Phase 2: 跨 realm 通信 | 降低风险，Phase 1 可独立验证 |
| 跨 realm 通信模型 | 双 kernel 各管各机；host kernel 管理跨机流量；WSL kernel 是 host kernel 的 client | 对称、清晰；session_id 全局唯一保证可寻址 |
| 跨机消息存储 | 跨机消息存 host database；WSL 对 host conversations 只读 | host kernel 作为唯一写者，避免竞争；WSL 经 `/mnt/c/` 只读访问 |
| session_id | 全局唯一身份令牌（UUID，碰撞概率可忽略） | CC session_id 是 UUID，跨机碰撞概率为零 |
| connect 归属 | **保持 user-space**（V1 模式），kernel 只做单步非阻塞 RPC | connect 有 60s 阻塞（listen_poller），放入 kernel 会阻塞单线程循环（见 #W4） |
| spawn 方式 (WSL2) | tmux detached session（pty，非 GUI） | WSL2 原生支持 pty；`-p` 模式 CC 会退出，不能用于 evoke（见 #W3） |
| 机器注册 | C:\ 根目录文件握手（filesystem rendezvous） | 零配置；legitimate industry pattern（见 #W9） |
| 机器身份 | 双字段：`type`（语义）+ `id`（UUID，区别） | type 用于路由/显示；UUID 保证唯一 |
| keep_listen | 合并 arm+poller+collect 为单脚本；any-undelivered 语义；settle 3s | arm/collect 是纯文件 I/O，不需要 kernel 状态（见 #W5） |
| 跨 realm 文件 I/O | WSL→host: `/mnt/c/`；host→WSL: `//wsl.localhost/Ubuntu/`（forward-slash） | 双向实测通过（见 #W1） |
| 跨 realm RPC | queue 文件双向直接 I/O，不需要 subprocess | `/mnt/c/` 和 `//wsl.localhost/` 均可读写（见 #W8） |

---

## 1. 架构总览

### 1.1 双 kernel 架构

```
    ┌─────────────────── HOST (Windows) ───────────────────┐
    │                                                       │
    │  ┌────────┐  ┌────────┐          ┌─────────────────┐ │
    │  │ Host CC│  │ Host CC│  ...     │  Host Kernel    │ │
    │  └───┬────┘  └───┬────┘          │  (lazy daemon)  │ │
    │      │ MCP       │ MCP           │  sessions.json  │ │
    │  ┌───▼────┐  ┌───▼────┐          │  alive_sessions │ │
    │  │MCP srv │  │MCP srv │  ──RPC──►│  alive_convs    │ │
    │  └───┬────┘  └───┬────┘          │  conversations/ │ │
    │      └─────┬─────┘                │  queue/         │ │
    │            │                      └────────┬────────┘ │
    │            │                               │          │
    └────────────┼───────────────────────────────┼──────────┘
                 │                               │
    ┌────────────┼──────── WSL2 (Ubuntu) ────────┼──────────┐
    │            │                               │          │
    │  ┌─────────▼────────┐          ┌───────────▼────────┐ │
    │  │  WSL Kernel      │          │  /mnt/c/.../       │ │
    │  │  (lazy daemon)   │◄──RPC────│  host data/        │ │
    │  │  sessions.json   │   (read  │  (WSL 经 /mnt/c/   │ │
    │  │  alive_sessions  │    only  │   访问 host        │ │
    │  │  alive_convs     │    for   │   conversations)   │ │
    │  │  conversations/  │    conv) │                    │ │
    │  │  queue/          │          └────────────────────┘ │
    │  └────────▲─────────┘                                 │
    │           │ RPC                                       │
    │  ┌────────┴────────┐                                  │
    │  │ WSL CC  WSL CC  │  ...                             │
    │  │ MCP srv MCP srv │                                  │
    │  └─────────────────┘                                  │
    └───────────────────────────────────────────────────────┘

    host → WSL 反向 RPC (inform_connect 等):
    host kernel 写 //wsl.localhost/Ubuntu/.../data/queue/
    WSL kernel 本地轮询 data/queue/ → 处理 → 写 responses/
    host kernel 轮询 //wsl.localhost/Ubuntu/.../data/queue/responses/
```

### 1.2 跨 realm 通信通道（全部实测验证）

| 方向 | 文件 I/O | 进程执行 |
|---|---|---|
| WSL → host | `/mnt/c/...` ✅ 读写 | `python.exe <script>` ✅ |
| host → WSL | `//wsl.localhost/Ubuntu/...` ✅ 读写（**forward-slash**） | `wsl.exe -d Ubuntu -- python3 <script>` ✅ |

**关键**：queue RPC 双向都可用直接文件 I/O，不需要 subprocess。`wsl.exe`
只在 spawn 交互式 CC（evoke/create_collaborator）时才需要。

### 1.3 数据存储分布

| 数据 | 存储位置 | 谁写 | 谁读 |
|---|---|---|---|
| Host CC session 信息 | host `data/server/sessions.json` | host kernel | host kernel, WSL kernel (via RPC) |
| WSL CC session 信息 | WSL `data/server/sessions.json` | WSL kernel | WSL kernel, host kernel (via RPC) |
| Host-Host 对话消息 | host `data/conversations/` | host kernel | host kernel, host CCs |
| WSL-WSL 对话消息 | WSL `data/conversations/` | WSL kernel | WSL kernel, WSL CCs |
| **Host-WSL 跨机对话消息** | **host `data/conversations/`** | **host kernel（唯一写者）** | host kernel, host CCs, WSL CCs (via `/mnt/c/` 只读) |
| machine_info_log | 各自 `data/machine_info_log/` | 各自 kernel（注册时） | 各自 kernel |
| queue (RPC) | 各自 `data/queue/` | 本机 CCs + 远端 kernel（跨机 RPC） | 本机 kernel |

### 1.4 session_id 全局唯一性

CC session_id 是 UUID（如 `8ed4ef97-f04c-45dd-9742-d56af88ce551`）。UUID v4
碰撞概率为 ~$10^{-37}$，跨机碰撞可视为零。因此 session_id 可直接作为跨机
寻址的唯一令牌，无需额外命名空间。

---

## 2. Phase 1: in-WSL cc-communicate（独立可用）

### 2.1 目标

在 WSL2 中部署一套完整的 cc-communicate，使 WSL 内的 CC 之间能 p2p 通信。
此阶段**不涉及**跨 realm 通信。WSL cc-communicate 对内提供与 v0.1 主机版
完全等价的功能。

### 2.2 移植清单：可复用 vs 需修改

| 文件 | 复用/修改 | 说明 |
|---|---|---|
| `scripts/registrar.js` | ✅ 直接复用 | stdlib-only，无平台依赖 |
| `scripts/lib/paths.js` | ✅ 直接复用 | `__file__`-relative 路径解析跨平台 |
| `scripts/lib/proc.js` | ⚠️ 需验证 | psutil/`/proc` 在 Linux 上行为；`resolveClaude` 搜进程名含 `claude`（Linux 二进制名可能是 `claude` 或 `claude.exe`，需确认） |
| `hooks/hooks.json` | ✅ 直接复用 | `node` 命令在 WSL 中可用 |
| `.mcp.json` | ✅ 直接复用 | `python` 命令在 WSL 中可用（确保 WSL 的 python 有 deps） |
| `skills/cc-communicate/SKILL.md` | ✅ 复用（Phase 1） | Phase 2 需更新（新函数、跨机说明） |
| `server/paths.py` | ✅ 直接复用 | `__file__`-relative 跨平台 |
| `server/proc.py` | ⚠️ 需验证 | psutil 跨平台；`resolve_claude` 在 Linux 上搜 `claude` 进程名 |
| `server/kernel.py` | ✅ 直接复用 | 纯 Python，无平台依赖 |
| `server/kernel_api.py` | ✅ 直接复用（Phase 1） | Phase 2 需修改（is_local、machine 路由） |
| `server/check_core.py` | ⚠️ 需修改 | `_spawn_kernel()` 用了 Windows `creationflags`（见 2.4） |
| `server/rpc_client.py` | ✅ 直接复用 | 纯文件 I/O |
| `server/conversations.py` | ✅ 直接复用 | 纯路径操作 |
| `server/listen_poller.py` | ✅ 直接复用（Phase 1） | Phase 2 合并重构（见 §3.7） |
| `server/spawn.py` | ❌ 需实现 Linux 分支 | `spawn_cc_new` / `spawn_cc_resume` 的 Linux 实现（见 2.3） |
| `server/user_functions.py` | ✅ 直接复用（Phase 1） | Phase 2 需修改（跨机编排） |
| `server/mcp_server.py` | ✅ 直接复用（Phase 1） | Phase 2 需新增工具声明 |

### 2.3 spawn.py 移植：tmux detached session

**核心认知纠正**：交互式 CC 需要的是 **pty（伪终端）**，不是 GUI。WSL2 原生
支持 pty。tmux detached session 提供 pty，CC 可在其中跑交互模式（处理初始
prompt 后进入 REPL，stay alive）。此机器还装了 WSLg（`DISPLAY=:0`），但 pty
已足够，GUI 无关。

**不能用 `claude -p`**：`-p` 模式处理完 prompt 就退出，CC 无法 stay alive
听后续消息。evoke 需要进程持久（CC 常驻，靠 task-notification 驱动多轮）。

```python
# spawn.py Linux 分支
def spawn_cc_new(cwd: str, prompt: str):
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "/D", cwd, "claude", prompt])
    else:
        # tmux new-session -d: detached, 有 pty, CC 可跑交互模式
        # -s: session 名（用 cwd 的 basename 或随机，避免冲突）
        # -c: 设置工作目录（等价 Windows start /D）
        session_name = f"cc_{os.path.basename(cwd)}_{int(time.time())}"
        subprocess.Popen([
            "tmux", "new-session", "-d",
            "-s", session_name,
            "-c", cwd,
            "claude", prompt
        ])

def spawn_cc_resume(session_id: str, prompt: str):
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "claude", "--resume", session_id, prompt])
    else:
        session_name = f"cc_{session_id[:8]}"
        subprocess.Popen([
            "tmux", "new-session", "-d",
            "-s", session_name,
            "claude", "--resume", session_id, prompt
        ])
```

**与 Windows 版的对应关系**：

| 行为 | Windows (v0.1) | WSL2 (v2) |
|---|---|---|
| 打开新终端 | `cmd /c start` 新窗口 | `tmux new-session -d` detached session |
| 提供终端 | 窗口有 TTY | tmux 提供 pty |
| 设置 cwd | `start /D <cwd>` | `tmux -c <cwd>` |
| prompt 模式 | 位置参数（非 `-p`），处理后进 REPL | 同左 |
| 存活 | 新窗口独立运行 | detached session 独立运行 |
| 人工查看 | 窗口可见 | `tmux attach -t <session_name>` |

**已验证**（由 WSL2 CC 实测）：
- tmux detached session 存活、可投递命令（send-keys）、可回读（capture-pane）
- 第二 turn 状态保留
- CC 的 poller + task-notification 机制与终端类型无关（harness 管理 bg task）

### 2.4 check_core.py 移植：Linux 进程 spawn

`_spawn_kernel()` 当前用 Windows `creationflags = DETACHED_PROCESS |
CREATE_NEW_PROCESS_GROUP`。Linux 不需要 creationflags，用 `start_new_session`
实现 detached。

```python
def _spawn_kernel():
    kernel_py = os.path.join(os.path.dirname(__file__), "kernel.py")
    err_log = open(os.path.join(SERVER_DATA_DIR, "kernel.stderr.log"), "ab")
    kwargs = {
        "cwd": SERVER_DATA_DIR,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": err_log,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        # Linux: start_new_session=True 创建新进程组，等价 detached
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, kernel_py], **kwargs)
```

### 2.5 proc.py 移植：resolve_claude 在 Linux 上

`resolve_claude` 用 psutil 遍历进程树找 `claude` 祖先。psutil 跨平台，但需
确认 Linux 上 CC 进程名。

**已确认**：WSL2 CC 二进制名是 `claude`（虽然文件名叫 `claude.exe`，但它是
Linux ELF 二进制；psutil `parent.name()` 返回 `claude`）。当前 `resolve_claude`
匹配 `"claude" in name`，Linux 上同样命中。

**注意**：Linux 上 CC 可能通过 vscode-server 的集成终端启动，进程树是
`vscode-server -> shell -> claude`。`resolve_claude` 向上遍历 parents 找
`claude`，不受中间层影响。

### 2.6 hooks/MCP 配置

无需修改。`hooks.json` 用 `node`，`.mcp.json` 用 `python`，两者在 WSL 中
均在 PATH 中。确保 WSL 的 python 安装了 `psutil`、`filelock`、`mcp`。

### 2.7 Phase 1 验证标准

Phase 1 完成后，以下场景应通过（全部在 WSL 内，不涉及主机）：
1. WSL CC 安装插件 → SessionStart hook 触发 → `data/session_ctrl/` 有事件
2. `/mcp` 显示 cc-communicate server
3. `my_session_id()` 返回 UUID
4. 两个 WSL CC 之间 `connect` → `send_message` → `arm_poller` + `collect_messages` → `close_connection` 全流程
5. `evoke` 复活 dead WSL CC（tmux detached session）
6. `create_collaborator` 在 WSL 内 spawn 新 CC（tmux）

---

## 3. Phase 2: 跨 realm 通信

### 3.1 机器注册：machine_sign_up / machine_add

#### 3.1.1 机器身份

每台机器有双字段身份：

| 字段 | 含义 | 示例 | 用途 |
|---|---|---|---|
| `type` | 语义字段：说明机器类型 | `win-host`, `wsl-ubuntu`, `vbox-ubuntu` | 显示、路由判断、tmux session 命名 |
| `id` | 区别字段：UUID | `a1b2c3d4-...` | 全局唯一标识；`type` 相同时区分（如两个 WSL distro） |

`id` 在首次运行时生成（UUID4），存永久文件 `data/machine_identity.json`。
注册时双方交换 `{type, id, ...}`。`machine_info_log` 文件名用 `id`（保证
唯一），内容含 `type`（供语义判断）。

#### 3.1.2 握手协议（C:\ 根目录文件会合）

这是 **filesystem rendezvous at well-known location** 模式（见 #W9）。双方
通过 C:\ 根目录的临时文件互相发现，交换身份和数据库路径。C:\ 根目录已验证
可写（不需 admin）。

**路径视角原则**：每方在握手文件中写入**对方视角的** data 路径，对方直接
使用无需转换。
- WSL 写入 host 的路径为 WSL 视角：`/mnt/c/研究生/.../data`
- host 写入 WSL 的路径为 host 视角：`//wsl.localhost/Ubuntu/home/mocry/.../data`

**握手流程**（WSL 侧 `machine_sign_up`，host 侧 `machine_add`）：

```
步骤 1 [WSL]: 创建 C:\cc_signup_{id}.json
  内容: {type:"wsl-ubuntu", id:"<uuid>", system_info:{...},
         data_dir_for_host: "//wsl.localhost/Ubuntu/home/mocry/.../data",
         data_dir_self: "/home/mocry/.../data"}

步骤 2 [host]: machine_add 监听 C:\，发现 cc_signup_{id}.json
  -> 读取内容，获取 WSL 身份和 data_dir_for_host
  -> 创建 C:\cc_echo_{id}.json
     内容: {type:"win-host", id:"<host-uuid>", system_info:{...},
            data_dir_for_wsl: "/mnt/c/研究生/.../data",
            data_dir_self: "C:\\研究生\\...\\data"}
  -> 删除 cc_signup_{id}.json

步骤 3 [WSL]: 轮询 C:\，发现 cc_echo_{id}.json
  -> 读取内容，获取 host 身份和 data_dir_for_wsl
  -> 注册到 WSL data/machine_info_log/{host-id}.json
  -> 创建 C:\cc_success_{id}.json
  -> 删除 cc_echo_{id}.json

步骤 4 [host]: 发现 cc_success_{id}.json
  -> 注册到 host data/machine_info_log/{wsl-id}.json
  -> 删除 cc_success_{id}.json
  -> 注册完成
```

**超时与重试**：
- `machine_sign_up`（WSL 侧）：轮询 echo 文件 hold time = 1min。超时则清除
  echo（如有），返回注册失败。若发现自己的 signup 不见了且 echo 也没出现，
  重新创建 signup。
- `machine_add`（host 侧）：全局 timeout 5min（不重置）。每个具体监听操作
  timeout 1min。任何监听 timeout 则回到函数开头：清理 C:\ 中 success/signup/
  echo 文件，重新监听 signup。直到全局计时器到时。
- **防重复注册**：双方通信后发现对方 `id` 已在 `machine_info_log` 中，终止
  注册，返回 `already logged`。

**machine_info_log 条目结构**：
```json
{
  "type": "wsl-ubuntu",
  "id": "a1b2c3d4-...",
  "system_info": {"kernel": "...", "hostname": "..."},
  "data_dir": "/mnt/c/研究生/.../data",
  "registered_at": "2026-07-11T..."
}
```

#### 3.1.3 注册操作方式

提供两种方式（详见 §8 注册操作指南）：
1. **命令行**：用户先后启动 host 的 `machine_add()` 和 WSL 的
   `machine_sign_up()`
2. **借助 CC**：渐进式披露。用户说"cc-communicate 机器注册"，CC 查文档后执行

### 3.2 数据结构变更

#### 3.2.1 sessions.json session_inf 新增 `machine` 字段

```json
{
  "8ed4ef97-...": {
    "session_id": "8ed4ef97-...",
    "pid": 754,
    "cwd": "/home/mocry/projects/lora",
    "start_time": "2026-07-11T...",
    "start_time_epoch": 1783061940.16,
    "source": "...",
    "started_at": 1783061940000,
    "ended_at": null,
    "first_seen": 1783061940000,
    "machine": "wsl-ubuntu"
  }
}
```

`machine` 字段值 = 注册时的 `type` 字段（如 `wsl-ubuntu`、`win-host`）。
本机 session 的 `machine` = 本机 type。远端 session（经跨机查询得知）的
`machine` = 远端 type。

**向后兼容**：缺失 `machine` 字段的旧记录视为本机 session。

`alive_sessions` 内存结构同步新增 `machine` 字段。

#### 3.2.2 machine_info_log

新增 `data/machine_info_log/` 目录。每个注册的机器一个文件
`{id}.json`，内容见 §3.1.2。

### 3.3 V1 内核函数调整

> 以下函数调整后**仍保持内核态**，不披露给 CC，减轻上下文压力。

#### 3.3.1 process_session_ctrl_event()
**无变化。** 不同机器各司其职，各自管理自己机器中的 sessions。本机 hook 写
本机 `session_ctrl/`，本机 kernel replay。远端 session 不在本机 event log 中。

#### 3.3.2 withdraw(fromid, toid, init_connect)

**主机侧**：无变化。跨机通信不影响 conversation 内容在 host database 中的
位置和结构（依赖 session_id 唯一性假设 + 设计保证：withdraw 的时序一定在
sync_conversation 之后，主机 withdraw 沿用旧逻辑）。

**WSL 侧**：新增 conversation 判别逻辑。WSL kernel 收到的 withdraw 请求，
fromid 一定是 WSL CC id（WSL CC 发起的）。只判断 toid：
- `toid` 属于 WSL（`query_session(toid, is_local=1)` 有结果）→ 本地事宜，
  走原 withdraw 逻辑
- `toid` 是主机上的 id → WSL kernel 调用主机 kernel 的 withdraw 函数（via
  `/mnt/c/.../data/queue/`），传递相同参数

#### 3.3.3 query_session(session_id, is_local=0)

**新增隐参数 `is_local`**（对 CC 透明，CC 不传此参数，默认 0）。

- `is_local=1`：只查本机 `sessions.json`（同 v0.1 逻辑）
- `is_local=0`（默认）：先查本机；若无，遍历 `machine_info_log` 中注册的
  机器，调用其 `query_session(session_id, is_local=1)`（via 跨机 queue RPC）。
  任一机器返回有结果则返回；全部无则返回 null。

**防级联**：跨机查询时传 `is_local=1`，防止对方再向其他机器级联查询（见
#W10）。

#### 3.3.4 check_alive(session_id, is_local=0)

**新增隐参数 `is_local`**（对 CC 透明，默认 0）。

- `is_local=1`：只查本机 `alive_sessions`（同 v0.1 逻辑）
- `is_local=0`（默认）：先查本机。有且活→返回 1；有但没活→返回 0；本机无→
  遍历注册机器查 alive。任一机器返回 1→返回 1；全部 0 或无→返回 0。

跨机查询传 `is_local=1`（防级联）。

#### 3.3.5 evoke(session_id) — 从内核态移到用户态

**移到 user-space**（理由：evoke 需要遍历机器 + spawn 进程，是编排任务，适
合 user-space；且跨机时需要调用远端 kernel，user-space 编排更自然）。

操作逻辑：
1. `query_session(session_id, is_local=1)` → 查本机
2. 若本机有此 session → 参考 spawn.py 的方法，唤起一个带初始 prompt（告知被
   唤醒，要求装备技能并立刻开始听）、可持续交互的 CC
   - Windows: `cmd /c start claude --resume <sid> <prompt>`
   - WSL2: `tmux new-session -d -s cc_<sid> 'claude --resume <sid> <prompt>'`
3. 若本机无 → 遍历 `machine_info_log`，对每台机器 `query_session(id,
   is_local=1)`（via 跨机 RPC）
4. 若确定在某机器上 → 调用该机器的 evoke（via 跨机 queue RPC，该机器的
   user-space evoke 函数执行本地 spawn）
5. 全部查不到 → 返回 `session not exists`

### 3.4 V1 用户函数调整

#### 3.4.1 query_conversations(session_id, is_local=0)

**输出格式变更**：list → dict。键为 partner session_id 字符串，值为 info。
（原 v0.1 返回 `[{partner: sid}, ...]`，不够简洁。）

```python
# v0.1: [{"partner": "abc..."}, {"partner": "def..."}]
# v2:   {"abc...": {...info}, "def...": {...info}}
```
需修改所有调用此函数的地方的处理逻辑。

**新增隐参数 `is_local`**（对 CC 透明，默认 0）：
- `is_local=1`：保持 v0.1 逻辑，只查本机 conversations 文件夹
- `is_local=0`：先本机查询，再遍历注册机器调用 `query_conversation(id,
  is_local=1)`，按 session_id 唯一性合并结果（重合的 sid 丢弃后者）

#### 3.4.2 connect(caller_sid, target_sid, hold_time=60)

**核心原则（v0.1 模式）**：connect 保持 user-space，由调用方的 MCP server
编排。kernel 只提供单步非阻塞 RPC。**connect 不是 kernel 函数，不会阻塞
kernel。**

**WSL 侧**（WSL CC 连接 target）：
1. `query_session(target_sid, is_local=0)` 确认 target 存在
2. 若 target 在 WSL（本机）→ 走 v0.1 原逻辑（本地 connect）
3. 若 target 在 host → 跨机 connect（见下方跨机流程）
4. 若 target 不存在 → 返回 `not exist`

**Host 侧**（Host CC 连接 target）：
1. `query_session(target_sid, is_local=0)` 确认 target 存在
2. 若 target 在 host（本机）→ 走 v0.1 原逻辑
3. 若 target 在 WSL → 跨机 connect（见下方跨机流程）
4. 若 target 不存在 → 返回 `not exist`

**跨机 connect 流程**（以 WSL→host 为例；host→WSL 对称）：

```
WSL MCP server 的 connect(wsl_sid, host_sid):
  1. query_session(host_sid, is_local=0)
     -> WSL kernel 查本地无 -> 转发 host queue -> host kernel 查 -> 返回
  2. check_alive(host_sid) via host kernel queue RPC
  3. if dead: evoke(host_sid)
     -> user-space evoke 遍历机器 -> 找到在 host -> 调 host kernel
     -> host kernel spawn 主机 CC (cmd /c start)
     -> poll check_alive until alive
  4. register_conversation(wsl_sid, host_sid)
     -> 提交到 host kernel (via /mnt/c/ queue)  [host kernel 注册]
     -> 同时在 WSL kernel 本地注册             [WSL kernel 状态追踪]
  5. send_message(wsl_sid, host_sid, hello) via host kernel queue RPC
     -> host kernel 写 host conversations/pipe
  6. arm + run listen_poller (本地 WSL)
     -> 扫描 /mnt/c/.../conversations/ 找 host_sid 的回复
     ★ 阻塞，但在 WSL MCP server 进程，不阻塞任何 kernel
  7. collect_messages(wsl_sid) via host kernel queue RPC
     -> host kernel 读 host conversations/pipe + 归档(pipe->log) + 返回消息
  8. 返回 connect succeed
```

**Host→WSL 的额外步骤**（host 独立完成全部 connect 流程后通知 WSL）：

```
Host MCP server 的 connect(host_sid, wsl_sid):
  步骤 1-7 同上（对称，host kernel 本地 + WSL kernel via //wsl.localhost/ queue）
  其中步骤 4 只在 host kernel 注册（WSL kernel 尚不知情）
  步骤 6 listen_poller 扫描 host 本地 conversations/（跨机消息在 host）

  步骤 7 成功后:
  8. inform_connect(host_sid, wsl_sid) via //wsl.localhost/ queue
     -> 告知 WSL kernel 此 connect 已成功建立
     -> WSL kernel 在本地 alive_conversations 注册这对对话
     -> WSL kernel 返回已同步
  9. 等 WSL kernel 返回已同步后，host MCP server 才返回 success
```

**为什么 host→WSL 需要 inform_connect 而 WSL→host 不需要？**
- WSL→host：WSL 是发起方，WSL MCP server 在步骤 4 就本地注册了
- host→WSL：host 是发起方，host MCP server 在步骤 4 只在 host kernel 注册。
  WSL kernel 不知情。步骤 8 的 inform_connect 让 WSL kernel 同步状态。

**inform_connect / inform_unconnect 通过 UNC 直接文件 I/O**（见 #W8）：
host kernel 写 `//wsl.localhost/Ubuntu/.../data/queue/`，WSL kernel 本地
轮询处理。

#### 3.4.3 send_message(fromid, toid, message)

**WSL 侧**：
- 本地内部 send（toid 在 WSL）→ 本地处理
- 跨机器 send（toid 在 host）→ 转交 host kernel 处理（via `/mnt/c/` queue）

**Host 侧**：处理逻辑不变（跨机消息本来就存 host database，host kernel 写
host conversations/pipe）。

#### 3.4.4 keep_listen — 合并重构

**合并 arm_poller + listen_poller + collect_messages 为单脚本**（见 #W5、#W6
的详细分析）。

**合并后的脚本逻辑**（WSL CC 侧）：

```
脚本启动(session_id, timeout):
  deadline = now + timeout
  loop:
    # 1. 扫描所有 conversation 文件夹中发给 session_id 的未投递消息
    candidates = []
    for conv in query_active_conversations(session_id):
      if conv.partner 在 WSL (本地):
        scan 本地 conversations/<conv>/pipe/ for toid==session_id
      elif conv.partner 在 host:
        scan /mnt/c/.../conversations/<conv>/pipe/ for toid==session_id  (只读)
    candidates += found files

    # 2. 检测到消息
    if candidates 非空:
      # 2a. settle: 等待 3s 防止半写
      sleep(3)
      # 2b. 读取候选文件内容
      messages = [read(f) for f in candidates]
      # 2c. 归档
      for f in candidates:
        if conv 本地:
          move f: pipe -> log  (本地文件 I/O)
        else:  # 跨机, 在 host conversations
          # WSL 对 host conversations 只读，归档委托 host kernel
          host_kernel_rpc("collect_messages", {session_id: session_id})
          # 或更精确: host_kernel_rpc("archive_files", {files: candidates})
      # 2d. 打印消息到 stdout, exit 0
      print(json.dumps(messages))
      exit(0)

    # 3. 无消息
    if now > deadline: exit(2)
    sleep(backoff)  # 5s -> 10s -> ... -> 5min
```

**关键设计点**：

1. **any-undelivered 语义**（非 baseline）：只要有发给 session_id 的未投递消
   息就触发，不管是不是新出现的。合并后不需要 baseline——归档把 pipe→log，
   下次不会重复（见 #W6）。

2. **方向特定**：只收集 `toid == session_id` 的消息（b2a if A listens）。
   `fromid == session_id` 的消息是发给对方的，由对方收集。

3. **settle 3s**：发现候选文件后等 3s 再读，防止写进程还在写到一半。只归档
   初始检测到的候选文件（settle 期间新到的不归档，下轮处理）。

4. **双路径路由**（WSL 特有）：
   - 本地 conversation → 扫描 WSL `conversations/`（读写）
   - 跨机 conversation → 扫描 `/mnt/c/.../conversations/`（只读检测），
     归档委托 host kernel（WSL 对 host conversations 只读）

5. **Host CC 侧无双路径**：所有 conversation（本地+跨机）都在 host
   `conversations/`，host 脚本只扫本地。

**CC 调用方式**（合并后）：
```bash
# 一行 Bash，后台运行
Bash("python <plugin_root>/server/listen.py <session_id> <timeout>", run_in_background=true)
# task-notification 带回 exit code (0=有消息, 2=超时) + stdout 里的消息 JSON
```

#### 3.4.5 close_connection(session_id, toid)

**WSL 侧**：
- 两个本地 CC 之间的 close → 本地处理
- 跨机关闭 → 转交 host kernel `close_connection`，得到返回后本地同步 WSL
  kernel 内存中维护的信息

**Host 侧**：
- 和原编排逻辑相似
- 若是 host 独立完成的跨机关闭（即不是收到来自 WSL 的请求），则需调用 WSL
  侧的 `inform_unconnect(fromid, toid)` 通知 WSL kernel 同步

#### 3.4.6 create_collaborator(caller_sid, cwd, hold_time=60)

设计理念同 v0.1。注意在不同系统上 create 的方式不同：
- Windows: `start /D <cwd> claude <prompt>`
- WSL2: `tmux new-session -d -c <cwd> 'claude <prompt>'`

### 3.5 新增函数

#### 3.5.1 machine_sign_up()
WSL 侧一次性不对称函数。生效条件：host kernel 在线并运行 `machine_add()`。
详见 §3.1.2。

#### 3.5.2 machine_add()
Host 侧一次性不对称函数。详见 §3.1.2。

#### 3.5.3 kernel_terminate()
提供 kernel 主动终结机制。可被外部调用（注册完成后主动释放进程负载，而非
等 idle timeout 自杀）。

#### 3.5.4 query_machines()
返回当前注册的机器字典。键为机器 `id`，值为该机器的注册信息
（`{type, data_dir, ...}`）。

#### 3.5.5 create_conversation_folder(id1, id2)
从 connect 中外接出来的函数。
- **Host 侧**：任何情况下直接在 host `data/conversations/` 中创建对话文件
  夹结构（跨机消息存 host）。
- **WSL 侧**：检查 id1, id2 是否都在 WSL 本地（`query_session(is_local=1)`）。
  若都在本地 → 在本地 `conversations/` 创建。否则（至少一方在 host）→ 调用
  host kernel 的 `create_conversation_folder`（via `/mnt/c/` queue）。

#### 3.5.6 inform_connect(fromid, toid)
WSL kernel 一侧独有的函数，host 可调用（via `//wsl.localhost/` queue）。
向 WSL 同步一起完全由 host 完成的、成功的 connect（即 host CC connect WSL CC
的 case）。WSL kernel 收到后在本地 `alive_conversations` 注册这对对话，返回
已同步。

#### 3.5.7 inform_unconnect(fromid, toid)
同 inform_connect，只有 host 独立完成全部 close 操作时，在最后告诉 WSL。

---

## 4. 技术难点与解决方案（全部已验证）

> 以下编号 #W1-#W11 对应 WSL2 移植的技术挑战。与 `core_plan.md` 的 #0-#11
> （v0.1 基础设计挑战）互补。所有结论均实测验证。

### #W1 跨 realm 文件访问

**问题**：WSL2 CC 和 host CC 在不同文件系统（ext4 vs NTFS），如何互相访问
对方的 data 目录（queue、conversations）？

**验证过程**：
- WSL → host：`/mnt/c/...` — 9p 文件系统，读写均 OK ✅
- host → WSL：`\\wsl.localhost\Ubuntu\...`（backslash UNC）— Python
  `os.listdir` / `os.path.exists` / `open()` 全部失败 ❌
- host → WSL：`//wsl.localhost/Ubuntu/...`（**forward-slash** UNC）—
  `open()` 读写、`os.listdir`、`os.path.exists`、`os.remove` 全部 OK ✅

**根因**：backslash UNC 在 bash shell 中被转义层吃掉反斜杠，实际路径变成
单反斜杠（`\wsl.localhost` 而非 `\\wsl.localhost`）。forward-slash 不受
shell 转义影响。

**解决方案**：
- WSL → host：`/mnt/c/研究生/实习/learn AI/projects/hello cc/.../data`
- host → WSL：`//wsl.localhost/Ubuntu/home/mocry/.../data`
- **代码中永远用 forward-slash UNC**，不用 backslash

**代码片段**：
```python
# host kernel 访问 WSL data 目录
WSL_DATA_DIR = "//wsl.localhost/Ubuntu/home/mocry/.claude/plugins/.../data"
# 不要写 r"\\wsl.localhost\Ubuntu\..." — shell 会吃反斜杠
```

### #W2 跨 realm 进程执行

**问题**：某些场景需要在一侧执行另一侧的 Python 脚本（如 host 需要在 WSL
context 里运行代码，或反向）。

**验证**：
- WSL → host：`python.exe <script>` — Windows Python 从 WSL 调用 ✅
- host → WSL：`wsl.exe -d Ubuntu -- python3 <script>` — WSL Python 从 host
  调用 ✅

**三个坑**（host → WSL 方向）：
1. **MSYS 路径篡改**：`/path/to/script.py` 作为独立参数会被 MSYS 改成
   `C:/Users/.../path/...`。解决：用 **subprocess list 形式**（不用
   `bash -c` 字符串），或 `MSYS_NO_PATHCONV=1`。
2. **默认 cwd 是 `/mnt/c/...`**（Windows 当前目录的 WSL 映射）。解决：脚本
   用绝对路径，或 `bash -c 'cd /wsl/path && ...'`。
3. **stderr UTF-16 代理警告**：`wsl.exe` stderr 有一条 UTF-16LE 乱码警告。
   在中文 Windows（GBK 控制台）直接 print 会 UnicodeEncodeError。解决：
   `errors="replace"` 或 `2>/dev/null`。

**解决方案**（host → WSL，subprocess list 形式）：
```python
r = subprocess.run(
    ["wsl.exe", "-d", "Ubuntu", "--", "python3", "/path/to/script.py", "arg1"],
    capture_output=True, text=True, errors="replace"
)
```

**关键结论**：queue RPC **不需要** subprocess——双向文件 I/O 已可用（#W1）。
`wsl.exe` 只在 spawn 交互式 CC（evoke/create_collaborator）时才需要。

### #W3 WSL2 spawn 交互式 CC：pty ≠ GUI

**问题**：v0.1 的 `spawn.py` 用 `cmd /c start` 开新窗口。WSL2 默认"无 GUI"，
如何 spawn 一个需要 TTY 的交互式 CC？

**根因分析**：交互式 terminal 程序需要的是 **pty（伪终端）**，不是 GUI。
"WSL2 无 GUI"是错误前提——WSL2 原生支持 pty。此机器还装了 WSLg
（`DISPLAY=:0`），但 pty 已足够。

**三种方案验证**：

| 方案 | 命令 | 进程持久 | TTY | 验证 |
|---|---|---|---|---|
| A: `claude -p --resume` | `claude -p --resume <sid> "<prompt>"` | ❌ 处理完退出 | 不需要 | ✅ 可跑 |
| B: tmux + send-keys | `tmux new-session -d 'claude --resume <sid>'` | ✅ 常驻 | pty | ✅ pty 机制验证 |
| C: `claude --background` | `claude --background --resume <sid> "<prompt>"` | ✅ 常驻 | 不需要 | ✅ 可跑，多轮追加待验证 |

**方案选择**：**B（tmux）**。理由：
- evoke 需要进程持久（CC stay alive 听消息），方案 A `-p` 模式会退出 ❌
- tmux detached session 提供 pty，CC 可跑交互模式 ✅
- 是 Windows `cmd /c start claude --resume` 的 WSL2 等价物 ✅
- 方案 C 的"往已 idle 的 bg session 追加 prompt"未验证，留作未来优化

**已验证**（WSL2 CC 实测）：
- tmux detached session 存活、可投递命令（send-keys）、可回读（capture-pane）
- 第二 turn 状态保留
- CC 的 poller + task-notification 机制与终端类型无关

**实现**：见 §2.3 代码片段。

### #W4 connect 不能是 kernel 函数（阻塞问题）

**问题**：原始设计说"wsl connect 请求调用主机 connect，由主机 kernel 处理剩
余逻辑"。但 connect 有 60s 阻塞（listen_poller），放入 kernel 会阻塞单线程
循环。

**根因分析**：
- v0.1 中 connect 是 **user-space** 函数（`user_functions.py`），运行在 MCP
  server 进程
- connect 内部 `subprocess.run(listen_poller, timeout=hold_time)` 阻塞 60s，
  但阻塞在 **MCP server 进程**，不在 kernel
- kernel 是**单线程轮询循环**（`core_plan #9`），所有 kernel 函数串行执行
- 如果 connect 变成 queue RPC 提交给 kernel，kernel 会被阻塞 60s，期间无法
  处理任何其他请求

**解决方案**：connect **保持 user-space**（V1 模式）。WSL MCP server 的
connect() 自己编排，调用 host kernel 的**单个 kernel 函数**（via queue RPC，
每步非阻塞 ~100ms），listen_poller 在 WSL MCP server 进程本地跑（阻塞在此
进程，不阻塞 kernel）。两个 kernel 都不阻塞。

**"由主机 kernel 处理剩余逻辑"的正确含义**：host kernel 提供 individual
kernel 函数（query_session、check_alive、send_message 等）供 WSL 的 connect()
调用——和 v0.1 的 connect() 调用本地 kernel 函数完全一样，只是跨机而已。
**不是**让 host kernel 运行整个 connect()。

### #W5 keep_listen 合并：arm/collect 是纯文件 I/O

**问题**：v0.1 的 keep_listen 拆成 3 步（arm_poller MCP + bash poller +
collect_messages MCP）。能否合并为单脚本？

**根因分析**：
- `arm_poller`（`kernel_api.py:183`）：算 baseline + 写 config + 返回 command。
  **不访问任何 kernel 内存状态**（sessions、alive_sessions、alive_conversations
  一个没碰）。纯文件 I/O。
- `collect_messages`（`kernel_api.py:203`）：扫 conversations 文件夹 + 读 pipe
  + 移到 log。**不访问任何 kernel 内存状态**。纯文件 I/O。
- 两者被归在 kernel_api.py 是 v0.1 的设计归类问题，它们本可以是 user-space。

**poller 能调 kernel 吗？** 技术上能（queue RPC 是文件 I/O，任何进程可用），
但不应该：
1. arm/collect 不需要 kernel 状态，走 kernel RPC 是多此一举
2. kernel 可能没在跑（idle 自杀），poller 调 RPC 会触发 ensure_core 拉起
   kernel 只为做文件 I/O，然后 kernel 又准备自杀——无谓开销

**解决方案**：合并为单脚本，直接做文件 I/O。唯一例外：跨机归档需委托 host
kernel（WSL 对 host conversations 只读，见 #W7）。

### #W6 listen 语义：any-undelivered + direction-specific + settle

**问题**：v0.1 用 baseline 语义（只有 arm 之后的新消息才触发）。合并后用
什么语义？

**v0.1 baseline 语义的问题**：arm 时 pipe 已有 3 条旧消息 → baseline=3 →
poller 等第 4 条才退出 → 3 条旧消息被"埋"住。

**合并后的 any-undelivered 语义**：
- `count_undelivered(sid) > 0` → 立即触发
- 不需要 baseline——归档把 pipe→log，下次不会重复
- 更简单、更鲁棒，不丢消息

**direction-specific**：只收集 `toid == session_id` 的消息。pipe 中可能同时
有 a2b 和 b2a 文件；A 发起的 listen 只收集 b2a（发给 A 的）。

**settle 3s**：发现候选文件后等 3s 再读，防止写进程还在写到一半。只归档初始
检测到的候选文件（settle 期间新到的不归档，下轮处理）。

### #W7 WSL 对 host conversations 只读

**问题**：跨机消息存 host database。WSL CC 的 poller 需要读 host conversations
检测回复。但归档（pipe→log）是写操作。WSL 能写 host conversations 吗？

**设计决策**：**WSL 对 host conversations 只读。** host kernel 是唯一写者，
避免竞争。

**技术事实**：WSL 经 `/mnt/c/` **可以**写 host 文件（实测 `echo > /mnt/c/...`
成功）。"只读"是设计选择，不是技术限制。选择只读是为了保持 host kernel
作为单一写者的一致性。

**影响**：合并脚本对跨机消息：
- 检测（扫 pipe）：WSL 本地读 `/mnt/c/.../conversations/`（只读 OK）
- 归档（pipe→log）：委托 host kernel（via `/mnt/c/` queue RPC）

### #W8 反向 RPC：host → WSL via UNC

**问题**：inform_connect / inform_unconnect 需要 host kernel 调用 WSL kernel
的函数。host 如何写 WSL 的 queue？

**验证**：`//wsl.localhost/Ubuntu/...`（forward-slash UNC）从 host Python 可
读写（#W1）。host kernel 直接写 `//wsl.localhost/Ubuntu/.../data/queue/`，
WSL kernel 本地轮询 `data/queue/` 处理，写 `data/queue/responses/`，host
kernel 轮询 `//wsl.localhost/Ubuntu/.../data/queue/responses/` 读取。

**对称性**：两个方向的 queue RPC 都用直接文件 I/O，不需要 subprocess：
- WSL → host：WSL 写 `/mnt/c/.../data/queue/`
- host → WSL：host 写 `//wsl.localhost/Ubuntu/.../data/queue/`

### #W9 机器注册：filesystem rendezvous

**问题**：两个陌生的程序（WSL kernel 和 host kernel）在同一台电脑上如何
找到对方？

**行业模式**：

| 模式 | 机制 | 典型例子 |
|---|---|---|
| 预配置地址 | A 的配置写明 B 在哪 | DB 连接串、config.yml |
| 约定位置 | 双方约定固定路径/port | `/var/run/docker.sock`、HTTP 80 |
| 文件系统会合 | 一方在已知位置留文件（含自己地址），另一方读 | `.pid` 文件、lock file |
| 服务注册中心 | 中央 registry | etcd/Consul/systemd |
| 零配置网络 | 局域网广播"我在这里" | mDNS/Bonjour |

**本设计选择**：约定位置（C:\ 根目录）+ 文件系统会合。C:\ 根目录已验证可写
（不需 admin）。握手文件交换双方身份和数据库路径。这是 legitimate industry
pattern（Docker `.pid` 文件、X11 `/tmp/.X11-unix/X0` 同思路）。

**trade-off**：
- 零配置（用户体验好）↔ 协议复杂（4-way 握手 + 清理 + 超时）
- cc-communicate 机器注册是**一次性操作**，一次性握手换零配置体验是合理的

**bootstrap 会合点**：C:\ 根目录是唯一需要预先约定的位置。握手后所有通信走
交换来的路径（`/mnt/c/` 和 `//wsl.localhost/`）。不需要额外配置文件。

### #W10 is_local 隐参数防级联

**问题**：`query_session(is_local=0)` 会遍历注册机器查询。如果对方也用
`is_local=0`，会无限级联（A 查 B，B 查 A，A 查 B...）。

**解决方案**：跨机查询时传 `is_local=1`。即：
- CC 调用 `query_session(sid)` → kernel 收到 `is_local=0`（默认）
- kernel 查本地无 → 遍历注册机器，调用对方的 `query_session(sid, is_local=1)`
- 对方收到 `is_local=1` → 只查本地，不再级联

**对 CC 透明**：CC 永远不传 `is_local`（MCP tool 定义不暴露此参数），默认 0
（全局查询）。CC 不感知跨机细节。

### #W11 路径视角：每方写对方视角的路径

**问题**：握手时交换数据库路径。WSL 的 data 路径是 `/home/mocry/...`，但
host 需要用 `//wsl.localhost/Ubuntu/home/mocry/...` 访问。谁做转换？

**解决方案**：每方在握手文件中写**对方视角的** data 路径，对方直接使用无需
转换。
- WSL 写入：`data_dir_for_host: "//wsl.localhost/Ubuntu/home/mocry/.../data"`
- host 写入：`data_dir_for_wsl: "/mnt/c/研究生/.../data"`

**转换规则**（写入方自行构造）：
- WSL 路径 → host 视角：`/home/...` → `//wsl.localhost/Ubuntu/home/...`
  （prepend `//wsl.localhost/Ubuntu`，去掉开头的 `/`）
- host 路径 → WSL 视角：`C:\研究生\...` → `/mnt/c/研究生/...`
  （`C:\` → `/mnt/c/`，`\` → `/`）

**同时写自己的视角路径**（`data_dir_self`）作为参考，但对方使用
`data_dir_for_peer`。

---

## 5. Caution 清单（实现时必须注意的坑）

| # | 坑 | 后果 | 解决 |
|---|---|---|---|
| C1 | backslash UNC `\\wsl.localhost\...` | shell 吃反斜杠，路径变成单 `\`，file not found | **永远用 forward-slash** `//wsl.localhost/...` |
| C2 | `wsl.exe` 用 `bash -c` 字符串传参 | MSYS 路径篡改，`/tmp/x.py` → `C:/.../tmp/x.py` | 用 **subprocess list 形式** `["wsl.exe","--","python3","/path"]` |
| C3 | `wsl.exe` 默认 cwd 是 `/mnt/c/...` | 脚本用相对路径踩坑 | 脚本用绝对路径或显式 `cd` |
| C4 | `wsl.exe` stderr UTF-16 乱码 | 中文 Windows print 时 UnicodeEncodeError | `errors="replace"` 或 `2>/dev/null` |
| C5 | WSL 写 host conversations | 破坏"host kernel 唯一写者"一致性 | 跨机归档委托 host kernel RPC，WSL 只读 `/mnt/c/.../conversations/` |
| C6 | connect 作为 kernel queue RPC | 阻塞 kernel 单线程循环 60s | connect 保持 user-space，kernel 只做单步 RPC |
| C7 | 用 `claude -p` 做 evoke | CC 处理完 prompt 退出，无法 stay alive 听消息 | 用 tmux detached session（pty，交互模式） |
| C8 | `is_local=0` 跨机查询级联 | 无限循环 A→B→A→B | 跨机查询传 `is_local=1` |
| C9 | 路径视角混淆 | host 用了 WSL 视角路径（或反过来），file not found | 握手文件写对方视角路径，存在 `data_dir_for_peer` 字段 |
| C10 | 旧 sessions.json 缺 `machine` 字段 | 跨机路由判断失败 | 缺失视为本机 session（向后兼容） |
| C11 | tmux session 名冲突 | 多次 evoke 同一 sid 导致 tmux session 名碰撞 | session 名含 sid 前 8 位 + 时间戳，或先 kill 旧 session |

---

## 6. 已验证可行性清单

| 验证项 | 方法 | 结果 | 日期 |
|---|---|---|---|
| WSL2 CC 是原生 Linux | `readlink /proc/<pid>/exe` | Linux ELF，`/home/mocry/` 下 | 2026-07-11 |
| C:\ 根目录写入 | Windows Python `open("C:\\\\test","w")` | OK，不需 admin | 2026-07-11 |
| WSL → host 文件读写 | `echo > /mnt/c/Users/Mocry/test.txt` | OK | 2026-07-11 |
| host → WSL 文件读写（forward-slash UNC） | Python `open("//wsl.localhost/Ubuntu/tmp/test","w")` | OK（读写/listdir/exists/remove 全通过） | 2026-07-11 |
| host → WSL 文件读写（backslash UNC） | Python `os.path.exists(r"\\\\wsl.localhost\\...")` | ❌ 失败（shell 转义） | 2026-07-11 |
| WSL → host 进程执行 | WSL Python `subprocess.run(["python.exe","-c","print(42)"])` | OK，stdout=`'42\n'` | 2026-07-11 |
| host → WSL 进程执行 | host Python `subprocess.run(["wsl.exe","-d","Ubuntu","--","python3","-c","..."])` | OK，stdout 透传 | 2026-07-11 |
| tmux detached session 存活 | `tmux new-session -d` + send-keys + capture-pane | 存活、可投递、可回读、第二 turn 保留 | 2026-07-11 |
| claude headless 运行 | `timeout 90 claude -p "Reply PONG"` | 输出 PONG，exit 0 | 2026-07-11 |
| `claude agents --json` | 原生命令 | 结构化 JSON，列出运行中 CC 实例 | 2026-07-11 |
| WSL2 有 tmux/screen/wt.exe | `which tmux / screen / wt.exe` | 全部可用 | 2026-07-11 |
| Python 版本一致 | host `python --version` = WSL `python3 --version` | host 3.14.5, WSL 3.14.4 | 2026-07-11 |

---

## 7. Build 逐步清单

### Phase 1: in-WSL cc-communicate（独立可用）

| 步骤 | 任务 | 验证标准 |
|---|---|---|
| 1.1 | 在 WSL 中部署插件副本（独立目录，非 /mnt/c/） | 文件结构完整 |
| 1.2 | WSL python 安装 deps（psutil, filelock, mcp） | `pip install -r requirements.txt` 成功 |
| 1.3 | 实现 `spawn.py` Linux 分支（tmux） | `tmux new-session -d` 能起 CC |
| 1.4 | 修改 `check_core.py` `_spawn_kernel()` Linux 分支 | kernel 能 lazy-start，写 core_status.json |
| 1.5 | 验证 `proc.py` `resolve_claude` 在 Linux 上 | `my_session_id()` 返回 UUID |
| 1.6 | 验证 hooks 触发（SessionStart/End） | `data/session_ctrl/` 有事件文件 |
| 1.7 | 验证 MCP server 启动 | `/mcp` 显示 cc-communicate |
| 1.8 | **Phase 1 端到端测试**：两个 WSL CC p2p | connect→send→listen→collect→close 全流程 |
| 1.9 | 验证 `evoke`（tmux detached CC） | 复活 dead CC，能收消息 |
| 1.10 | 验证 `create_collaborator`（tmux spawn） | 新 CC 注册 + connect 成功 |

### Phase 2: 跨 realm 通信

| 步骤 | 任务 | 验证标准 |
|---|---|---|
| 2.1 | 实现 `machine_sign_up`（WSL 侧）/ `machine_add`（host 侧） | C:\ 根目录握手成功，双方 machine_info_log 有记录 |
| 2.2 | sessions.json + alive_sessions 新增 `machine` 字段 | 旧记录向后兼容 |
| 2.3 | 实现 `is_local` 参数（query_session, check_alive, query_conversations） | 本地查询不受影响；跨机查询返回正确结果 |
| 2.4 | 实现跨机 queue RPC（paths.py 新增 host/wsl data dir 解析） | WSL 能写 host queue；host 能写 WSL queue |
| 2.5 | 调整 `withdraw`（WSL 侧路由）、`send_message`（WSL 侧路由） | 本地/跨机消息正确投递 |
| 2.6 | 合并 keep_listen 为单脚本（listen.py） | CC 一行 Bash 完成听消息 |
| 2.7 | 实现 `inform_connect` / `inform_unconnect`（via UNC） | host→WSL 反向 RPC 通 |
| 2.8 | 调整 `connect`（跨机编排，user-space） | WSL CC ↔ host CC connect 成功 |
| 2.9 | 调整 `close_connection`（跨机关闭 + inform_unconnect） | 跨机关闭后双方状态同步 |
| 2.10 | 实现 `create_conversation_folder` / `kernel_terminate` / `query_machines` | 各函数功能正常 |
| 2.11 | 更新 `SKILL.md`（新函数、跨机说明、keep_listen 合并后的用法） | CC 能按文档操作 |
| 2.12 | **Phase 2 端到端测试**：WSL CC ↔ host CC p2p | 跨机 connect→send→listen→collect→close 全流程 |

---

## 8. 注册操作指南

### 方式 1：命令行

```bash
# 1. host 侧：启动 machine_add（预期输出 "activated, listening..."）
python .../server/machine_add.py

# 2. WSL 侧：启动 machine_sign_up（预期输出 "signing up..." -> "shaking hand..." -> "success!"）
python3 .../server/machine_sign_up.py

# 3. 注册完成后，可选主动释放 kernel（节省进程负载）
python .../server/kernel_terminate.py   # host 侧
python3 .../server/kernel_terminate.py  # WSL 侧
```

### 方式 2：借助 CC（渐进式披露）

不显式向 CC 披露注册功能。用户说"查询 cc-communicate 文档，找到机器注册部
分，完成机器注册"时，CC 查文档执行：

- **WSL 文档**告诉 CC：直接调用 `machine_sign_up`
- **host 文档**告诉 CC：先澄清（目前只支持 WSL 注册到主机），用户确认后启动
  `machine_add`

两边 CC 监测函数返回，向用户给第一手反馈。

---

*Last updated: 2026-07-11. Based on core_plan.md v0.1 + WSL2 移植讨论。所有技术结论实测验证。*
