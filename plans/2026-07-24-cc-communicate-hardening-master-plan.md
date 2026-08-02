# cc-communicate 加固总体规划（Master Plan）

> 日期：2026-07-24
> 作者：cc-builder（项目负责者，自主决策）
> 依据：`2026-07-24-cc-communicate-hardening-proposals.md`（14 条）+ `2026-07-24-cc-communicate-hardening-review.md`（逐条审核 + 代码核实）
> 性质：自主决策的加固总纲。上层 builder 不参与下层技术决策；本文件锁定全部决策点，给出路线图与对上层 Runtime 的交付契约。
> 执行：按增量拆分为独立实现计划（首个为 Gate 0），每个增量可独立交付、独立验证、独立回滚。

---

## 1. 决策锁定（Autonomous Decisions）

审核时上交上层的 5 个决策点 + 我补全的关键技术决策，全部锁定如下。每条给理由（技术细节 / 风险 / 难度权衡）。

### D1 — HP-02 listen API 形态：**Option A（版本化工具）**
新增 `listen_v2(session_id, cursors, timeout)` 与 `query_my_cursors(session_id)`；旧 `listen` 保留一个 release 作 legacy wrapper。
- **理由**：上层 Runtime 是 greenfield，本就按新 API 编写，给无歧义的版本化工具优于「少一个工具名」。Option B（单工具按入参切模式）有 footgun：一次对话中某次忘传 cursors 即**静默降级回 timestamp 模式**，PB-3 跨时钟误归档立刻回归且无报错。Option C（破坏式升级）在调用基数尚小也无必要。
- **风险/难度**：中。新增 2 工具 + 1 个 release 的 wrapper；SKILL 需写明「一旦用 cursors 不得回退」。

### D2 — HP-11 data root：**本周期只做 override + 迁移工具；默认目录切换推迟**
实现 `CC_COMMUNICATE_DATA_DIR` override（**`paths.py` 与 `scripts/lib/paths.js` 双边同步**）+ schema_version 约定 + 迁移工具。**默认仍 `PLUGIN_ROOT/data`**；切换到平台 user-state 默认目录推迟到 override 经实战验证、且跨 realm handshake 重新注册路径被 live 验证之后。
- **理由**：默认切换是全部改动里**爆炸半径最大**的（使已装插件失效 + peer 记录的 `data_dir` 作废）。把协议加固与数据迁移绑在一起违反「不在一个未验证大提交里同时重写多件事」。先给逃生口（override），默认切换单独版本。
- **风险/难度**：override 低；默认切换高（故推迟）。

### D3 — HP-13 源码统一：**立即上 B（parity gate）；A/C 推迟到协议稳定后**
parity gate（hash 比对 win/wsl，allow-list 最小化到仅 `.mcp.json` 与平台 launcher）**提前到 Gate 0**；canonical 单源（A）/ 单树 launcher（C）推迟到 Wave 3 协议稳定后，届时倾向 A（显式、可测；C 依赖未证实的 Claude plugin MCP 跨平台行为）。
- **理由**：parity gate 廉价（两树当前本就等价）却能**立即**防止协议 churn 期间的静默分叉——这正是提案把 HP-13 放 Wave 4 所忽略的近期风险。我把「gate」与「迁移」拆开：gate 提前，迁移推迟。manifest 当前 win/wsl 内容一致，故一并纳入比对。
- **风险/难度**：gate 低；迁移高（故推迟）。

### D4 — HP-10 spawn 权限默认：**新 API 默认 `standard`，显式 opt-in `bypass`；legacy 保留 bypass 并标记**
新 `spawn_collaborator`/`spawn_cc_*` 默认 `permission_mode="standard"`；无人值守自动化须显式传 `"bypass"`。legacy `create_collaborator` 维持 bypass 但在返回与日志标记。threat model 写进 README：「trusted single-user / trusted registered peer realm / **not safe against a malicious local process with data-dir access**」。
- **理由**：安全默认值应诚实。当前无条件 `--dangerously-skip-permissions` 适合受控实验，但不能未经声明就成为上层自主 Agent 的默认。secure-by-default + 显式 opt-in，把「要不要全自主」的选择权显式留给上层。
- **风险/难度**：中。standard 会重现 trust dialog（破坏全自动），故必须保留显式 bypass 逃生口；这正是默认 standard 的代价，可接受。

