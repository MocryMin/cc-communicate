# cc-communicate 加固提案集（Proposal–Revise–Accept）

> 日期：2026-07-24  
> 面向版本：`v0.3.0`，当前权威实现为 `v2_win/` 与 `v2_wsl/`  
> 目的：在构建探索式上层 Agent Runtime 之前，把 cc-communicate 加固成可依赖的 Agent Transport / Worker Fabric  
> 文档性质：待 `cc-builder` 逐条审议的 proposal；不是已批准的实现命令

---

## 0. 结论与边界

cc-communicate 的长期角色应限定为：

1. CC session 的身份、发现、存活检测；
2. Worker 的创建、恢复和定位；
3. 本机及 Windows/WSL 跨 realm 路由；
4. 持久、可重投、可诊断的消息传输；
5. 传输级连接与生命周期管理。

以下内容**不进入 cc-communicate**：

- PlanGraph、DecisionNode、Assumption、Candidate；
- Probe 选择策略、风险评分和搜索预算；
- Evidence 的领域含义与可信度判断；
- git worktree、代码 checkpoint、rollback；
- 上层任务何时提交、回退或重规划。

cc-communicate 可以提供通用的 `kind`、`correlation_id`、`payload`、`artifact_refs` 等传输字段，但不得内置 `probe.success`、`decision.commit` 等上层业务状态机。

### 目标交付语义

本轮加固应明确采用：

> **at-least-once delivery + per-store ordering + detectable duplicates + idempotent mutation**

不承诺“网络/进程崩溃条件下的严格 exactly-once”。所谓“可靠”应具体意味着：

- 已成功提交的消息不会因文件名冲突、时钟回拨、跨 realm 时钟差而静默消失；
- 未被确认的消息允许重投；
- 每条消息有稳定身份，接收方能够去重；
- RPC 重试不会无依据地重复 spawn、send 等副作用；
- 所有降级、重试和残留都可观测。

---

## 1. Proposal–Revise–Accept 制度

### 1.1 Proposal 状态

每条 proposal 独立流转：

```text
PROPOSED
   ├── ACCEPTED_FOR_BUILD
   ├── REVISE_REQUESTED → REVISED → ACCEPTED_FOR_BUILD
   └── REJECTED / DEFERRED

ACCEPTED_FOR_BUILD
   → IMPLEMENTED
   → VERIFIED
   → ACCEPTED
```

- `PROPOSED`：本文件的初始状态；尚未授权实现。
- `REVISE_REQUESTED`：cc-builder 认为目标正确但结构需调整。
- `ACCEPTED_FOR_BUILD`：上层规划者确认修改后的结构，可以搭建。
- `IMPLEMENTED`：代码已完成，但尚不能等同于 proposal 被接受。
- `VERIFIED`：proposal 自己的 acceptance criteria 已有证据。
- `ACCEPTED`：上层规划者阅读加固报告后最终接收。

### 1.2 cc-builder 对每条 proposal 的必填审核格式

```markdown
### HP-XX Review

- Decision: ACCEPT / REVISE / REJECT / DEFER
- Rationale:
- Conflicts with current implementation:
- Proposed revision, if any:
- Files/modules expected to change:
- Tests to add:
- Compatibility impact:
- Residual risks after implementation:
```

不能只写“同意”或“已实现”。如果 cc-builder 采用了不同结构，必须说明它如何继续满足原 proposal 的不变量与验收条件。

### 1.3 实施纪律

- proposal 按依赖关系分批接受，不要求一次接受全部。
- 不在同一个未验证的大提交里同时重写消息协议、跨 realm 路由和源码布局。
- 新协议必须先有自动化故障测试，再移除旧兼容路径。
- 所有破坏兼容性的改动必须带 schema/version 和迁移说明。
- `v2_win/` 与 `v2_wsl/` 必须同步，或先建立单一源码生成机制。

---

## 2. 优先级与建议实施波次

| 波次 | Proposal | 目标 |
|---|---|---|
| Gate 0 | HP-00 | 固化现状，建立自动化故障测试与验收基线 |
| Wave 1 | HP-01、HP-02、HP-03、HP-06 | 消除静默丢失、错误 ACK、重复副作用和路径风险 |
| Wave 2 | HP-04、HP-05、HP-07 | 支持并行 Worker、可靠关联和结构化调用 |
| Wave 3 | HP-08、HP-09、HP-10、HP-11 | 生命周期、资源、安全、持久化与可诊断性 |
| Wave 4 | HP-13 | 消除双份源码维护风险 |

建议在每个 Wave 后运行完整回归，并由上层规划者决定是否继续下一 Wave。

---

## HP-00：建立可重复的自动化基线与故障注入测试

**状态：PROPOSED**  
**优先级：Gate / 必须最先处理**  
**实现难度：M**  
**依赖：无**

### 必要性

当前仓库拥有很有价值的真实测试记录，包括跨 realm、kernel restart、取消安全和 1v4 多会话验证，但没有独立自动化测试代码。后续会触碰消息文件格式、ACK、RPC retry、spawn 和跨 realm 路由；没有可重复基线时，修复一个竞态很容易重新引入另一个竞态。

### 结构提议

新增 `tests/`，至少分为：

```text
tests/
  unit/
    test_message_record.py
    test_cursor_ack.py
    test_rpc_idempotency.py
    test_validation.py
    test_spawn_correlation.py
  integration/
    test_kernel_restart.py
    test_cancel_redelivery.py
    test_concurrent_senders.py
    test_dual_store_cursor.py
  live/
    README.md
    test_windows_cc_manual.md
    test_wsl_cross_realm_manual.md
```

测试不得写入已安装插件的真实 `data/`。可选择：

1. 为 `paths.py` 增加测试可覆盖的 `CC_COMMUNICATE_DATA_DIR`；或
2. 每个测试把插件复制到独立临时目录；或
3. 把 path/state 依赖注入 kernel fixture。

推荐 1，且该环境变量随后可被 HP-10 正式采用。

### 首批必须固化的 v0.3 行为

- SessionStart/End replay 与 PID+start_time liveness；
- register → send → listen peek → ACK archive；
- listen 被取消后消息仍可重投；
- kernel restart 后 session、conversation、ACK 状态可恢复；
- remote request 可唤醒已退出 kernel；
- Windows/WSL 源码除平台入口差异外保持等价。

