# cc-communicate WSL2 移植 - 核心设计 (v2.1)

> **文档定位**：本文件是 cc-communicate 从 Windows-only (v0.1) 扩展到支持
> WSL2 CC 跨 realm 通信的设计文档。`core_plan.md` 是 v0.1 基础设计，本文件
> 是其 v2 扩展。阅读顺序：`core_plan.md` → 本文件。
>
> **验证状态**：本文件中所有技术结论均已实测验证（见 §技术难点）。builder
> 无需重复验证，可直接进入实现。
>
> **目标**：完成后，WSL2 中的 CC 能与主机 CC 通过 cc-communicate 框架实现
> 跨 realm 的互相感知与无缝交流。
>
> **v2.1 变更**（2026-07-11 设计审查后）：跨机 fan-out 全部上移到 user-space，
> kernel 取消 is_local 保持纯本地操作，删除 inform_connect/inform_unconnect，
> 新增死锁规避、poller 退避、交叉验证项。

---

## 0. 先决架构决策

| 决策 | 结论 | 依据 |
|---|---|---|
| WSL2 CC 类型 | 原生 Linux 进程（非 Windows CC via interop） | `readlink /proc/<pid>/exe` 确认 Linux ELF，路径在 `/home/mocry/` 下 |
| 插件部署方式 | WSL 内独立部署（option B），非共享 `/mnt/c/` | 跨文件系统 I/O 有延迟；kernel 高频轮询需本地 ext4 |
| 阶段划分 | Phase 1: 独立 in-WSL cc-communicate；Phase 2: 跨 realm 通信 | 降低风险，Phase 1 可独立验证 |
| 跨 realm 通信模型 | 双 kernel 各管各机，**kernel 永远只做本地操作**；跨机 fan-out 在 **user-space（MCP server）** | 避免死锁（#W12）；保持 kernel v0.1 单线程语义 |
| 跨机消息存储 | 跨机消息存 host database；WSL 对 host conversations 只读 | host kernel 作为唯一写者，避免竞争；WSL 经 `/mnt/c/` 只读访问 |
| session_id | 全局唯一身份令牌（UUID，碰撞概率可忽略） | CC session_id 是 UUID，跨机碰撞概率为零 |
| connect 归属 | **保持 user-space**（V1 模式），kernel 只做单步非阻塞 RPC；跨机编排在 MCP server | 死锁规避（#W12）；与 v0.1 设计一致 |
| spawn 方式 (WSL2) | tmux detached session（pty，非 GUI） | WSL2 原生支持 pty；`-p` 模式 CC 会退出，不能用于 evoke（见 #W3） |
| 机器注册 | C:\ 根目录文件握手（filesystem rendezvous） | 零配置；legitimate industry pattern（见 #W9） |
| 机器身份 | 双字段：`type`（语义）+ `id`（UUID，区别） | type 用于路由/显示；UUID 保证唯一 |
| keep_listen | 合并 arm+poller+collect 为单脚本；any-undelivered 语义；settle 3s | arm/collect 是纯文件 I/O，不需要 kernel 状态（见 #W5） |
| 跨 realm 文件 I/O | WSL→host: `/mnt/c/`；host→WSL: `//wsl.localhost/Ubuntu/`（forward-slash） | 双向实测通过（见 #W1） |
| 跨 realm RPC | queue 文件双向直接 I/O，不需要 subprocess | `/mnt/c/` 和 `//wsl.localhost/` 均可读写（见 #W8） |
| **kernel 纯本地** | **kernel 不感知 is_local，永远只做本地操作；跨机 fan-out 在 user-space** | 死锁规避（#W12）；简化设计（无 inform_connect/inform_unconnect）（审查后确认） |

---

## 1. 架构总览

### 1.1 双 kernel 架构（v2.1 — kernel 纯本地）

```
    ┌─────────────────── HOST (Windows) ───────────────────┐
    │                                                       │
    │  ┌────────┐  ┌────────┐          ┌─────────────────┐ │
    │  │ Host CC│  │ Host CC│  ...     │  Host Kernel    │ │
    │  └───┬────┘  └───┬────┘          │  (lazy daemon)  │ │
    │      │ MCP       │ MCP           │  sessions.json  │ │
    │  ┌───▼────┐  ┌───▼────┐  ──RPC──►│  alive_sessions │ │
    │  │MCP srv │  │MCP srv │          │  alive_convs    │ │
    │  │(user-  │  │(user-  │          │  conversations/ │ │
    │  │ space  │  │ space  │          │  queue/         │ │
    │  │fan-out)│  │fan-out)│          └────────┬────────┘ │
    │  └───┬────┘  └───┬────┘                    │          │
    │      │ 跨机路由   │                          │          │
    │      │ 读 machine_│                          │          │
    │      │ info_log  │                          │          │
    └──────┼───────────┼──────────────────────────┼──────────┘
           │           │                          │
           │  读/写远端 queue（跨机 RPC）           │
           │  ┌────────┴──────────┐               │
           │  │ rpc_client 新增   │               │
           │  │ call_remote()     │               │
           │  └───────────────────┘               │
           │                                      │
    ┌──────┼─────────── WSL2 (Ubuntu) ────────────┼──────────┐
    │      │                                      │          │
    │  ┌───▼────────────┐          ┌──────────────▼────────┐ │
    │  │  WSL Kernel    │          │  /mnt/c/.../          │ │
    │  │  (lazy daemon) │          │  host data/           │ │
    │  │  sessions.json │          │  (WSL 经 /mnt/c/      │ │
    │  │  alive_sessions│          │   访问 host            │ │
    │  │  alive_convs   │          │   conversations —     │ │
    │  │  (仅 WSL-WSL)  │          │   只读)               │ │
    │  │  conversations/│          └───────────────────────┘ │
    │  │  queue/        │                                    │
    │  └───────▲────────┘                                    │
    │          │ RPC (仅本地 CC)                              │
    │  ┌───────┴────────┐                                    │
    │  │ WSL CC WSL CC  │  ...                               │
    │  │ MCP srv(user-  │                                    │
    │  │  space fan-out)│                                    │
    │  └────────────────┘                                    │
    └────────────────────────────────────────────────────────┘

    关键变化 (v2.1 vs 原设计):
    - kernel 之间不直接通信。跨机 fan-out 在 MCP server（user-space）完成。
    - WSL kernel 的 alive_conversations 仅追踪 WSL-WSL 本地对话。
    - host kernel 的 alive_conversations 追踪所有涉及 host 的对话（含跨机）。
    - WSL kernel 不追踪跨机对话（不需要 inform_connect/inform_unconnect）。
```