### D5 — HP-09 内联载荷上限：**默认 1 MiB，可配置 `CC_COMMUNICATE_MAX_INLINE_BYTES`**
超限走 `artifact_refs`（path/URI + size + sha256 + media_type）。
- **理由**：真实消息多为文本，1 MiB 足够宽松；编译输出/长日志本应走 artifact_refs。1 MiB 既包容正常大消息、又能界定滥用。配置化以适配上层真实数据。
- **风险/难度**：低（纯策略 + schema 校验）。

### D6 — HP-01 record 模块：**新增 `server/message_record.py`**
信封 schema + 原子发布（tmp + flush + **fsync** + `os.replace`）独立成小模块；`conversations.py` 只管目录/文件名。持久计数器 `message_sequence.json` 原子写**补 fsync**（现有 `_atomic_write_json` 无 fsync）。
- **理由**：record schema 与原子发布是内聚单元，独立文件避免 `conversations.py` 继续膨胀；fsync 是崩溃持久性的必要补强（现有 helper 缺）。
- **风险/难度**：中。跨 realm rename/fsync 语义须 live 验证（见风险 R2）。

### D7 — 错误码枚举：**提前到 Wave 1**（随 HP-06）
新增 `server/result.py`，Wave 1 先落地 code 枚举 + 最小 ok/error 构造（供 HP-06 返回 `INVALID_ARGUMENT`）；完整信封 + 结构化 `data` 在 Wave 2（HP-07）补齐。
- **理由**：HP-06（Wave 1）需要结构化错误，HP-07（Wave 2）才给信封；不提前会返工。枚举很小，提前零风险。
- **风险/难度**：低。

### D8 — HP-04 spawn_token：**env 注入 + 先 live probe + plan B 兜底**
`_detached_popen(env={**os.environ, "CC_COMMUNICATE_SPAWN_TOKEN": token})`（Windows）；tmux 侧 `env VAR=x <claude>` 或 `tmux set-environment`（WSL）。`registrar.js` start 事件加 `spawn_token` 字段。**先做最小 live probe 验证 env→hook→SessionStart 链路**；失败则落 plan B（`pending_spawn/<token>.json`，Worker 首次调工具认领）。
- **理由**：核实显示 hook 继承 CC 环境、`cmd /c start` 默认继承环境，链路很可能可行，但 `start`/tmux 增加间接层，必须实测。plan B 保证不阻塞。
- **风险/难度**：中。probe 廉价；plan B 为已知备选。

### D9 — HP-05 连接：**同一 pair 单 active connection；新建 `info.json`**
拒绝同 pair 多 connection_id 并存；retry 返回当前状态。connection metadata 落**新建** `info.json`（审核已纠正提案「已预留 info.json」的失实）。legacy fallback（旧 Worker 普通 send_message）限一个版本、仅单一 pending 无歧义时启用，并接 telemetry 判定删除时机。
- **理由**：单 active 大幅简化上层心智；多 connection 徒增复杂度且无真实需求。
- **风险/难度**：中。

### D10 — HP-08 kernel 生命周期：**exit 只看 queue/activity/lease/mutation，registration 不阻退；retry+wake 兜底**
安全 GC 白名单，**永不删 unacked message / conversation log**。
- **理由**：kernel 已能 lazy-start + 恢复（`_load_sessions/_load_alive_convs/_load_ack_timestamps` + session_ctrl replay），registration 不应成永久进程租约。exit-vs-remote-request 竞态由 client retry + `_wake_remote` 兜底（二次 queue scan 只是优化）。
- **风险/难度**：中。竞态良性，需竞争测试坐实。

---

## 2. 加固后目标架构