### 结构可行性

现有 kernel API 多数把状态 dict 显式作为参数传入，适合单元测试。文件系统协议也容易用临时目录和伪造消息文件测试，不需要真实启动 CC 才能覆盖大部分正确性。

### 风险与缓解

- **风险：测试为适应当前实现而锁死错误行为。** 仅固化外部契约和已确认不变量，不固定内部轮询次数、日志文本等实现细节。
- **风险：Windows 专属测试在 CI 不可用。** 单元测试跨平台；真实 CC/WSL 测试保留为明确的 live gate。

### Acceptance criteria

- `pytest` 单命令可运行非 live 测试；
- 所有测试使用独立临时 data root；
- 可重复证明“取消不丢消息”和“kernel restart 后可恢复”；
- builder 报告记录测试命令、测试数量、运行平台和输出摘要；
- 后续每条 proposal 都必须新增会在旧代码上失败、在新代码上通过的测试。

---

## HP-01：版本化消息记录、唯一 message_id、单调 sequence 与原子发布

**状态：PROPOSED**  
**优先级：P0 / Wave 1**  
**实现难度：L**  
**依赖：HP-00**

### 必要性

当前消息文件以 `<wall_clock_ms>__<from>__<to>.md` 命名并用普通 `open(..., "w")` 写入，已经确认存在：

- 同毫秒同方向 send 覆盖；
- wall clock 回拨破坏排序；
- 跨 realm 时钟偏差破坏全局 watermark；
- 直接写最终文件时，直接扫描 pipe 的代码可能观察到尚未完全写完的文件。

这些风险在人类低频对话中可能罕见，但上层 Agent 会并行、突发地派发消息，概率和影响都会上升。

### 结构提议

每个 conversation store 由其本地单线程 kernel 分配持久单调序号。建议消息记录：

```json
{
  "schema_version": 1,
  "message_id": "uuid-v4-or-v7",
  "store_id": "machine-identity-id",
  "sequence": 42,
  "from_session": "...",
  "to_session": "...",
  "kind": "text",
  "correlation_id": null,
  "causation_id": null,
  "created_at_ms": 1780000000000,
  "payload": {"text": "..."}
}
```

建议文件名：

```text
<sequence:020d>__<message_id>.json
```

其中：

- `sequence` 是唯一排序/ACK 单位；
- `message_id` 是端到端身份和去重单位；
- `created_at_ms` 只用于展示和诊断，不能参与正确性判断；
- `kind` 保持传输层通用，不引入上层 Plan/Probe 语义。

### sequence 分配与 crash 语义

建议保存 `message_sequence.json`：

```json
{"schema_version": 1, "store_id": "...", "last_allocated": 42}
```

分配流程：

1. kernel 内存中取 `last_allocated + 1`；
2. 先原子持久化新的 `last_allocated`；
3. 再发布消息文件；
4. 若 2 与 3 之间崩溃，允许 sequence 出现 gap；不得复用旧 sequence；
5. 启动时以 counter 和现存消息最大 sequence 的较大者恢复。

### 原子发布

消息先写唯一临时文件，`flush + fsync` 后 `os.replace` 到包含 UUID 的唯一最终路径。最终路径即使 sequence 因 bug 重复，也因 `message_id` 不同而不会覆盖既有消息。

### 插入点

- `server/conversations.py`：新 message record、文件名 parse/format；
- `server/kernel.py`：加载/保存 sequence state；
- `server/kernel_api.py::send_message`：分配、封装、原子发布；
- `server/user_functions.py`：兼容返回值与路由；
- `server/paths.py`：sequence state 路径。

### 结构可行性

conversation store 的 mutation 已集中经过单线程 kernel，天然适合作为 sequence allocator；remote send 也最终进入实际 store 所属 kernel。无需引入跨 kernel 全局计数器，也不需要把 SQLite 作为本轮前置条件。持久 counter + UUID 最终文件名足以在允许 sequence gap 的前提下消除覆盖和时钟依赖。

### 兼容策略

至少一个版本内 reader 同时识别旧 `.md` 与新 `.json`；writer 只写新格式。旧消息可按 `(created_at_ms, filename)` 读出，但不能伪造为可靠 sequence。builder 必须提出清晰的旧 ACK 迁移策略，不能静默把 timestamp 当作 sequence。

### 风险与缓解

- **风险：文件格式迁移影响旧 listener。** 双 reader、协议版本和明确 deprecation window。
- **风险：counter state 损坏。** 启动时扫描现存最大 sequence，自愈到不小于历史最大值。
- **风险：跨 realm 文件系统的 fsync/rename 语义不同。** 正确性依赖“最终文件通过原子 rename 出现”；必须在 NTFS、WSL ext4、`/mnt/c` 和 `//wsl.localhost` 路径做验证。

### Acceptance criteria

- 模拟 1000 次相同 wall clock 的同方向 send，无覆盖、无丢失；
- 模拟 wall clock 回拨，顺序仍按 sequence 正确；
- 每条消息均有唯一 `message_id`；
- reader 不会读到部分 JSON；
- kernel 在 counter 已递增但消息未发布的崩溃场景下允许 gap、绝不复用 sequence；
- v0.3 遗留 `.md` 仍可读取，迁移行为有测试。

---

## HP-02：以 per-store cursor 替代单一 timestamp watermark

**状态：PROPOSED**  
**优先级：P0 / Wave 1**  
**实现难度：L**  
**依赖：HP-01**

### 必要性

当前一个 session 只保存一个 timestamp watermark；WSL caller 会合并本地 store 与 host store 的时间戳。两个 store 的时钟不一致时，一个 store 的高 watermark 可能错误归档另一个 store 尚未真正交付的消息。

### 结构提议

ACK 状态改为：

```json
{
  "schema_version": 1,
  "sessions": {
    "session-id": {
      "store-id-a": 42,
      "store-id-b": 17
    }
  }
}
```

新 listen 协议建议：

```json
request:
{
  "session_id": "...",
  "cursors": {"store-a": 41, "store-b": 17},
  "timeout": 30
}

response:
{
  "messages": [...],
  "next_cursors": {"store-a": 45, "store-b": 17}
}
```

归档规则只能是：