### 1.2 跨 realm 通信通道（全部实测验证）

| 方向 | 文件 I/O | 进程执行 |
|---|---|---|
| WSL → host | `/mnt/c/...` ✅ 读写 | `python.exe <script>` ✅ |
| host → WSL | `//wsl.localhost/Ubuntu/...` ✅ 读写（**forward-slash**） | `wsl.exe -d Ubuntu -- python3 <script>` ✅ |

**关键**：queue RPC 双向都可用直接文件 I/O，不需要 subprocess。`wsl.exe`
只在 spawn 交互式 CC（evoke/create_collaborator）时才需要。

**rpc_client 新增 `call_remote(machine, function, args)`**：
- 写请求到远端 queue（路径从 machine_info_log 获取）
- 轮询远端 queue/responses/
- **不做 ensure_core**（远端 kernel 的生命周期由远端 CC 管理）
- 超时（默认 30s）返回失败
- 跨机 RPC 写失败或超时 → 视同"对端不可达" → 返回 null/0/failed，**不重试不挂起**

### 1.3 数据存储分布

| 数据 | 存储位置 | 谁写 | 谁读 |
|---|---|---|---|
| Host CC session 信息 | host `data/server/sessions.json` | host kernel | host kernel + host MCP server |
| WSL CC session 信息 | WSL `data/server/sessions.json` | WSL kernel | WSL kernel + WSL MCP server |
| Host-Host 对话消息 | host `data/conversations/` | host kernel | host kernel, host CCs |
| WSL-WSL 对话消息 | WSL `data/conversations/` | WSL kernel | WSL kernel, WSL CCs |
| **Host-WSL 跨机对话消息** | **host `data/conversations/`** | **host kernel（唯一写者）** | host kernel, host CCs, WSL CCs (via `/mnt/c/` 只读) |
| machine_info_log | 各自 `data/machine_info_log/` | `machine_sign_up`/`machine_add` 脚本 | MCP server（每次跨机 fan-out 时重读） |
| queue (RPC) | 各自 `data/queue/` | 本机 CCs + 远端 MCP server（跨机 RPC via call_remote） | 本机 kernel |

**关键变化 (v2.1)**：
- `machine_info_log` 由独立脚本写入，kernel **不感知**。MCP server 每次跨机
  fan-out 时重读目录（简单、无陈旧、注册频率极低）。
- 跨机 queue RPC 由 MCP server 的 `rpc_client.call_remote()` 发起，kernel 不
  参与跨机路由。

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
| `scripts/lib/proc.js` | ⚠️ 需验证 | psutil/`/proc` 在 Linux 上行为；`resolveClaude` 搜进程名含 `claude`（Linux 二进制名是 `claude`，已验证） |
| `hooks/hooks.json` | ✅ 直接复用 | `node` 命令在 WSL 中可用（已验证） |
| `.mcp.json` | ⚠️ 需修改 | `"command": "python"` → `"python3"`（WSL 中 `python` 不在 PATH，只有 `python3`） |
| `skills/cc-communicate/SKILL.md` | ✅ 复用（Phase 1） | Phase 2 需更新（新函数、跨机说明） |
| `server/paths.py` | ✅ 直接复用 | `__file__`-relative 跨平台 |
| `server/proc.py` | ⚠️ 需验证 | psutil 跨平台；`resolve_claude` 在 Linux 上进程名是 `claude`（已验证 psutil 返回 `claude`） |
| `server/kernel.py` | ✅ 直接复用 | 纯 Python，无平台依赖 |
| `server/kernel_api.py` | ✅ 直接复用（Phase 1） | Phase 2：删除 is_local 参数，kernel 永不做跨机 fan-out |
| `server/check_core.py` | ⚠️ 需修改 | `_spawn_kernel()` 用了 Windows `creationflags`（见 2.4） |
| `server/rpc_client.py` | ⚠️ Phase 2 新增 | Phase 2 新增 `call_remote()` 函数 |
| `server/conversations.py` | ✅ 直接复用 | 纯路径操作 |
| `server/listen_poller.py` | ✅ Phase 1 复用 | Phase 2 合并为 listen.py（固定短间隔，无指数退避） |
| `server/spawn.py` | ❌ 需实现 Linux 分支 | `spawn_cc_new` / `spawn_cc_resume` 的 Linux 实现（见 2.3）；**用全路径 claude**（kernel init 时检测） |
| `server/user_functions.py` | ✅ 直接复用（Phase 1） | Phase 2 需修改（跨机编排在 MCP server） |
| `server/mcp_server.py` | ✅ 直接复用（Phase 1） | Phase 2 需新增工具声明 |