```
┌─────────────────────────── 上层 Agent Runtime（不属于本项目） ───────────────────────────┐
│  PlanGraph / Probe / Evidence Store / 重规划 —— 通过下方「交付契约」消费传输能力          │
└──────────────────────────────────────────────────────────────────────────────────────┘
                 │ 结构化 Result/Error（HP-07）· WorkerHandle（HP-04）· listen_v2/cursors（HP-02）
                 ▼
┌─ cc-communicate（本项目，加固后）───────────────────────────────────────────────┐
│ MCP 层（mcp_server.py）  16 → ~20 工具：+listen_v2/query_my_cursors/spawn_collaborator/diagnose_transport │
│   └ 入口校验（HP-06 validation.py）· self-identity 绑定（HP-10）· 结构化结果（HP-07 result.py） │
│ 传输 API（user_functions.py）  connect/close/listen/send —— 传输级生命周期，无上层语义        │
│ 内核（kernel.py 单线程 daemon）  分发 + operation journal（HP-03）+ sequence 分配（HP-01）      │
│   └ lazy-start（check_core）· 生命周期解耦 + 安全 GC（HP-08）· telemetry（HP-12）              │
│ kernel_api.py  mutation 幂等（HP-03）· per-store cursor（HP-02）· connection_id（HP-05）        │
│ 存储层                                                                                  │
│   message_record.py  版本化信封 + 原子发布（HP-01）                                       │
│   conversations.py     目录/文件名 + connection info.json（HP-05）· 积压统计（HP-09）          │
│   validation.py        唯一校验/路径约束（HP-06）                                         │
│ 持久态（全带 schema_version，HP-11）  sessions / alive_convs / cursors / sequence / journal / machine_identity │
│ 数据根  CC_COMMUNICATE_DATA_DIR override（paths.py + paths.js 双边，D2）                     │
│ 可观测  telemetry.py 结构化事件 + diagnose_transport（HP-12）                                │
└───────────────────────────────────────────────────────────────────────────────────────────┘
                 │ registrar.js（SessionStart/End hook，写 data/session_ctrl，带 spawn_token）
                 ▼
        Windows host  ⇄  WSL2（跨 realm，per-store cursor 路由）
```

**交付语义（对上层承诺，诚实）**：`at-least-once delivery + per-store ordering + detectable duplicates + idempotent mutation`。**不承诺**崩溃条件下严格 exactly-once。

**边界（不进入本项目）**：PlanGraph/Probe/Evidence 语义、风险评分、git checkpoint/rollback、上层任务提交/回退/重规划。`kind`/`correlation_id`/`payload`/`artifact_refs` 保持传输通用。

---

## 3. 波次路线图（自主排序 + effort/risk/dependency）

> 我对提案波次的两处自主调整：**HP-13-B parity gate 提前到 Gate 0**（D3）；**HP-11 默认切换移出本周期**（D2）。

### Gate 0 — 基线与护栏（无协议变更，零行为风险）
| 项 | 内容 | effort | risk | 依赖 |
|---|---|---|---|---|
| HP-00 | 自动化测试基线 + `CC_COMMUNICATE_DATA_DIR` 隔离（paths.py+paths.js） | M | 低 | 无 |
| HP-13-B | parity gate（win/wsl hash 比对，allow-list 最小化） | S | 低 | HP-00 测试框架 |
| HP-11(部分) | schema_version 约定 + 迁移工具骨架 | S | 低 | HP-00 |

**交付物**：`pytest` 单命令可跑的非 live 测试套件（独立临时 data root）+ parity gate。**验收**：可重复证明「取消不丢消息」「kernel restart 可恢复」。

### Wave 1 — 正确性核心（消静默丢失 / 错误 ACK / 重复副作用 / 路径风险）
| 序 | 项 | 内容 | effort | risk | 依赖 |
|---|---|---|---|---|---|
| 1 | HP-06 | 集中校验 + 路径约束 + destructive target 校验（+ result.py 错误码枚举 D7） | M | 低 | Gate 0 |
| 2 | HP-01 | 版本化 message record + message_id + per-store sequence + 原子发布 | L | **高** | HP-00 |
| 3 | HP-02 | per-store cursor（listen_v2 + query_my_cursors，D1） | L | 高 | HP-01 |
| 4 | HP-03 | RPC operation_id + 领域幂等 + 安全重试（journal） | L | 中 | HP-00；send 依赖 HP-01 |

**自主排序理由**：HP-06 独立、低险、高价值，先做为大改前的「热身 + 护栏」；HP-01 是基石（sequence 是 HP-02 的 ACK 单位、message_id 是 HP-03 send 幂等键）；HP-02 依赖 HP-01；HP-03 的 send 去重依赖 HP-01 的 message_id（spawn/evoke 去重待 Wave 2 的 HP-04）。
**交付物**：同毫秒 burst 不覆盖、时钟回拨不乱序、跨 realm 每 store 独立 cursor、partial write 不可见、cancel/restart 重投；retry 不重复 send；非法 id/路径全拒。
**Wave 1 后跑完整回归再进 Wave 2。**