```text
message.store_id == S AND message.sequence <= cursors[S]
```

不能比较来自不同 store 的 sequence，也不能使用 `created_at_ms`。

### 重要语义

cursor ACK 表示“调用方已把这批 transport message 持久接收”，不表示上层任务已经执行完成。未来上层 Runtime 应先把消息写入自己的 Run/Event Store，再推进 cursor。

### API 兼容选项

cc-builder 必须在审核中选择并论证：

- **A（推荐）**：新增 `listen_v2(cursors=...)` 与 `query_my_cursors()`，旧 `listen` 保留一个 deprecation release；
- **B**：扩展现有 `listen` 接受可选 cursors，并在未提供时进入 legacy timestamp 模式；
- **C**：直接破坏性升级现有 `listen`，同时 bump major protocol version。

不建议把多个 store 的 sequence 再压成一个整数。

### 插入点

- `kernel.py`：加载/持久化 cursor map；
- `kernel_api.py::listen_scan/query_ack_timestamp/upload_ack_timestamp`：升级为 store cursor；
- `user_functions.py::listen/close_connection`：分别扫描、合并多个 store 的消息，但不合并 cursor 数值；
- `mcp_server.py` 与 `SKILL.md`：新 API 与迁移说明。

### 结构可行性

当前 WSL caller 已分别调用 local `listen_scan` 与 host remote `listen_scan`，说明 store 边界在代码中已经存在；主要变化是保留两个 scan 的独立 cursor，而不是取一个最大 timestamp。因此无需改变现有“跨机器 conversation 存在 host”的路由决策。

### 风险与缓解

- **风险：CC 忘记携带完整 cursor map。** 提供 `query_my_cursors` 恢复；上层确定性客户端负责 ACK。
- **风险：一次返回多条消息但模型只处理部分。** transport 默认整批不确认；只有调用方明确提交 `next_cursors` 才归档。必要时 builder 可提议显式 `ack_through(store_id, sequence)` 工具。
- **风险：旧 timestamp state 无法无损转换。** 旧状态保留为 legacy namespace，不猜测性转换；新协议首次使用从显式迁移点开始。

### Acceptance criteria

- 两个 store 使用完全不同的伪造时钟，仍无跨 store 错误归档；
- ACK store A 不会影响 store B；
- listen 取消后本批消息仍可重投；
- cursor state 在 kernel restart 后可恢复；
- 重复提交同一 cursor 幂等；提交较小 cursor 不允许回退已确认位置；
- legacy API 的支持期限与迁移行为被记录。

---

## HP-03：RPC operation_id、领域幂等与安全重试

**状态：PROPOSED**  
**优先级：P0 / Wave 1**  
**实现难度：L**  
**依赖：HP-00；send 路径最终依赖 HP-01**

### 必要性

当前 RPC client 超时后会重新 `_submit`，生成新的 request id。若第一次请求已经执行、只是 response 未被调用方观察到，第二次会再次执行副作用：

- `send_message` 可能重复发送；
- `spawn_cc_new` 可能创建两个 Worker；
- `evoke` 可能重复拉起同一 session；
- fire-and-forget remote 请求可能残留并在之后执行。

### 结构提议

区分：

```text
request_id   = 某一次传输尝试的身份
operation_id = 一次逻辑操作跨 retry 的稳定身份
```

同一个 client call 的所有 retry 必须复用 `operation_id`。不要泛化承诺严格 exactly-once，而是为每种 mutation 定义幂等键：

| mutation | 幂等键/处理方式 |
|---|---|
| send_message | `message_id`；已存在则返回原 send result |
| register/unregister | 本身按 canonical pair 幂等 |
| create_conversation_folder | `exist_ok`，按 pair 幂等 |
| spawn_cc_new | `spawn_token`，见 HP-04 |
| evoke | `(session_id, revive_operation_id)`；先检查 liveness/pending revive |
| cursor upload | `max(current, submitted)` |
| withdraw/delete | 显式目标 id；重复调用返回 already-done，而非任意删除“最新一条” |

kernel 可维护有限期限的 operation journal：

```json
{"operation_id":"...","function":"...","status":"completed","result":{...}}
```

journal 至少覆盖不可自然幂等的 mutation，并具有 TTL/容量上限。

### 插入点

- `rpc_client.py::_submit/call/_submit_remote/call_remote`；
- `kernel.py::drain_queue/_dispatch`；
- `kernel_api.py` 各 mutation；
- HP-04 的 spawn registry。

### 结构可行性

文件队列 request 已有 UUID request_id，增加稳定 operation_id 不改变 transport 方式。多数现有 mutation 天然幂等或可通过领域 id 幂等；真正困难的 send/spawn 分别由 HP-01 message_id 与 HP-04 spawn_token 提供可恢复事实源，因此不必用一个庞大事务系统包住整个 kernel。

### 风险与缓解

- **风险：在副作用完成后、journal 标记完成前崩溃。** 领域对象自身携带幂等 id；重启后从 message/spawn registry 判断，而非只信内存 journal。
- **风险：journal 无限增长。** TTL + 最大条目数；仍未完成的 operation 不得因普通 GC 丢失。
- **风险：withdraw“撤回最新一条”无法可靠重试。** 新协议要求按 `message_id` 撤回；旧接口标记 legacy/non-idempotent。

### Acceptance criteria

- 人为丢弃第一次 response，retry 不会产生第二条消息；
- 同一 spawn operation retry 不会创建第二个 CC；
- local 与 remote RPC 都携带稳定 operation_id；
- kernel restart 后对已持久化的 message_id/spawn_token 仍可去重；
- builder 明确列出仍非幂等的 API，并说明为何保留。

---

## HP-04：spawn_token 与结构化 WorkerHandle

**状态：PROPOSED**  
**优先级：P0 / Wave 2**  
**实现难度：L**  
**依赖：HP-03**

### 必要性

当前 `create_collaborator` 通过“cwd 相同且 started_at 晚于 since_ts”寻找新 session，并最终只返回 connect 文本。并行在同一 cwd 创建多个 Worker 时，多个调用可能认领同一个最新 session；上层也无法稳定获得 machine、session、spawn operation 和连接状态。

### 结构提议

每次 spawn 生成 `spawn_token`，通过子进程环境传递：