### 2.3 spawn.py 移植：tmux detached session

**核心认知纠正**：交互式 CC 需要的是 **pty（伪终端）**，不是 GUI。WSL2 原生
支持 pty。tmux detached session 提供 pty，CC 可在其中跑交互模式（处理初始
prompt 后进入 REPL，stay alive）。此机器还装了 WSLg（`DISPLAY=:0`），但 pty
已足够，GUI 无关。

**不能用 `claude -p`**：`-p` 模式处理完 prompt 就退出，CC 无法 stay alive
听后续消息。evoke 需要进程持久（CC 常驻，靠 task-notification 驱动多轮）。

**claude 二进制路径问题**：WSL 默认 PATH 中 `which claude` 返回
`/mnt/c/Users/Mocry/AppData/Roaming/npm/claude`（Windows 版！）。Linux native
claude 在 `/home/mocry/.npm-global/bin/claude`，不在默认 PATH。tmux new-session
里的 shell 也会找到 Windows 版。**解决**：kernel init 时检测自己的 claude
二进制路径（`psutil.Process(resolve_claude_pid).exe()`），存起来，spawn 命令
用全路径。

```python
# spawn.py Linux 分支
def spawn_cc_new(cwd: str, prompt: str):
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "/D", cwd, "claude", prompt])
    else:
        # tmux new-session -d: detached, 有 pty, CC 可跑交互模式
        # -s: session 名（用 cwd 的 basename + 时间戳，避免冲突）
        # -c: 设置工作目录（等价 Windows start /D）
        # claude 二进制用全路径（从 kernel init 时检测的路径）
        session_name = f"cc_{os.path.basename(cwd)}_{int(time.time())}"
        claude_bin = _get_claude_binary_path()  # kernel init 时从 resolve_claude pid 的 exe() 检测
        subprocess.Popen([
            "tmux", "new-session", "-d",
            "-s", session_name,
            "-c", cwd,
            claude_bin, prompt
        ])

def spawn_cc_resume(session_id: str, prompt: str):
    if os.name == "nt":
        _detached_popen(["cmd", "/c", "start", "claude", "--resume", session_id, prompt])
    else:
        session_name = f"cc_{session_id[:8]}_{int(time.time())}"
        claude_bin = _get_claude_binary_path()
        subprocess.Popen([
            "tmux", "new-session", "-d",
            "-s", session_name,
            claude_bin, "--resume", session_id, prompt
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
| claude 路径 | 系统 PATH 中的 `claude` | **全路径**（kernel init 时检测） |

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
        # 这是必需的，不是可选优化 — 父终端 SIGHUP 不会波及 kernel
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, kernel_py], **kwargs)
```

### 2.5 proc.py 移植：resolve_claude 在 Linux 上

`resolve_claude` 用 psutil 遍历进程树找 `claude` 祖先。psutil 跨平台，但需
确认 Linux 上 CC 进程名。

**已确认**：WSL2 CC 二进制名是 `claude`（虽然文件名叫 `claude.exe`，但它是
Linux ELF 二进制；psutil `Process(pid).name()` 实测返回 `claude`）。当前
`resolve_claude` 匹配 `"claude" in name`，Linux 上同样命中。

**注意**：Linux 上 CC 可能通过 vscode-server 的集成终端启动，进程树是
`vscode-server -> shell -> claude`。`resolve_claude` 向上遍历 parents 找
`claude`，不受中间层影响。

### 2.6 hooks/MCP 配置

- `hooks.json`：`node` 在 WSL 中可用（已验证 `/usr/bin/node`）。
- `.mcp.json`：`"command"` 需改为 `"python3"`（WSL 中 `python` 不在 PATH，
  只有 `/usr/bin/python3`）。
- arm_poller 命令串：改用 `sys.executable` 而非硬编码 `python`/`python3`。

确保 WSL 的 python3 安装了 `psutil`、`filelock`、`mcp`。

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
| `type` | 语义字段：说明机器类型（自动检测） | `win-host`, `wsl-ubuntu` | 显示、路由判断 |
| `id` | 区别字段：UUID4 | `a1b2c3d4-...` | 全局唯一标识；`type` 相同时区分（如两个 WSL distro） |

`machine_identity.json` 在 **kernel init 时首次生成**（若不存在）：
- `type`：自动检测。`os.name == "nt"` → `win-host`；否则检查 `/proc/version`
  是否含 `microsoft` → `wsl-ubuntu`；else → `linux-unknown`
- `id`：UUID4
- 存于 `data/server/machine_identity.json`（永久文件，后续启动不重新生成）
- kernel 用 `type`→`machine` 给本地 session 打字段；MCP server 用
  `{type, id, data_dir}` 做跨机路由

注册时双方交换 `{type, id, ...}`。`machine_info_log` 文件名用 `id`（保证
唯一），内容含 `type`（供语义判断）。

#### 3.1.2 握手协议（C:\ 根目录文件会合）

这是 **filesystem rendezvous at well-known location** 模式（见 #W9）。双方
通过 C:\ 根目录的临时文件互相发现，交换身份和数据库路径。C:\ 根目录已验证
可写（不需 admin）。

`machine_sign_up` 和 `machine_add` 是**独立脚本**（非 kernel 函数）。
直接写 `data/machine_info_log/` 目录。kernel **不感知**注册过程。