### Wave 2 — 并行 Worker + 结构化调用
| 序 | 项 | 内容 | effort | risk | 依赖 |
|---|---|---|---|---|---|
| 1 | HP-07 | 结构化 Result/Error 信封 + legacy wrapper（枚举已在 Wave 1） | M | 中 | Wave 1 |
| 2 | HP-04 | spawn_token + WorkerHandle（**先 live probe**，D8） | L | 中 | HP-03 |
| 3 | HP-05 | connection_id + 关联握手（新建 info.json，D9） | M–L | 中 | HP-01、HP-04 |

**交付物**：同 cwd 并发 spawn 不串 session、WorkerHandle 结构化返回、connect reply 经 correlation_id 匹配、内部不再解析字符串控流程。

### Wave 3 — 生命周期 / 资源 / 安全 / 持久化
| 项 | 内容 | effort | risk | 依赖 |
|---|---|---|---|---|
| HP-08 | kernel 生命周期解耦 + 安全 GC（D10） | M | 中 | HP-00、HP-03 |
| HP-09 | 资源上限 + 背压 + artifact_refs（D5） | M | 中 | HP-01、HP-07 |
| HP-10 | spawn 权限策略 + 身份边界 + threat model（D4） | L | 中 | HP-04、HP-06、HP-07 |
| HP-11(余) | 迁移工具 + schema 校验（默认切换**已移出**，D2） | M | 低 | HP-00 |

**交付物**：registered-but-idle 可退出、GC 不碰 unacked、超限结构化报错、threat model 入 README、自定义 data root 可恢复。

### Wave 4 — 源码统一（可再推迟）
| 项 | 内容 | effort | risk | 依赖 |
|---|---|---|---|---|
| HP-13-A | canonical 单源 + 生成 win/wsl artifact | M–L | 高 | Wave 1–3 协议稳定 |

**交付物**：clean checkout 一条命令生成/验证两个 plugin artifact，安装入口与 live 行为不变。

---

## 4. 对上层 builder 的交付契约（Handoff Contract）

加固完成后，我将向上层 Runtime 交付并明确以下（这是「告诉它加固后 cc-communicate 如何」的核心）：

### 4.1 传输保证（承诺什么）
- **at-least-once**：未确认消息允许重投；cancel/restart/crash 后未 ACK 消息会重投，不静默消失。
- **per-store ordering**：同一 conversation store 内按单调 `sequence` 有序。
- **detectable duplicates**：每条消息有稳定 `message_id`，接收方可去重。
- **idempotent mutation**：`send/spawn/evoke` 等 retry 经 `operation_id` + 领域幂等键去重，不产生不可识别重复副作用。
- **可观测（分阶段，AR-06 修订）**：H1 期间以结构化 Result/Error（`code` + `retryable` + `data`，含 `degraded_stores`/`degraded_steps` 降级标记）、`backlog_stats`、`run_gc(dry_run)`、kernel log 为替代观测面——降级/重试/残留均可查；`diagnose_transport` 统一诊断接口 + 结构化事件日志（HP-12）为分阶段延后项（G4 分阶段接受），**重启条件**：进入 H2/H3 或第一次真实无法定位的传输故障。

### 4.2 明确不保证（诚实边界）
- **不保证**崩溃/跨进程条件下严格 exactly-once（残留一个极窄 crash-window 可能重复，但重复可经 message_id 检测、绝不静默）。
- **不保证**全局跨 store 有序（不同 store 时钟/序号不可比——这正是 per-store cursor 的意义）。
- **不保证**对「同用户可写 data dir 的恶意本地进程」安全（threat model 如实声明，见 4.5）。

### 4.3 Runtime 使用契约（怎么正确使用）
1. **cursor 即传输 ACK，非任务完成**：Runtime 应先把消息写入自己的 Run/Event Store，**再**推进 cursor。`query_my_cursors` 可在 compact/重启后恢复。
2. **Worker 创建**：用 `spawn_collaborator` 拿结构化 `WorkerHandle`（session_id/machine_id/cwd/spawn_token/connection status），再决定何时 `connect`。同 token retry 返回原 handle。
3. **连接**：`connect` 经 `connection_id`/`correlation_id` 匹配，回复用 `accept_connection`/`reply_message`，不要手抄 envelope。
4. **大载荷**：> 内联上限（默认 1 MiB）走 `artifact_refs`（path/URI + size + sha256 + media_type）；artifact 生命周期归上层 Evidence Store。
5. **错误处理**：按结构化 `code`（`PEER_UNREACHABLE/TIMEOUT/NOT_ALIVE/RESOURCE_EXHAUSTED/...`）与 `retryable` 分支，**不要**解析 message 字符串。
6. **权限**：spawn 默认 `standard`；无人值守自动化显式 `permission_mode="bypass"` 并在自己的文档标记受控。