```text
CC_COMMUNICATE_SPAWN_TOKEN=<uuid>
```

`registrar.js` 在 SessionStart 事件中附带可选 `spawn_token`；kernel 保存：

```text
spawn_token → session_id, pid, cwd, machine_id, started_at
```

新结构化返回值：

```json
{
  "ok": true,
  "worker": {
    "session_id": "...",
    "machine_id": "...",
    "cwd": "...",
    "spawn_token": "...",
    "source": "new|resume"
  },
  "connection": {
    "status": "established|pending|failed",
    "connection_id": "..."
  }
}
```

建议拆出低层 `spawn_collaborator`（只 spawn + register）和组合层 `create_collaborator`（spawn + connect）。上层 Runtime 通常需要先取得 WorkerHandle，再决定何时连接和派发任务。

### Windows/WSL 可行性

- Windows：`subprocess.Popen(..., env=child_env)`；由 `cmd /c start` 子进程继承；
- WSL/tmux：使用 tmux environment 或 `env KEY=value <claude...>`；
- Hook/registrar 继承 CC 进程环境，写入 start event；
- 若实际 CC/hook 会过滤环境变量，builder 必须用 live probe 验证并提出替代握手文件方案。

### 插入点

- `spawn.py`；
- `scripts/registrar.js`；
- `kernel.py::_handle_start`；
- `kernel_api.py`：`find_session_by_spawn_token`；
- `user_functions.py::create_collaborator`；
- `mcp_server.py` 与 SKILL。

### 风险与缓解

- **风险：token 未传到 hook。** live gate；替代方案为 spawn 前写 `pending_spawn/<token>.json`，新 Worker 首次调用工具时认领。
- **风险：token 被复用。** registry 对 token 唯一；已完成 token 的 retry 返回原 WorkerHandle。
- **风险：暴露 token 成为权限凭据。** token 只作关联 id，不当作长期授权密钥。

### Acceptance criteria

- 同一 cwd 并发创建至少 5 个 Worker，每次返回不同且正确的 session_id；
- 同一 spawn_token retry 只产生一个 Worker；
- Windows 与 WSL 均验证 token 能出现在 SessionStart registry；
- caller 不需要解析自然语言 connect result 才能知道新 Worker 身份；
- 旧 `create_collaborator` 若保留，明确标记 legacy wrapper。

---

## HP-05：connection_id 与握手消息关联

**状态：PROPOSED**  
**优先级：P1 / Wave 2**  
**实现难度：M–L**  
**依赖：HP-01、HP-04**

### 必要性

当前 connect 发送 plain-text hello，再把目标发来的第一条“时间晚于 hello”的消息视为 reply。重连、残留消息或并发 connect 可能产生错配；timestamp 过滤也继承 HP-01 要消除的时钟问题。

### 结构提议

每次 connect 生成 `connection_id`，hello 使用通用控制消息：

```json
{
  "kind": "control.connect.request",
  "message_id": "...",
  "correlation_id": "connection-id",
  "payload": {"protocol_version": 1}
}
```

回复必须满足：

```text
kind == control.connect.accept
AND correlation_id == connection_id
AND from/to 与目标一致
```

建议增加 `reply_message(in_reply_to, text)` 或 `accept_connection(connection_id, text)`，避免要求模型手工复制复杂 envelope。

### 连接状态

conversation metadata 至少记录：

```text
PENDING(connection_id)
ESTABLISHED(connection_id)
CLOSING
CLOSED
```

不要把“conversation 目录存在”直接等同于连接已建立。

### 插入点与结构可行性

- `conversations.py`：`info.json` 或等价 connection metadata；
- `kernel_api.py`：按 connection_id 注册、接受、关闭；
- `user_functions.py::_poll_reply/connect`：从 timestamp 条件切换为 correlation 条件；
- `mcp_server.py`：`accept_connection`/`reply_message` 的 agent-facing helper；
- `SKILL.md`：明确控制消息的唯一合法回复方式。

现有 conversation 目录已经预留 `info.json` 概念，且 `_poll_reply` 集中承担握手领取，因此插入点清晰。难点不在文件路由，而在兼容旧 Worker 的无 correlation 回复；这应被当作显式 migration 问题，而不是继续依赖 timestamp 猜测。

### 风险与缓解

- **风险：旧 Worker 只会发普通 send_message。** legacy fallback 可在一个版本内存在，但必须只在单一 pending connection 且无歧义时启用。
- **风险：控制消息语义看似进入上层。** connect/close 属于 transport lifecycle，可以保留；Plan/Probe 状态仍禁止进入。
- **风险：同时对同一 pair 多次 connect。**明确选择拒绝、合并或允许多 connection_id；builder 必须在 review 中锁定策略。推荐同一 pair 同时只允许一个 active connection，retry 返回当前状态。

### Acceptance criteria

- stale close notice、普通业务消息均不能被当作 connect accept；
- 两次 connect/reconnect 不会消费对方的 reply；
- connect timeout 后状态可清理或安全重试；
- connection_id 出现在日志与诊断信息中；
- 不再依赖 `hello_ts` 判断握手归属。

---

## HP-06：集中输入验证、路径约束与 destructive target 校验

**状态：PROPOSED**  
**优先级：P0 / Wave 1**  
**实现难度：M**  
**依赖：HP-00**

### 必要性

session id 被用于 conversation 目录和消息文件名；部分低层 MCP tool 接受调用方任意提供的 `fromid/toid`。`withdraw(init_connect=1)` 会对计算出的目录执行递归删除。即使当前假设是单用户可信环境，也不应让模型生成的字符串直接参与路径和删除目标。

### 结构提议

建立唯一验证模块，例如 `server/validation.py`：

- `validate_session_id`：长度上限；仅允许 `[A-Za-z0-9-]` 或与真实 CC UUID 格式兼容的白名单；拒绝 `__`、斜杠、反斜杠、`.`/`..`、控制字符；
- `validate_message_id/operation_id/spawn_token`：UUID 或受限 token；
- `validate_message_size`：内联 payload 大小上限可配置；
- `validate_cwd`：绝对路径、存在、目录；不得用作任何隐式删除根；
- `validate_machine_entry`：type、id、data_dir、wake command 字段和路径类型；
- `resolve_under(root, parts...)`：最终 resolved path 必须仍位于指定 root 下。