**路径视角原则**：每方在握手文件中写入**对方视角的** data 路径，对方直接
使用无需转换。
- WSL 写入 host 的路径为 WSL 视角：`/mnt/c/研究生/.../data`
- host 写入 WSL 的路径为 host 视角：`//wsl.localhost/Ubuntu/home/mocry/.../data`

**握手流程**（WSL 侧 `machine_sign_up.py`，host 侧 `machine_add.py`）：

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
- **启动清理**：启动时扫 C:\ 清掉自己的残留握手文件（按自己的 `id` 前缀匹配）。

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

**编码**：握手 JSON 文件使用 `ensure_ascii=False` + UTF-8 编码，便于人读
和 debug（中文路径不转义为 `\uXXXX`）。

#### 3.1.3 注册操作方式

提供两种方式（详见 §8 注册操作指南）：
1. **命令行**：用户先后启动 host 的 `machine_add.py` 和 WSL 的
   `machine_sign_up.py`
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

`machine` 字段值 = 本机 kernel init 时自动检测的 `type`（如 `wsl-ubuntu`、
`win-host`）。本机 session 的 `machine` = 本机 type。

**向后兼容**：缺失 `machine` 字段的旧记录视为本机 session。

`alive_sessions` 内存结构同步新增 `machine` 字段。

#### 3.2.2 machine_info_log

新增 `data/machine_info_log/` 目录。每个注册的机器一个文件
`{id}.json`，内容见 §3.1.2。由独立脚本（`machine_sign_up.py`/
`machine_add.py`）写入，kernel 不读此目录。MCP server 每次跨机 fan-out
时重读。

### 3.3 V1 内核函数调整

> **核心原则 (v2.1)**：kernel **取消 is_local 参数**，永远只做本地操作。
> 跨机 fan-out 全部在 MCP server（user-space）完成。kernel 保持 v0.1
> 单线程语义不变。

#### 3.3.1 process_session_ctrl_event()
**无变化。** 不同机器各司其职，各自管理自己机器中的 sessions。本机 hook 写
本机 `session_ctrl/`，本机 kernel replay。远端 session 不在本机 event log 中。

#### 3.3.2 withdraw(fromid, toid, init_connect)
**无变化。** 跨机 conversation 的 withdraw 操作由 host kernel 处理（跨机消息
在 host database），WSL kernel 不参与。MCP server 根据 `is_local` 判断
toid 归属，远端则通过 `call_remote` 调用 host kernel 的 withdraw。

#### 3.3.3 query_session(session_id)
**回到 v0.1 逻辑**：只查本机 `sessions.json`。kernel 不感知跨机，
`is_local` 参数取消。

MCP server 中的 `query_session` MCP tool 新增 user-space 路由逻辑：
```python
def query_session(sid):
    # 先查本地 kernel
    result = rpc_client.call("query_session", {"session_id": sid})
    if result:
        return result
    # 遍历注册机器（只读 machine_info_log 目录）
    for machine in read_machine_info_log():
        result = rpc_client.call_remote(machine, "query_session", {"session_id": sid})
        if result:
            return result
    return None
```

#### 3.3.4 check_alive(session_id)
**回到 v0.1 逻辑**：只查本机 `alive_sessions`。`is_local` 参数取消。

MCP server 中的 `check_alive` MCP tool 新增 user-space 路由逻辑（同
query_session 模式：先本地，miss 则遍历 machine_info_log 逐机
call_remote）。

#### 3.3.5 evoke(session_id)
**Phase 1**：evoke 保留为 kernel 函数（v0.1 不变），只给 `spawn.py` 加
Linux 分支。

**Phase 2**：kernel 的 evoke（spawn 部分）保留，MCP tool `evoke` 变成
user-space 编排函数：
1. `query_session(sid)` 确定 session 所在机器
2. 若在本机 → 调本机 kernel 的 evoke（spawn 本地 CC）
3. 若在远端 → 通过 `call_remote` 调用远端 kernel 的 evoke（spawn 远端 CC）
4. 全部查不到 → 返回 `session not exists`

connect 改调 MCP server 的 `evoke()` 而非 `rpc_client.call("evoke",...)`。

### 3.4 V1 用户函数调整

#### 3.4.1 query_conversations(session_id)

**输出格式变更**：list → dict。键为 partner session_id 字符串，值为 info。
（原 v0.1 返回 `[{partner: sid}, ...]`，不够简洁。）

```python
# v0.1: [{"partner": "abc..."}, {"partner": "def..."}]
# v2:   {"abc...": {...info}, "def...": {...info}}
```
需修改所有调用此函数的地方的处理逻辑。

**user-space 路由**：MCP server 的 `query_conversations` tool 做路由：
- 先查本机 kernel（本地 conversations）
- 再遍历 machine_info_log，对每台远端机器调 `query_conversations(id)`（via
  call_remote）
- 按 session_id 唯一性合并结果（重合的 sid 丢弃后者）

kernel 侧的 `query_conversations` **不变**（只查本地 conversations 目录）。

#### 3.4.2 connect(caller_sid, target_sid, hold_time=60)

**核心原则**：connect 保持 user-space（V1 模式），由调用方的 MCP server
编排。kernel 只提供单步非阻塞 RPC。跨机编排在 MCP server，kernel 纯本地。

**跨机 connect 流程**（以 WSL→host 为例；host→WSL 对称）：