### 4.4 新增/变更的 API 面
- 新增：`listen_v2`、`query_my_cursors`、`spawn_collaborator`、`diagnose_transport`（+ 可能的 `ack_through`、`accept_connection`、`reply_message`）。`diagnose_transport` 随 HP-12 分阶段延后（AR-06，见 §4.1 重启条件）。
- 变更：`listen`/`close_connection` 支持 cursor；`create_collaborator` 转 legacy wrapper；多个工具返回结构化结果。
- 不变：身份/发现/存活（`my_session_id`/`query_session`/`check_alive`/`query_machines`）、跨 realm 路由语义。

### 4.5 安全与 threat model（如实交付）
- 默认 threat model：`trusted single-user, trusted registered peer realm, not safe against a malicious local process with data-dir access`。
- 所有 path/id 输入经统一校验；destructive 操作有 resolved-target containment。
- 完整密码学认证在 threat model 扩大到跨主机/不可信进程时另立；本轮不伪装成已认证。

### 4.6 残留风险（交付时如实列出）
- PB 残留：crash-window 重复（可检测）、跨 realm fsync 持久性较弱（rename 原子性兜底正确性）。
- legacy wrapper 在一个 release 后删除（删除时机经 telemetry 判定）。
- HP-11 默认 data root 切换、HP-13-A 单源迁移：本周期外，另行规划。

---

## 5. 风险登记册（自主评估 + 缓解）

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | HP-01 消息格式迁移影响旧 listener | 高 | 双 reader（旧 .md + 新 .json）一版本；writer 只写新；deprecation window；旧 ACK 不静默当 sequence |
| R2 | 跨 realm（/mnt/c DrvFs、//wsl.localhost 9P）rename/fsync 语义差异 | 高 | 正确性锚定「rename 原子性 → reader 不见 partial」；崩溃持久性由持久 counter + message_id 去重兜底；**Wave 1 live gate 实测** |
| R3 | spawn_token env 链路被 CC/hook 过滤 | 中 | 先 live probe（D8）；失败落 plan B `pending_spawn/<token>.json` |
| R4 | exit-vs-remote-request 竞态丢请求 | 中 | client retry + `_wake_remote` 兜底；退出前二次 queue scan；竞争测试 |
| R5 | HP-07 触及全部工具的回归 | 中 | legacy wrapper + 版本化工具；错误码枚举提前（D7）；分批迁移由兼容测试保护 |
| R6 | 双域手工同步产生隐蔽分叉 | 中 | parity gate 提前到 Gate 0（D3）；所有改动两域同步 |
| R7 | GC / 背压误删 unacked | 中 | 白名单 + 最小 age + dry-run + 永不碰 unacked 的硬规则 + 测试 |
| R8 | standard 权限破坏上层全自动 | 中 | 显式 bypass opt-in（D4）；文档标记 |

---

## 6. 执行方式

- **增量拆分**：每个 Wave / Gate 一个独立实现计划（writing-plans 格式，TDD，频繁提交）。首个为 **Gate 0**（见 `2026-07-24-cc-communicate-hardening-gate0-plan.md`）。
- **纪律**（沿用提案 §1.3）：不在一个未验证大提交里同时重写协议+路由+布局；新协议先有自动化故障测试再移除旧兼容路径；破坏性改动带 schema/version + 迁移说明；两域同步。
- **推进门槛**：每个 Wave 后跑完整回归（unit + integration + Windows live + WSL/cross-realm live + parity gate），达标再进下一 Wave。
- **验证驱动**：`IMPLEMENTED` → `VERIFIED` 只能靠测试输出或可重复操作，代码存在不算验证。
- **完成定义**：全部 Gate G1–G6（提案 §4）通过 + 本文件 §4 交付契约可如实陈述，才算加固完成、可对上层交付。

---

## 7. 下一步

实现 **Gate 0**（测试基线 + data root override + parity gate）——它是零行为风险的护栏，为 Wave 1 的高危协议变更提供回归安全网。Gate 0 的落地实现计划见配套文档。