所有 destructive 操作在执行前再次验证最终绝对路径：

```text
target is descendant of CONVERSATIONS_DIR
AND target != CONVERSATIONS_DIR
AND target matches canonical pair name
```

### API 设计

验证失败返回结构化 `INVALID_ARGUMENT`，不能悄悄 sanitize 成另一个合法 id；否则两个非法 id 可能映射到同一路径。

### 插入点与结构可行性

- 新增 `server/validation.py`，作为唯一 validator/resolve helper；
- MCP tool 入口先验证，kernel dispatch 再做一次 trust-boundary 验证；
- `conversations.conv_dir/pipe_filename` 只接受已验证的 typed value 或在内部强制验证；
- `withdraw`、GC、migration 等 destructive path 在操作点做最终 containment check。

这是局部、可先行的改动，不依赖新消息协议。双层验证会有少量重复，但能防止未来内部调用绕开 MCP 入口。

### 风险与缓解

- **风险：历史 synthetic test id 不符合严格 UUID。** 允许受限 slug，但禁止路径/分隔符；测试 id 不必冒充 UUID。
- **风险：合法 cwd 包含中文和空格。** 对 cwd 不使用字符白名单，只做绝对路径、存在性和目标范围校验。
- **风险：已登记 machine data_dir 是 UNC/WSL 特殊路径。** 使用平台感知路径验证；不得用简单字符串前缀替代 resolved containment。

### Acceptance criteria

- `../`、绝对路径注入、`__` 注入和控制字符全部被拒绝；
- fuzz 输入不能使任何写入/删除逃出测试 data root；
- destructive 测试在临时 workspace 内运行并确认根目录未被删除；
- 中文/空格 cwd、UNC 与 `/mnt/<drive>` 合法场景仍工作；
- 所有 MCP 入口和 remote RPC dispatch 均经过同一验证层。

---

## HP-07：结构化 Result/Error 与兼容适配层

**状态：PROPOSED**  
**优先级：P1 / Wave 2**  
**实现难度：M**  
**依赖：HP-00；建议与 HP-01/HP-04 一起设计 schema**

### 必要性

当前大量调用通过字符串包含关系判断：`"failed" in result`、从 `"message_sent at <ts>"` 解析时间。这使调用方无法可靠区分 retryable、invalid input、peer unreachable、timeout 和内部错误，也阻碍上层确定性调度。

### 结构提议

统一返回：

```json
{
  "ok": false,
  "code": "PEER_UNREACHABLE",
  "message": "human-readable summary",
  "retryable": true,
  "data": {},
  "operation_id": "..."
}
```

建议 code 至少覆盖：

```text
OK
INVALID_ARGUMENT
NOT_FOUND
NOT_ALIVE
NOT_CONNECTED
TIMEOUT
PEER_UNREACHABLE
ALREADY_EXISTS
ALREADY_DONE
CONFLICT
RESOURCE_EXHAUSTED
PROTOCOL_MISMATCH
INTERNAL_ERROR
```

Python 内部可使用轻量 dataclass/TypedDict，不必为此新增重量依赖。MCP 边界统一输出 JSON-compatible dict。

### 兼容策略

- 新 v2 tools 使用结构化结果；
- 旧 tools 可作为 wrapper 生成旧字符串，但内部不得再解析旧字符串来控制逻辑；
- error message 不作为程序分支条件。

### 插入点与结构可行性

- 建立 `server/result.py` 或等价 helper，统一 `ok/error` 构造；
- `kernel.py::drain_queue` 保留 transport error envelope，业务 error 作为结构化 result；
- `kernel_api.py` 与 `user_functions.py` 先内部结构化，再由 legacy wrapper 转回旧字符串；
- `mcp_server.py` 明确区分 legacy 与 versioned tool。

现有 MCP/RPC 天然传递 JSON-compatible dict，不需要改 transport 框架。工作量主要来自逐条移除字符串分支，适合按 API 分批迁移并由兼容测试保护。

### 风险与缓解

- **风险：16 个既有 MCP tool 的调用习惯改变。** 版本化工具或一个 release 的 wrapper；更新 SKILL 和 examples。
- **风险：过度设计错误层级。** code 集合先小而稳定，`data` 承载细节。

### Acceptance criteria

- 内部业务代码不再通过搜索 `"failed"` 或解析自然语言时间戳决定流程；
- timeout 与 peer unreachable 可区分；
- 所有 mutation 返回 operation_id 和结构化 data；
- legacy wrapper 有测试与明确删除条件。

---

## HP-08：kernel 生命周期与 conversation registration 解耦；安全 GC

**状态：PROPOSED**  
**优先级：P1 / Wave 3**  
**实现难度：M**  
**依赖：HP-00、HP-03**

### 必要性

当前只要 `alive_conversations` 非空，kernel 就不会因 idle 退出；而 registration 又会持久化。如果 close/unregister 因中断未完成，一个陈旧 conversation 可能让 kernel 永久驻留。事实上 kernel 已能 lazy-start 并恢复 conversation，因此 registration 不应直接成为永久进程租约。

### 结构提议

1. kernel 是否退出只由以下因素决定：
   - 当前有未处理 queue request；
   - 最近有 RPC/listen activity；
   - 有显式短期 lease；
   - 正在执行不可中断的 mutation。
2. conversation registration 作为持久逻辑状态保存，但不永久阻止 kernel idle exit。
3. listen 每次 RPC 自然刷新 activity；持续通信时 kernel 不会频繁退出。
4. 增加安全 GC，仅处理：
   - 过期 `.tmp`；
   - 已完成且超过 TTL 的 RPC response/journal；
   - 明确无 owner 的过期 pending connection；
   - 失效的 status/pid marker。
5. **绝不自动删除 unacked message 或 conversation log。**

### 可选 lease

如果 builder 认为某些长 mutation 需要跨 idle window，可实现短租约：

```text
lease_id, owner_operation_id, expires_at, renewable
```

lease 用于进程生命周期，不用于消息 ACK，不得因 lease 过期删除消息。

### 插入点与结构可行性