```
MCP server 的 connect()（user-space 编排）:
  1. query_session(target_sid) [MCP server 做跨机 fan-out]
     → 本地 kernel miss → 遍历 machine_info_log
     → 通过 call_remote 查远端 kernel
     → 找到 target 在 host
  2. check_alive(target_sid) [MCP server 跨机 fan-out]
     → via call_remote -> host kernel check_alive
  3. if dead: evoke(target_sid) [MCP server 调用 evoke tool]
     → evoke tool 遍历机器 → 找到在 host
     → host kernel spawn 主机 CC (cmd /c start)
     → poll check_alive until alive
  4. register_conversation(caller_sid, target_sid)
     → 提交到 host kernel (via call_remote)
     ★ host kernel 注册到 alive_conversations
     ★ WSL kernel 不注册！(WSL kernel 不追踪跨机对话)
  5. send_message(caller_sid, target_sid, hello) [via call_remote → host kernel]
     → host kernel 写 host conversations/pipe
  6. run listen.py 合并脚本（本地, 替代旧 arm_poller → listen_poller → collect_messages 3 步）
     → subprocess.run([sys.executable, "listen.py", caller_sid, str(hold_time)])
     → listen.py 扫描 conversations/（WSL: 本地 + /mnt/c/ 只读；host: 本地）
     → 检测到回复 → settle 3s → 归档(pipe→log, 本地直接移, 跨机委托 host kernel)
     → stdout 输出消息 JSON → exit 0
     ★ 阻塞在 MCP server 进程，不阻塞任何 kernel
     ★ connect 是唯一跑 listen.py 的进程，防止双 poller 竞争（见 #W14）
  7. 解析 listen.py stdout → 提取 target_sid 的回复 → 返回 connect succeed
     （无需再调 collect_messages — listen.py 已做完检测+读取+归档）
```

**关键变化 (v2.1)**：
- **无 inform_connect**！WSL kernel 不追踪跨机对话。
- WSL kernel 的 alive_conversations 仅追踪 WSL-WSL 本地对话。
- host kernel 的 alive_conversations 追踪所有涉及 host 的对话（含跨机）。
- WSL kernel 退出不影响跨机通信（消息在 host，listen.py 扫 /mnt/c/ 直接读，
  WSL kernel 退出后 ensure_core 会重启）。
- 跨机 connect **不需要 inform_connect** 因为 WSL kernel 侧的信息对跨机通信
  无实际用途（只影响 idle-exit 判定，但 exit 了 ensure_core 也能 restart）。
- **connect 改用 listen.py**：替代 v0.1 的 arm_poller → subprocess.run(listen_poller) →
  collect_messages 三步流程。listen.py 是合并脚本，stdout 直接含消息 JSON，
  无需再调 collect_messages。同时防止双 poller 竞争（#W14）。

#### 3.4.3 send_message(fromid, toid, message)

**user-space 路由**：MCP server 的 `send_message` tool：
1. 判断 `toid` 本地/远端：
   - `query_session(toid)` → 若在本机 → 本地 `rpc_client.call("send_message", ...)`
   - 若在远端 → `rpc_client.call_remote(machine, "send_message", {fromid, toid, message})`
2. kernel 侧的 `send_message` **不变**（v0.1 逻辑，只检查本地 alive_conversations）

**为什么不是 kernel 做路由**：死锁风险。如果 send_message 也在 kernel 内做
跨机判断+转发，仍有阻塞/死锁风险（见 #W12）。

#### 3.4.4 keep_listen — 合并重构

**合并 arm_poller + listen_poller + collect_messages 为单脚本**（见 #W5、#W6
的详细分析）。

**废弃** `arm_poller` 和 `collect_messages` MCP tool（从 MCP server 移除）。
CC 接口从三步变一步，是 **breaking change**，SKILL.md 重写。

**一份 listen.py + 运行时判断 machine type**（非 host/WSL 各一份）：
- 脚本启动时读 `data/server/machine_identity.json` 获取 `type`
- `type == "wsl-ubuntu"` → 双路径路由（本地 + `/mnt/c/` 只读）
- `type == "win-host"` → 只扫本地 conversations/

**合并后的脚本逻辑**（WSL CC 侧）：

```
脚本启动(session_id, timeout):
  deadline = now + timeout
  SLEEP = 2  # 固定间隔（非指数退避！见 #W13）
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
      # 2d. 打印消息到 stdout, exit 0
      print(json.dumps(messages))
      exit(0)

    # 3. 无消息
    if now > deadline: exit(2)
    sleep(SLEEP)  # 固定 2-3s，不用指数退避（#W13）
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

6. **固定短间隔**：sleep 2-3 秒，不封顶。不用 v0.1 的指数退避（见 #W13）。

**CC 调用方式**（合并后）：
```bash
# 一行 Bash，后台运行
Bash("python3 <plugin_root>/server/listen.py <session_id> <timeout>", run_in_background=true)
# task-notification 带回 exit code (0=有消息, 2=超时) + stdout 里的消息 JSON
```

#### 3.4.5 close_connection(session_id, toid)

**user-space 编排**：MCP server 做路由：
- 本地双方对话 → 本地 `rpc_client.call("close_connection", ...)`
- 跨机对话 → 提交到 host kernel（via call_remote），host kernel 处理
  close（关闭、drain pending、send close notify、unregister）

**v2.1 关键变化**：**无 inform_unconnect**。WSL kernel 不追踪跨机对话。
host kernel 的 close_connection 处理后即完成，不需要通知 WSL kernel。

#### 3.4.6 create_collaborator(caller_sid, cwd, hold_time=60)

**跨机 create_collaborator**：新增 kernel 函数 `spawn_cc_new(cwd, prompt)` /
`spawn_cc_resume(sid, prompt)`。MCP server 通过 `call_remote` 让远端 kernel
执行本地 tmux spawn。理由：
- 和 evoke 跨机调用风格一致（对称）
- 解决 claude PATH 问题（远端 kernel 知道自己的 claude 全路径，本地 kernel
  init 时检测）
- spawn 是快速非阻塞操作（一次 Popen），kernel 做没问题

### 3.5 新增函数

#### 3.5.1 machine_sign_up.py（独立脚本）
WSL 侧一次性不对称脚本。生效条件：host 运行 `machine_add.py`。
详见 §3.1.2。**独立脚本，非 kernel 函数。**

#### 3.5.2 machine_add.py（独立脚本）
Host 侧一次性不对称脚本。详见 §3.1.2。**独立脚本，非 kernel 函数。**

#### 3.5.3 kernel_terminate()
提供 kernel 主动终结机制。可被外部调用（注册完成后主动释放进程负载，而非
等 idle timeout 自杀）。

#### 3.5.4 query_machines()
MCP server tool。返回 `machine_info_log/` 目录中注册的机器字典。键为机器
`id`，值为该机器的注册信息（`{type, data_dir, ...}`）。

#### 3.5.5 create_conversation_folder(id1, id2)
从 connect 中外接出来的函数。
- **Host 侧**：任何情况下直接在 host `data/conversations/` 中创建对话文件
  夹结构（跨机消息存 host）。
- **WSL 侧**：检查 id1, id2 是否都在 WSL 本地（`query_session` 查本地）。
  若都在本地 → 在本地 `conversations/` 创建。否则（至少一方在 host）→ 调用
  host kernel 的 `create_conversation_folder`（via call_remote）。

#### ~~3.5.6 inform_connect(fromid, toid)~~ — 已删除 (v2.1)
~~WSL kernel 一侧独有的函数，host 可调用（via `//wsl.localhost/` queue）。
向 WSL 同步一起完全由 host 完成的、成功的 connect。~~

**v2.1 删除理由**：Option B 后 WSL kernel 不追踪跨机对话（WSL kernel 侧
的跨机对话记录对跨机通信无实际用途）。WSL kernel alive_conversations 只
追踪 WSL-WSL 本地对话。

#### ~~3.5.7 inform_unconnect(fromid, toid)~~ — 已删除 (v2.1)
~~同 inform_connect，只有 host 独立完成全部 close 操作时，在最后告诉 WSL。~~

**v2.1 删除理由**：同 inform_connect。跨机关闭由 host kernel 独立完成。

#### 3.5.6 rpc_client.call_remote(machine, function, args, timeout=30)
rpc_client 新增函数：
- `machine`：从 `machine_info_log` 条目获取的 `{type, id, data_dir}` 字典
- `function`：要调用的远端 kernel 函数名
- `args`：参数 dict
- 写请求到远端 `data/queue/`（路径从 `machine["data_dir"]` 构建）
- 轮询远端 `data/queue/responses/`（直接文件 I/O，不经本机 kernel）
- **不做 ensure_core**（远端 kernel 的生命周期由远端 CC 管理）
- 超时（默认 30s）返回失败
- 跨机 request 文件名加 machine type 前缀（如 `wsl-ubuntu_<ts>_<rid>.json`），
  防止跨机 request_id 碰撞（见 C14）

---

## 4. 技术难点与解决方案（全部已验证）

> 以下编号 #W1-#W13 对应 WSL2 移植的技术挑战。与 `core_plan.md` 的 #0-#11
> （v0.1 基础设计挑战）互补。所有结论均实测验证或逻辑推理确认。

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

### #W4 跨机 fan-out 全在 user-space（kernel 纯本地）

**问题**：跨机 fan-out（如 query_session 遍历 machine_info_log 查远端）应该
在哪层做？kernel 还是 user-space？

**原始设计错误**：把 `is_local` 参数放在 kernel 函数里（§3.3.3/§3.3.4 原版），
kernel 查本地 miss 后遍历 machine_info_log 做跨机 queue RPC。这会导致**死锁**
（见 #W12）——kernel 单线程循环，做跨机 RPC 时阻塞轮询远端响应，无法 drain
本地 queue。两个 kernel 互相等 → 死锁。

**正确设计 (v2.1)**：**所有跨机 fan-out 在 user-space（MCP server）完成。**
- kernel 取消 `is_local` 参数，回到 v0.1 逻辑（纯本地操作）
- MCP server tool（如 query_session）先调本地 kernel，miss 则遍历
  machine_info_log 通过 `rpc_client.call_remote()` 查远端
- 阻塞发生在 MCP server 进程（和 v0.1 connect 阻塞在 MCP server 一致）
- v0.1 connect 保持 user-space 是同一个原则——kernel 永不阻塞

**连锁简化**：
1. kernel 不感知 is_local，不需要 machine_info_log
2. inform_connect / inform_unconnect 不需要（WSL kernel 不追踪跨机对话）
3. 双 kernel alive_conversations 无需同步（各自管本地对话）
4. 设计更简洁，无死锁风险

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
- 归档（pipe→log）：委托 host kernel（via call_remote）

### #W8 跨机 RPC：call_remote（via 直接文件 I/O）

**问题**：MCP server 如何调用远端 kernel 的函数？

**v2.1 方案**：rpc_client 新增 `call_remote(machine, function, args)`。
- `machine` 从 machine_info_log 获取（含 `data_dir` — 对方视角的路径）
- 直接写远端 `data/queue/`（文件 I/O，不需要 subprocess）
- 轮询远端 `data/queue/responses/`（同上）
- **不做 ensure_core** — 远端 kernel 生命周期由远端 CC 管理
- 跨机 RPC 失败 → 返回 null/0/failed，不重试不挂起