- `kernel.py::_should_exit/main`：移除 registration 的永久阻塞语义，加入退出前 queue double-check；
- `kernel.py`/`paths.py`：短 lease 与 GC 元数据（若采用）；
- `check_core.py`/`rpc_client.py`：确认 lazy restart 与 retry 仍成立；
- `diagnose`（HP-12）：暴露 stale/pending/GC 状态。

alive conversation 已持久化且 remote wake 已存在，所以 kernel 无需为保持逻辑连接而常驻。该 proposal 不改变 message store，只改变 daemon 生命周期，结构上可以独立回滚。

### 风险与缓解

- **风险：kernel 在 remote request 到达前退出。** `_queue_has_pending` + remote wake + request retry；增加退出前第二次 queue scan。
- **风险：listen 间隔大于 idle timeout。** listen RPC 本身是阻塞/轮询活动；为 active listener 使用短 lease 或合理 idle timeout。
- **风险：GC 误删。** 白名单目录、最小 age、dry-run 诊断、永不处理 unacked data。

### Acceptance criteria

- 存在 registered conversation 但无活动时，kernel 可正常 idle exit；
- 下一次 send/listen 可 lazy-start 并恢复 registration；
- exit 与 remote request 竞争测试不丢 request；
- 故意遗留 pending/response/tmp 后，GC 只删除满足规则的对象；
- unacked message 在任意 GC 测试中保持不变。

---

## HP-09：资源上限、背压与大载荷边界

**状态：PROPOSED**  
**优先级：P1 / Wave 3**  
**实现难度：M**  
**依赖：HP-01、HP-07**

### 必要性

上层 Agent 可能返回长日志、编译输出和批量 probe 结果。当前 pipe、queue、response、operation journal 均缺少明确大小和积压策略。文件系统不能以“磁盘写成功”代替健康的背压机制。

### 结构提议

- 可配置单条内联消息上限；建议初始 256 KiB～1 MiB，由 builder 根据真实数据决定；
- 超大结果不直接塞入 message，使用通用 `artifact_refs`：路径/URI、size、sha256、media_type；
- 每 session/connection 可查询：unacked count、bytes、oldest sequence；
- 超过软阈值返回 `RESOURCE_EXHAUSTED`/`BACKPRESSURE`，不静默丢弃；
- 设全局 hard safety limit 时必须拒绝新写入，不能删除旧 unacked 消息腾空间；
- operation journal、response 和日志各自有独立 TTL/容量政策。

artifact 内容与生命周期未来由上层 Evidence Store 管理；cc-communicate 只传引用并校验基本 schema，不承担领域解释。

### 插入点与结构可行性

- `validation.py`：payload/artifact ref 限额与 schema；
- `kernel_api.py::send_message`：发布前计算 size、检查积压；
- `conversations.py`：统计 per-session/per-conversation unacked count/bytes；
- `result.py`：`RESOURCE_EXHAUSTED/BACKPRESSURE`；
- `diagnose`：暴露阈值和积压。

文件式 mailbox 已能通过目录扫描得到数量和大小，第一版不需要独立 broker。为避免每次 send 全目录扫描，builder 可在 kernel 内维护可重建计数缓存；磁盘文件仍是事实源。

### 风险与缓解

- **风险：固定阈值不适合所有任务。** 配置化，诊断工具展示当前阈值。
- **风险：artifact path 泄露或越界。** 默认只允许受控 artifact root 或明确 URI；结合 HP-06。
- **风险：背压导致死锁。** control messages 预留小额度；诊断能指出哪个 consumer 未 ACK。

### Acceptance criteria

- 超限 payload 返回结构化错误且不产生部分文件；
- 积压达到软阈值时可观测，不丢旧消息；
- artifact ref 包含 hash/size，篡改可被检测；
- GC 与背压策略不触碰 unacked message；
- 压力测试记录最大积压、恢复时间和磁盘增长。

---

## HP-10：spawn 权限策略、身份边界与 threat model

**状态：PROPOSED**  
**优先级：P1 / Wave 3**  
**实现难度：L**  
**依赖：HP-04、HP-06、HP-07**

### 必要性

当前 spawned/resumed CC 默认使用 `--dangerously-skip-permissions`，且多个底层 tool 接受调用者自报的 from/session id。它适合受控实验，但不能未经声明地成为上层自主 Agent 的默认安全模型。

### 结构提议

#### A. 明确 threat model

README/设计文档必须明确当前支持等级，例如：

```text
trusted single-user, trusted registered peer realm,
not safe against a malicious local process with data-dir access
```

#### B. spawn permission policy

新增显式参数或配置：

```text
permission_mode = standard | bypass
```

- 新低层 spawn API 推荐默认 `standard`；
- 自动化测试或明确受控 Worker 可指定 `bypass`；
- legacy `create_collaborator` 若继续默认 bypass，必须在返回和日志中标记。

不要把具体 Claude CLI flag 散落在多个函数里；集中由 spawn policy 转换。

#### C. 调用者身份

- agent-facing 高层 tool 优先使用 MCP server 从进程树解析出的 self session id；
- 接受任意 `fromid` 的 raw/admin tool 与普通 tool 区分；
- remote request 至少记录 `source_machine_id` 和 operation_id；
- 不把 spawn_token/message_id 当作授权密钥。

完整密码学认证可在 threat model 扩大到跨主机/不可信进程时另立 proposal；本轮至少不得伪装成已认证系统。

### 插入点与结构可行性

- `spawn.py`：集中 permission policy → CLI argv；
- `mcp_server.py/user_functions.py`：区分 self-bound high-level API 与 raw/admin API；
- `rpc_client.py`：附带 source_machine_id/operation_id；
- plugin config、SKILL 和 README：声明默认 policy 与 threat model。

spawn flag 当前集中在两个函数，改为 policy builder 的结构成本有限。调用者身份绑定会触及 API 兼容，适合先新增安全 high-level tool、保留明确标记的 raw tool，而不是一次删除所有旧入口。

### 风险与缓解

- **风险：standard 模式重新出现 trust dialog，破坏全自动 spawn。** policy 显式选择；上层受控实验可选择 bypass，而不是全局硬编码。
- **风险：改变现有 API。** 新 API 安全默认，legacy wrapper 保持行为并告警。
- **风险：本地 data dir 仍可被其他同用户进程修改。** 在 threat model 中承认；HP-06/HP-11 提供检测与审计，不虚假承诺安全。

### Acceptance criteria