**对称性**：
- WSL → host：call_remote 写 `/mnt/c/.../data/queue/`
- host → WSL：call_remote 写 `//wsl.localhost/Ubuntu/.../data/queue/`

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

### #W10 is_local 概念上移到 user-space

**v2.1 重述**：is_local 不再是 kernel 参数，而是 user-space（MCP server）
概念。

**原设计问题**：`is_local` 作为 kernel 参数会导致 deadlock（#W12）。
`is_local=0` 时 kernel 内做跨机 fan-out → 阻塞单线程循环。

**v2.1 方案**：MCP server 做路由逻辑：
```python
# MCP tool 层
def query_session(sid):
    result = rpc_client.call("query_session", {"session_id": sid})  # 本地 kernel
    if result:
        return result
    for machine in read_machine_info_log():  # MCP server 读目录
        result = rpc_client.call_remote(machine, "query_session", {"session_id": sid})
        if result:
            return result
    return None
```

kernel 的 `query_session` 永远只做本地查询（v0.1 逻辑）。不会级联——
MCP server 对每个远端机器分别 call_remote，不存在 A→B→A 循环。

**对 CC 透明**：CC 永远不传 `is_local`。CC 不感知跨机细节。

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

### #W12 死锁规避：kernel 纯本地

**问题**：如果 kernel 处理跨机 RPC（查远端 queue），两个 kernel 可能互相等待
对方 → 死锁。

**死锁场景**（原设计）：
1. WSL CC connect(WSL→host) → WSL kernel query_session(host_sid, is_local=0)
   → WSL kernel 阻塞轮询 host queue 响应
2. 同时 host CC connect(host→WSL) → host kernel query_session(wsl_sid,
   is_local=0) → host kernel 阻塞轮询 WSL queue 响应
3. 两个 kernel 互相等对方处理自己的请求，各自卡在轮询上 → 死锁

**即使不死锁**：单次跨机 RPC 让 kernel 在 ~100ms-5s 内无法 drain 本地 queue
和其他请求。

**解决方案 (Option B)**：**kernel 永远只做本地操作**。跨机 fan-out 全部在
MCP server（user-space）完成。阻塞发生在 MCP server 进程（和 v0.1 connect
阻塞在 MCP server 一致）。kernel 保持 v0.1 单线程语义不变。

### #W13 listen poller 退避设计问题

**问题**：v0.1 `listen_poller.py` 使用指数退避（5s→10s→20s→40s→80s→160s→
300s 封顶 5 分钟）。armed 超过 ~3 分钟后检查间隔超过 connect 的 60s 窗口，
必然漏消息。

**影响**：单机场景不易触发（CC 通常 arm 后很快有消息）；跨机场景下对端 CC
从收到指令到调用 connect 可能有几分钟延迟，必现。

**实测验证**（2026-07-11）：host CC armed poller 2 分钟后，对方 connect。
Poller 处于 300s 长睡眠中，完全错过 60s 窗口。对方超时 -> withdraw 清理 ->
poller 醒来什么都看不到。

**解决方案**：合并后的 `listen.py` 使用**固定短间隔**（2-3 秒）或封顶 10
秒。不用指数退避。跨机场景对端可能在任何时间发消息，大退避不适应这种需求。

### #W14 双 poller 竞争

**问题**：v0.1 的 connect 内部 spawn listen_poller 子进程监听回复。如果 CC 同时
自己 arm 了后台 poller（如通过 skill 提前调用 arm_poller），就会有两个 poller
同时监听同一个 pipe。先检测到的 poller 触发 collect_messages 归档消息（pipe→log），
后检测到的 poller 看到空 pipe，永远等不到触发 → 误判超时。

**实测验证**（2026-07-11）：审查 CC 在 connect 前已经 arm 了后台 poller
（timeout=600s）。connect 发送 hello 后，target 在 8.3s 后回复。后台 poller
（更早启动）先检测到 → collect_messages 归档回复 → connect 内部 poller 醒来
看到 count_undelivered=0 → 继续睡 → timeout。

**解决方案**：合并后的 `listen.py` 替代旧三步流程。**connect 是唯一运行
listen.py 的进程**——不在 connect 外单独 arm poller。合并后的 listen.py
归档（pipe→log）和 connect 的回复检测在同一进程中完成，不存在竞争。

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
| C8 | kernel 做跨机 fan-out | **死锁**（两 kernel 互相等） | 跨机 fan-out 全部在 user-space，kernel 纯本地（Option B） |
| C9 | 路径视角混淆 | host 用了 WSL 视角路径（或反过来），file not found | 握手文件写对方视角路径，存在 `data_dir_for_peer` 字段 |
| C10 | 旧 sessions.json 缺 `machine` 字段 | 跨机路由判断失败 | 缺失视为本机 session（向后兼容） |
| C11 | tmux session 名冲突 | 多次 evoke 同一 sid 导致 tmux session 名碰撞 | session 名含 sid 前 8 位 + 时间戳，或先 kill 旧 session |
| **C12** | **WSL 中 `python` 不在 PATH** | `.mcp.json` 和 arm_poller 命令失败 | `.mcp.json` 改 `"command": "python3"`；arm_poller 命令串用 `sys.executable` |
| **C13** | **WSL 中 `which claude` 返回 Windows 版** | tmux spawn 起的是 Windows CC 而非 Linux CC | kernel init 时检测自己的 claude 二进制路径（`psutil.Process(resolve_claude_pid).exe()`），spawn 命令用全路径 |
| **C14** | **跨机 queue request_id 碰撞** | 两个 MCP server 同时生成 uuid4 可能（极低概率）冲突 | 跨机请求文件名加 machine type 前缀（如 `wsl-ubuntu_<ts>_<rid>.json`） |
| **C15** | **connect 外提前 arm poller 导致双 poller 竞争** | 两个 poller 同时监听，先检测者归档消息后，后者永远等不到触发 | connect 改用 listen.py 合并脚本，connect 是唯一运行 listen.py 的进程 |

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
| **WSL `python` 在 PATH** | `which python` in WSL | ❌ **NOT_FOUND**，只有 `/usr/bin/python3`（C12） | 2026-07-11 |
| **WSL `node` 在 PATH** | `which node` in WSL | ✅ `/usr/bin/node` | 2026-07-11 |
| **WSL `claude` 二进制路径** | `which claude` in WSL | ⚠️ 返回 `/mnt/c/...`（Windows 版），Linux claude 在 `/home/mocry/.npm-global/bin/claude` 不在默认 PATH（C13） | 2026-07-11 |
| **WSL CC 进程名** | `psutil.Process(<pid>).name()` | ✅ 返回 `claude`（非 `claude.exe`），resolve_claude 可匹配 | 2026-07-11 |
| **Poller 退避漏消息** | host CC armed 2min 后对方 connect | ❌ poller 在 300s 睡眠中，完全错过 60s 窗口（#W13） | 2026-07-11 |
| **双 poller 竞争** | 前台 poller + connect 内部 poller 同时监听 | ❌ 前台 poller 先归档回复（pipe→log），connect 内部 poller 醒来看到空 pipe → 永远等不到 → timeout（#W14） | 2026-07-11 |

---

## 7. Build 逐步清单

### Phase 1: in-WSL cc-communicate（独立可用）

| 步骤 | 任务 | 验证标准 |
|---|---|---|
| 1.1 | 在 WSL 中部署插件副本（独立目录，非 /mnt/c/） | 文件结构完整 |
| 1.2 | WSL python3 安装 deps（psutil, filelock, mcp） | `pip install -r requirements.txt` 成功 |
| 1.3 | 验证 tmux 内 claude 可用（**先测全路径**） | `tmux new-session -d '<claude_full_path> --version > /tmp/x 2>&1'`，/tmp/x 有输出 |
| 1.4 | 实现 `spawn.py` Linux 分支（tmux + 全路径 claude） | `tmux new-session -d` 能起 CC |
| 1.5 | 修改 `check_core.py` `_spawn_kernel()` Linux 分支 | kernel 能 lazy-start，写 core_status.json |
| 1.6 | 验证 `proc.py` `resolve_claude` 在 Linux 上 | `my_session_id()` 返回 UUID |
| 1.7 | 验证 hooks 触发（SessionStart/End） | `data/session_ctrl/` 有事件文件 |
| 1.8 | 验证 MCP server 启动 | `/mcp` 显示 cc-communicate |
| 1.9 | **Phase 1 端到端测试**：两个 WSL CC p2p | connect→send→listen→collect→close 全流程 |
| 1.10 | 验证 `evoke`（tmux detached CC） | 复活 dead CC，能收消息 |
| 1.11 | 验证 `create_collaborator`（tmux spawn） | 新 CC 注册 + connect 成功 |

### Phase 2: 跨 realm 通信

| 步骤 | 任务 | 验证标准 |
|---|---|---|
| 2.1 | 实现 `machine_sign_up.py`（WSL 侧）/ `machine_add.py`（host 侧）独立脚本 | C:\ 根目录握手成功，双方 machine_info_log 有记录 |
| 2.2 | sessions.json + alive_sessions 新增 `machine` 字段（kernel init 时自动检测 type + 生成 machine_identity.json） | 旧记录向后兼容；新 session 有 machine 字段 |
| 2.3 | rpc_client 新增 `call_remote()` 函数 | MCP server 能调用远端 kernel 函数 |
| 2.4 | MCP tool 层新增 user-space 路由逻辑（query_session, check_alive, query_conversations, send_message, close_connection） | 本地/跨机返回正确结果 |
| 2.5 | kernel 函数**删除 is_local 参数**，回到 v0.1 逻辑 | 本地操作不变，跨机不进入 kernel |
| 2.6 | 合并 keep_listen 为单脚本 `listen.py`（固定 2-3s 间隔，一份脚本 + 运行时判断 type） | CC 一行 Bash 完成听消息 |
| 2.7 | **connect 改用 listen.py**：替代旧 arm_poller → listen_poller → collect_messages 三步流程。`subprocess.run(listen.py, sid, timeout)`，stdout JSON 直接提取回复。connect 是唯一跑 listen.py 的进程（防双 poller 竞争 #W14）。**无 inform_connect**。 | WSL CC ↔ host CC connect 成功，无双 poller 竞争 |
| 2.8 | 调整 `close_connection`（**无 inform_unconnect**） | 跨机关闭后双方状态正确 |
| 2.9 | 实现 `create_conversation_folder` / `kernel_terminate` / `query_machines` | 各函数功能正常 |
| 2.10 | 新增 kernel 函数 `spawn_cc_new`/`spawn_cc_resume`（供 call_remote 用） | host 能远程让 WSL kernel 本地 tmux spawn |
| 2.11 | 更新 `SKILL.md`（新函数、跨机说明、keep_listen 合并后用法；breaking change：arm_poller/collect_messages 废弃） | CC 能按文档操作 |
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

*Last updated: 2026-07-11 (v2.1). Based on wsl2_core_plan.md v2 + WSL2 CC 设计审查。所有技术结论实测验证或审查确认。*