- spawn 调用和 WorkerHandle 可看见实际 permission_mode；
- builder 测试 standard/bypass 两种命令构造；
- 普通 agent-facing send/close 默认绑定 self identity，或 builder 说明为何暂时无法做到；
- raw/admin API 清晰标记，不在 SKILL quick start 中鼓励使用；
- 文档存在明确 threat model 与未覆盖攻击面。

---

## HP-11：稳定 data root、状态 schema version 与无损迁移

**状态：PROPOSED**  
**优先级：P1 / Wave 3**  
**实现难度：M–L**  
**依赖：HP-00；与 HP-01/HP-02 的 schema 一起设计**

### 必要性

当前 data 位于插件根目录下。插件更新、重装、缓存路径改变或 win/wsl 目录复制可能使 session registry、ACK、machine identity 和 conversation history 丢失或携带错误机器身份。上层将依赖恢复和审计，运行状态不能与可替换的插件代码目录绑定。

### 结构提议

第一步先支持：

```text
CC_COMMUNICATE_DATA_DIR=<absolute path>
```

解析优先级：

```text
explicit env/config
→ platform user-state default（未来切换）
→ legacy PLUGIN_ROOT/data（兼容期）
```

建议平台默认候选：

- Windows：`%LOCALAPPDATA%/cc-communicate/`；
- Linux/WSL：`$XDG_STATE_HOME/cc-communicate/` 或 `~/.local/state/cc-communicate/`。

但是否立即切换默认位置必须由 cc-builder 在 review 中评估跨 realm handshake、已安装插件和现有数据迁移风险。推荐先增加 override 和 migration tooling，再在后续版本切默认。

每个持久文件带 `schema_version`；kernel 启动顺序：

```text
detect schema
→ validate
→ backup
→ migrate atomically
→ start serving
```

迁移只复制/转换，不自动删除 legacy data。machine identity 与 data root 绑定时，要检测 realm/type 不一致并要求显式 regenerate。

### 插入点与结构可行性

- `paths.py` 与 `scripts/lib/paths.js`：相同 data root 解析规则；
- `machine_identity.py`：data root/realm 绑定校验；
- 新 `migration.py` 或一次性 CLI：schema 检测、backup、copy/convert；
- handshake scripts：重新发布 peer perspective 的 data_dir；
- `check_core.py`/hooks：统一使用解析后的 root。

路径解析已经集中在 Python/JS 两套小模块，加入 override 可低风险先行；真正高风险的是默认目录切换和跨 realm re-registration，因此 proposal 明确允许分两阶段接受。

### 风险与缓解

- **风险：host/WSL 互相记录的 data_dir 失效。** migration 后更新 machine registration 或提供 re-register 检查；诊断显示 stale peer path。
- **风险：两个插件实例指向同一 data root。** machine/store identity 校验 + 单 kernel lock；realm 不一致拒绝启动。
- **风险：迁移中断。** backup + temp + atomic replace；旧目录保留可回退。

### Acceptance criteria

- 自定义 data root 下完整运行 local tests；
- 模拟插件代码目录更换后，指定同一 data root 可恢复 session/conversation/cursor；
- schema 不支持时 fail closed，并给出可操作诊断，不按空状态启动；
- 迁移中断后可从备份或旧目录恢复；
- Windows/WSL handshake 对新 data_dir 的双向路径转换重新 live 验证。

---

## HP-12：结构化可观测性与 health/diagnose

**状态：PROPOSED**  
**优先级：P2 / Wave 3–4**  
**实现难度：M**  
**依赖：HP-01、HP-03、HP-04、HP-07**

### 必要性

上层 Agent 需要区分：消息未发送、已发送未 ACK、Worker 未启动、connect 关联失败、remote kernel 未唤醒。仅靠自然语言 tool result 和散落 log 很难做可靠恢复，也难让下一轮规划根据真实失败修订。

### 结构提议

新增 append-only structured event log，默认不记录完整 message content：

```json
{
  "event": "message.published",
  "at": "...",
  "operation_id": "...",
  "message_id": "...",
  "store_id": "...",
  "sequence": 42,
  "from": "...",
  "to": "...",
  "size": 1234
}
```

建议事件：

```text
rpc.submitted / rpc.retried / rpc.completed / rpc.failed
message.published / message.redelivered / cursor.advanced
worker.spawn.requested / worker.registered / worker.resumed
connection.pending / connection.established / connection.closed
kernel.started / kernel.exited / remote.wake
gc.deleted / validation.rejected / backpressure.applied
```

新增 `diagnose_transport()` 或等价只读接口，返回：

- protocol/schema versions；
- machine/store identity 与 data root；
- kernel 状态；
- queue depth、oldest request；
- per-session unacked count/bytes/cursors；
- pending spawn/connection；
- stale peer registration；
- 最近错误的摘要。

### 插入点与结构可行性

- 新增 `server/telemetry.py`，统一追加结构化事件并负责轮转；
- operation/message/spawn/connection 的创建点发出带稳定 id 的事件；
- `kernel_api.py` 提供只读 health snapshot；
- `mcp_server.py` 暴露 `diagnose_transport`；
- `paths.py` 定义日志位置和限额。

所有所需状态本来就在 kernel、queue 和 mailbox 文件中；diagnose 只需汇总，不需要引入外部监控系统。事件日志 append-only，符合当前项目擅长的文件协议模式。

### 风险与缓解

- 日志可能泄露 prompt/message；默认只记录 metadata，content opt-in；日志有轮转/大小上限。
- diagnose 不得修改状态或顺手 GC。

### Acceptance criteria

- 任一失败可通过 operation/message/spawn/connection id 在结构化日志中串联；
- diagnose 能只读报告积压、cursor、pending operation 与 data root；
- 日志默认不含完整 message payload；
- telemetry 写入失败不会使 send/listen 主流程崩溃，但会产生可见降级信号；
- 日志轮转不删除 mailbox 或 operation state。

---

## HP-13：源码单一事实源与跨 realm parity gate

**状态：PROPOSED**  
**优先级：P2 / Wave 4**  
**实现难度：M–L**  
**依赖：HP-00；建议在 Wave 1–3 协议稳定后迁移**

### 必要性

当前 `v2_win/` 与 `v2_wsl/` 保存两份几乎相同的实现，只有 `.mcp.json` 等平台入口应不同。协议加固会同时修改多个核心模块，手工双写容易产生隐蔽分叉。

### 结构选项

- **A（推荐长期）**：建立 canonical `src/cc_communicate/`，release script 生成 win/wsl plugin 目录；
- **B（推荐短期）**：暂时保留双目录，但 CI/hash test 强制除允许名单外 byte-identical；
- **C**：一个 plugin tree，平台 launcher 决定 Python/interpreter。采用前需确认 Claude plugin MCP command 的跨平台行为。

不建议在 Wave 1 同时进行大规模目录重构。短期先上 B，协议稳定后再评估 A/C。

### 插入点与结构可行性

- Gate 0 先增加 parity test 和允许差异清单；
- 若采用 A：新增 canonical source、materialize/release script，并让生成目录可重复；
- 若采用 C：新增跨平台 launcher，`.mcp.json` 只调用 launcher；
- marketplace/plugin manifest 由同一版本源生成。

当前静态检查显示两套 server 源码本就保持等价，说明建立 hash/parity gate 的短期成本很低。生成式单一事实源的风险主要是 plugin packaging/path，适合最后迁移。

### 风险与缓解

- **风险：目录重构破坏 `${CLAUDE_PLUGIN_ROOT}` 与安装流程。** 先生成与现有布局完全相同的 artifact，再改变内部源码位置。
- **风险：generated files 被手工修改。** CI 检查 clean regeneration；文件头标记 generated。
- **风险：与协议修复混在一起难以定位回归。** 单独 Wave、单独 commit、完整 live regression。

### Acceptance criteria

- CI 会在 win/wsl 非允许文件出现差异时失败；
- builder 报告说明最终选择 A/B/C 及迁移计划；
- 从 clean checkout 可一条命令生成/验证两个 plugin artifact；
- 生成前后 Windows/WSL 安装入口和 live behavior 不变。

---

## 3. Proposal 依赖图

```text
HP-00 Baseline tests
  ├── HP-01 Message record + sequence
  │     ├── HP-02 Per-store cursor
  │     ├── HP-05 Correlated handshake
  │     └── HP-09 Backpressure
  ├── HP-03 RPC idempotency
  │     ├── HP-04 Spawn token / WorkerHandle
  │     └── HP-08 Kernel lifecycle / GC
  └── HP-06 Validation / path safety

HP-01 + HP-04 + HP-06
  └── HP-07 Structured result/error
        ├── HP-10 Permission / identity policy
        └── HP-12 Observability

HP-01 + HP-02 + HP-00
  └── HP-11 Stable data root / migration

HP-00 + stable Wave 1–3 protocol
  └── HP-13 Single source / parity
```

注：HP-07 实际可与 HP-01/HP-04 同时设计；图表达的是“验收时必须兼容”的依赖，不要求机械串行开发。

---

## 4. 进入上层 Agent Runtime 前的总体验收门

只有以下 Gate 都通过，cc-communicate 才应被视为上层 Runtime 的稳定依赖：

### G1：消息不会静默消失

- 同毫秒 burst 不覆盖；
- 时钟回拨不影响顺序/ACK；
- cross-realm 每 store 独立 cursor；
- partial write 不可见；
- cancel/restart 后未确认消息重投。

### G2：重复可检测，副作用可幂等

- 每条消息有 message_id；
- RPC retry 有稳定 operation_id；
- send/spawn/evoke retry 不产生不可识别的重复副作用；
- withdraw 按明确 message_id，而不是非幂等的“最新一条”。

### G3：并行 Worker 可正确关联

- 同 cwd 并发 spawn 不串 session；
- WorkerHandle 结构化返回；
- connect reply 通过 connection/correlation id 匹配。

### G4：状态可恢复、故障可解释

- kernel restart 恢复 session/conversation/cursor；
- data root 与 schema 版本明确；
- diagnose 可解释 queue、unacked、pending spawn/connect；
- legacy 与新协议迁移路径明确。

### G5：安全边界诚实且可配置

- 所有 path/id 输入受验证；
- destructive action 有 resolved target 校验；
- permission bypass 是显式 policy；
- threat model 明确，不将“同用户可写文件夹”描述成已认证通道。

### G6：测试与双 realm 一致性

- 自动化 unit/integration suite 全通过；
- Windows live gate 通过；
- WSL 与跨 realm live gate 通过；
- win/wsl 非平台文件 parity gate 通过。

---

## 5. cc-builder 最终《cc-communicate 加固报告》必含内容

完成搭建后，请 cc-builder 创建独立报告，至少包含：

```markdown
# cc-communicate Hardening Report

## 1. Executive summary
## 2. Proposal decision table
| Proposal | Decision | Implemented revision | Verification | Residual risk |

## 3. Architecture after hardening
## 4. Protocol schemas
## 5. Persistence and migration
## 6. Compatibility behavior
## 7. Tests executed and raw result summary
## 8. Windows/WSL parity and live gates
## 9. Rejected/deferred proposals with rationale
## 10. Known residual risks
## 11. API/tool reference after hardening
## 12. Exact handoff contract for the future upper Runtime
```

报告中必须明确：

- 哪些 proposal 原样接受；
- 哪些经过 revise 后实现，以及 revised design；
- 哪些被拒绝/延期及原因；
- 实际代码与本文件的差异；
- 尚未通过的 live gate；
- 当前传输究竟保证什么、不保证什么。

只有测试输出或可重复操作能够把 `IMPLEMENTED` 推进到 `VERIFIED`；代码存在本身不算验证。

---

## 6. 给 cc-builder 的首轮审核请求

首轮请只做 review，不直接大规模实现。建议输出：

1. HP-00～HP-13 的逐条 Decision；
2. 对 HP-01/HP-02 的替代协议设计（若不同意 sequence + per-store cursor）；
3. 对 HP-03 crash-window 幂等边界的诚实说明；
4. 验证 spawn_token 是否能从 child env 传入 SessionStart hook 的最小 live probe；
5. 建议的兼容版本策略；
6. Wave 1 的精确文件修改清单与测试清单；
7. 需要上层规划者决定的争议点。

在上层规划者返回 ACCEPT/REVISE 之前，不应默认全部 proposal 已获批准。
