# cc-communicate 加固完成报告

> **日期**：2026-08-03
> **报告人**：cc-communicate 加固执行方（accountable owner）
> **致**：加固要求者（上层 builder）
> **依据**：`plans/2026-07-24-cc-communicate-hardening-proposals.md`（14 条提案）、`plans/2026-07-24-cc-communicate-hardening-master-plan.md`（总纲，D1-D10 决策锁定）
> **结论**：加固程序 4 个 Wave + Gate 0 全部完成并通过外部审核。13/14 条提案已实现并验证；1 条（HP-12 可观测性）经验收修订后正式标记为 **DEFERRED（分阶段接受，AR-06）**，重启条件见 §2。交付语义达到总纲 §4 交付契约的承诺，唯 `diagnose_transport` 统一诊断接口未交付（H1 期间以结构化 Result/Error + backlog_stats + run_gc(dry_run) + kernel log 为替代观测面）。

---

## 1. 加固程序总览

### 1.1 执行规模

| 指标 | 数值 |
|---|---|
| 执行周期 | 2026-07-24 ~ 2026-08-03（10 天） |
| 起点 → 终点 | v0.3.0 (`c114aa1`) → `febc803` (main, pushed to origin) |
| Commits | 89 |
| 自动化测试 | 0 → **204**（从零建立） |
| Server 模块 | 22 个 `.py` + 2 个 `.js` |
| MCP 工具 | 16 → **20**（新增 `listen_v2`、`query_my_cursors`、`spawn_collaborator`、`claim_pending_spawn`） |
| 回归 gate | T0 语法 / T1 pytest / T2 parity + artifacts → **GATE PASS** |
| Live gates | L1-L7 全部完成（L2 DEGRADED (T46)，其余 PASS） |
| 外部审核 | Wave 1-4 逐波审核，全部 PASS，无 fix-before-merge 项 |

### 1.2 波次路线图与完成状态

| 波次 | 提案 | 目标 | 测试数 | 状态 |
|---|---|---|---|---|
| Gate 0 | HP-00, HP-13-B, HP-11(部分) | 基线与护栏 | 50 | ✅ 完成+审核 |
| Wave 1 | HP-06, HP-01, HP-02, HP-03 | 正确性核心（消静默丢失/错误ACK/重复副作用/路径风险） | 117 | ✅ 完成+审核 |
| Wave 2 | HP-07, HP-04, HP-05 | 并行 Worker + 结构化调用 | 117 | ✅ 完成+审核 |
| Wave 3 | HP-08, HP-09, HP-10, HP-11(余) | 生命周期/资源/安全/持久化 | 193 | ✅ 完成+审核 |
| Wave 4 | HP-13-A | 源码统一（canonical 单源 + 生成 artifact） | 204 | ✅ 完成+审核 |

---

## 2. 逐条提案交付状态

### 已实现（13/14）

#### HP-00 — 自动化测试基线与数据隔离（Gate 0）✅
- `tests/` 目录从零建立，220 个测试覆盖 unit + integration + parity（204 基线 + AR-01~03/N-01~03 新增 16，见 §9）。
- `CC_COMMUNICATE_DATA_DIR` 环境变量 override（`paths.py` + `paths.js` 双边同步），测试使用独立临时 data root，不触碰已装插件。
- conftest 依赖序 reload 机制确保模块路径常量正确绑定。
- **验收**：`pytest` 单命令可跑；可重复证明"取消不丢消息"和"kernel restart 后可恢复"。

#### HP-01 — 版本化 message record + 原子发布（Wave 1）✅
- 新增 `server/message_record.py`：版本化信封 schema + 原子发布（tmp + flush + fsync + `os.replace`）。
- 持久计数器 `message_sequence.json` 原子写补 fsync。
- 同毫秒 burst 不覆盖（unique filename + sequence）；partial write 不可见（rename 原子性）。
- **验收**：同毫秒 burst、时钟回拨、cancel/restart 重投均有故障测试覆盖。

#### HP-02 — per-store cursor（Wave 1，D1）✅
- 新增 `listen_v2(session_id, cursors, timeout)` + `query_my_cursors(session_id)`。
- 旧 `listen` 保留为 legacy wrapper（timestamp-ACK 模式）。
- per-store cursor 独立：跨 realm 不串时钟；cursor 推进 = 传输级 ACK（非任务完成）。
- **验收**：跨 realm per-store cursor 独立性 live gate PASS（T47，真实 WSL cross-realm）。

#### HP-03 — RPC operation_id + 领域幂等（Wave 1）✅
- `operation_journal.py`：append-only journal 记录 operation_id + 领域幂等键。
- send/spawn/evoke 等 retry 经 operation_id 去重，不产生不可识别重复副作用。
- **验收**：retry 不重复 send；幂等键覆盖 send（message_id）、spawn（spawn_token）、evoke（session_id）。

#### HP-04 — spawn_token + WorkerHandle（Wave 2，D8）✅
- Plan A（env 注入）：`CC_COMMUNICATE_SPAWN_TOKEN` -> `registrar.js` Start 事件 -> kernel 绑定 token->sid。
- Plan B 兜底：`pending_spawn/<token>.json` + `claim_pending_spawn`。
- `spawn_collaborator` 返回结构化 `WorkerHandle`（session_id / machine_id / cwd / spawn_token / permission_mode）。
- 同 token retry 返回原 handle（幂等）。
- **验收**：同 cwd 并发 spawn 不串 session（L5 live gate PASS）；token->sid 绑定正确。

#### HP-05 — connection_id + 关联握手（Wave 2，D9）✅
- `connect` 携带 `connection_id`（= correlation_id）；reply 经 correlation_id 匹配。
- 同 pair 单 active connection（CONFLICT 拒绝并存）；`info.json` 记录连接状态。
- `close_connection` deactivate；同 id 可 reuse。
- legacy fallback（旧 Worker 普通 send_message）限单一 pending 无歧义时启用。
- **验收**：correlation-matched reply + single-active CONFLICT + clean close/reuse（L6 live gate PASS）。

#### HP-06 — 集中校验 + 路径约束（Wave 1）✅
- `server/validation.py`：统一校验入口（session_id / path / message / artifact_refs / permission_mode / bool）。
- destructive target 校验（resolved-path containment）。
- `server/result.py`：错误码枚举（INVALID_ARGUMENT / NOT_FOUND / PEER_UNREACHABLE / TIMEOUT / CONFLICT / RESOURCE_EXHAUSTED / NOT_ALIVE / INTERNAL）。
- 非法 id/路径全拒，入口校验在 MCP 层和 kernel 层双重执行。
- **验收**：非法输入测试矩阵全覆盖；peer cwd 校验延迟到 peer kernel（T31 fix）。

#### HP-07 — 结构化 Result/Error 信封（Wave 2）✅
- 5-field envelope：`{ok, code, message, data, retryable}`。
- kernel 返回 raw structured dict；`user_functions.py` 统一包装。
- 控制流不再解析字符串；调用方按 `code` + `retryable` 分支。
- legacy `create_collaborator` 保留为 wrapper。
- **验收**：全部 20 个 MCP 工具返回结构化 envelope。

#### HP-08 — kernel 生命周期解耦 + 安全 GC（Wave 3，D10）✅
- exit 谓词解耦：registration 不再阻塞退出；exit 只看 queue/activity/terminate。
- `server/cleanup.py`：安全 GC，白名单结构（session_ctrl ≥7d、pending_spawn >TTL、responses ≥7d）。
- **pipe/ 和 log/ 从不被枚举**（路径组件 guardrail 防御）。
- `spawn._child_env` 剥离 `CLAUDE_CODE_CHILD_SESSION`（T38 fix：修复 resumed CC transcript 保存）。
- `proc.pid_matches` 统一 PID liveness 检测（dedup Wave 1/2 重复逻辑）。
- **验收**：registered-but-idle kernel 可退出 + 竞态测试（L1 live gate PASS）；GC 不碰 unacked。

#### HP-09 — 资源上限 + 背压 + artifact_refs（Wave 3，D5）✅
- `ResourceExhaustedError`（code=RESOURCE_EXHAUSTED）：超限内联文本 -> 结构化报错 + retryable。
- `artifact_refs`：`{path|uri, size, sha256, media_type}`，两个信任边界校验，max 16。
- 背压：per-pair unacked cap（`CC_COMMUNICATE_MAX_BACKLOG`），超限返回 RESOURCE_EXHAUSTED retryable=True。
- `backlog_stats` kernel 函数：per-partner unacked 计数 + bytes（可观测性）。
- **验收**：超限+refs 拒绝、背压 cap/release、backlog_stats 测试全覆盖。

#### HP-10 — spawn 权限策略 + 身份边界 + 威胁模型（Wave 3，D4）✅
- `spawn_collaborator` / `spawn_cc_new` 默认 `permission_mode="standard"`（不加 `--dangerously-skip-permissions`）。
- `spawn_cc_resume` / `evoke` 默认 `bypass`（resume ≠ 新信任决策）。
- `evoke` 新增 `permission_mode` override 参数。
- legacy `create_collaborator` 显式 bypass + 返回标记 + kernel log。
- `WorkerHandle` 携带 `permission_mode`。
- `README.md` 威胁模型：trusted single-user / trusted registered peer realm / **NOT safe against malicious local process with data-dir access**。
- **验收**：权限策略端到端贯通测试 + L7 live smoke（D4 live-confirmed: WorkerHandle permission_mode: standard）。

#### HP-11 — schema 校验 + 迁移工具（Gate 0 部分 + Wave 3 余，D2）✅
- `server/schema.py`：`SUPPORTED_SCHEMA=1`、`schema_too_new`、`unwrap`（dual-read）、`stamp_v1`/`wrap_v1`、`validate_layout`。
- kernel loaders refuse newer schema（skip + loud log + file untouched）；dual-read wrapped v1 + legacy flat。
- writers emit versioned shapes（`{schema_version: 1, <key>: <payload>}`）。
- `tools/migrate_data.py` CLI：`--data-root <dir> [--dry-run]`，验证 layout + 迁移 v1 registries，refuse newer。
- flat registries 不能就地 stamp（version key 会被误读为条目）-> wrap-migrate 设计修正（D2-a）。
- **D2 决策**：默认 data root 切换**推迟**（override 已交付，爆炸半径最大的变更单独规划）。
- **验收**：schema guard 矩阵 + migrate dry-run/execution/idempotent 测试。

#### HP-13 — 源码统一（HP-13-B Gate 0 + HP-13-A Wave 4，D3）✅
- **HP-13-B**（Gate 0）：`tools/check_parity.py` — win/wsl hash 比对，allowlist 最小化到仅 `.mcp.json`。从 Gate 0 起防止协议 churn 期间的静默分叉。
- **HP-13-A**（Wave 4）：`v2_win/cc-communicate/` 为 canonical 单源；`tools/build_artifacts.py generate|verify` 生成/校验 `v2_wsl`。
  - `generate`：mirror + `.mcp.json` 替换 + stale 删除 + vacuous guard。
  - `verify`：temp-dir 重新生成（零 repo 写入）-> byte-compare 全部文件（含 `.mcp.json`）+ pin `v2_win/.mcp.json` == win 模板。
  - `check_parity.py` 重构：expose `collect()`/`compare()`，generator 复用 -> **文件集规则物理上不可漂移**。
  - `.gitattributes`：v2 trees + templates pin 到 `text eol=lf`（CRLF hazard 防御）。
  - editing workflow rule：编辑 `v2_win/` only -> `generate` -> commit both trees；忘记 generate -> red T2。
- **验收**：0-diff invariant（generate -> empty diff）；clean checkout 一条命令 `verify` 验证两个 artifact；L7 live smoke PASS。

### 延后（1/14）

#### HP-12 — 结构化可观测性与 diagnose_transport ⚠️ DEFERRED（分阶段接受，AR-06）
- **状态**：`DEFERRED (分阶段接受)`。验收审核（AR-06）确认：H1 期间不强制实现完整 HP-12，但契约必须如实区分"已交付"与"分阶段延后"，并定义重启条件。
- **原因**：HP-12 依赖 HP-01/03/04/07 全部落地（Wave 2 后才具备条件），但 Wave 3 已满载（HP-08/09/10/11），Wave 4 是源码统一。HP-12 的结构化事件日志 + `diagnose_transport` 统一诊断接口是独立增量，不影响协议正确性，可在加固后按需追加。
- **H1 期间替代观测面**（明确列举，AR-06）：
  - HP-07 结构化 Result/Error envelope（`code` + `retryable` + `data`）—— 每次调用即可获得结构化结果；AR-02 后传输故障不再伪装成空成功（`INTERNAL`/`PEER_UNREACHABLE` + `degraded_stores`/`degraded_steps` 标记）。
  - HP-09 `backlog_stats` kernel 函数 —— per-partner unacked 计数 + bytes。
  - HP-08 `run_gc(dry_run)` —— GC 候选列举，不删除。
  - kernel log 文件 —— 生命周期事件 + bypass spawn 记录。
- **交付契约影响**：总纲 §4.1 承诺"所有降级/重试/残留经 `diagnose_transport` + 结构化事件可查"——此承诺**未完全兑现**，已按 AR-06 改为分阶段表述（见总纲 §4.1）。结构化结果和 backlog_stats 覆盖了大部分日常诊断需求，但缺少 consolidated health snapshot 和 append-only structured event log。§4.4 列出的 `diagnose_transport` 工具未交付。
- **HP-12 重启条件（AR-06）**：进入 H2/H3（多 worker 阶段）或出现**第一次真实无法定位的传输故障**时，重启 HP-12 作为独立增量，预计 effort M。

---

## 3. 交付契约兑现状态

### 3.1 传输保证（总纲 §4.1）

| 承诺 | 状态 | 证据 |
|---|---|---|
| at-least-once delivery | ✅ 兑现 | 未 ACK 消息允许重投；cancel/restart/crash 后重投不静默消失 |
| per-store ordering | ✅ 兑现 | 同一 conversation store 内按单调 sequence 有序；跨 realm per-store cursor 独立（T47 live） |
| detectable duplicates | ✅ 兑现 | 每条消息有稳定 `message_id`；接收方可去重 |
| idempotent mutation | ✅ 兑现 | send/spawn/evoke retry 经 operation_id + 领域幂等键去重 |
| 可观测性 | ⚠️ 部分兑现（H1 分阶段） | H1 期间替代观测面：结构化 Result/Error（code+retryable+data；AR-02 后传输故障以 INTERNAL/PEER_UNREACHABLE + `degraded_stores`/`degraded_steps` 如实暴露）+ `backlog_stats` + `run_gc(dry_run)` + kernel log；**缺** `diagnose_transport` 统一接口和结构化事件日志（HP-12 DEFERRED，重启条件见 §2） |
| resume（evoke 恢复通信） | ⚠️ DEGRADED (AR-04) | 进程/session 恢复成功（resume 落在原 cwd，check_alive 1）；恢复后的通信 round-trip 2/2 失败（CC v2.1.220 MCP 客户端断连，T46，CC 侧）。**上层 H1 采用 spawn-fresh fallback**（固定新建 worker，不依赖 resume）；CC 更新后重测 L2，若 round-trip 通过则升级状态 |

### 3.2 明确不保证（总纲 §4.2）

| 边界 | 状态 |
|---|---|
| 不保证崩溃条件下严格 exactly-once | ✅ 如实声明（crash-window 重复可经 message_id 检测） |
| 不保证全局跨 store 有序 | ✅ 如实声明（per-store cursor 的意义） |
| 不保证对恶意本地进程安全 | ✅ 如实声明（README 威胁模型） |

### 3.3 Runtime 使用契约（总纲 §4.3）

| 契约 | 状态 |
|---|---|
| cursor = 传输 ACK，非任务完成 | ✅ 文档化（SKILL.md + README） |
| Worker 创建用 spawn_collaborator 拿 WorkerHandle | ✅ 交付 |
| connect 经 connection_id/correlation_id 匹配 | ✅ 交付 |
| 大载荷走 artifact_refs | ✅ 交付（> 1 MiB 默认上限，可配置） |
| 错误处理按 code + retryable 分支 | ✅ 交付（8 个 code 枚举） |
| spawn 默认 standard，bypass 显式 opt-in | ✅ 交付（D4） |

### 3.4 新增/变更 API 面（总纲 §4.4）

| API | 状态 |
|---|---|
| `listen_v2` | ✅ 新增 |
| `query_my_cursors` | ✅ 新增 |
| `spawn_collaborator` | ✅ 新增 |
| `claim_pending_spawn` | ✅ 新增（HP-04 Plan B 兜底） |
| `diagnose_transport` | ❌ 未交付（HP-12 DEFERRED，分阶段接受；H1 替代观测见 §3.1） |
| `listen` / `close_connection` 支持 cursor | ✅ 变更 |
| `create_collaborator` 转 legacy wrapper | ✅ 变更 |
| 多个工具返回结构化结果 | ✅ 变更（全部 20 工具） |
| 身份/发现/存活/跨 realm 路由 | ✅ 不变 |

**MCP 工具总数**：16 → 20（新增 4，legacy wrapper 保留 1）。

### 3.5 安全与威胁模型（总纲 §4.5）

- ✅ 默认 threat model 文档化：`trusted single-user, trusted registered peer realm, not safe against malicious local process with data-dir access`。
- ✅ 所有 path/id 输入经统一校验（HP-06）；destructive 操作有 resolved-target containment。
- ✅ spawn 默认 standard 权限（HP-10/D4）。
- ✅ 完整密码学认证在 threat model 扩大时另立（本轮不伪装已认证）。

### 3.6 残留风险（总纲 §4.6）

| 风险 | 状态 |
|---|---|
| crash-window 重复（可检测） | ✅ 如实记录（PB-1） |
| 跨 realm fsync 持久性较弱 | ✅ 如实记录（rename 原子性兜底正确性，PB-2/3） |
| legacy wrapper 删除时机 | ✅ 经 telemetry 判定（HP-12 追加后） |
| HP-11 默认 data root 切换 | ✅ 延后（D2，override 已交付） |
| HP-13-A 单源迁移 | ✅ 已完成（Wave 4） |

### 3.7 能力降级声明（AR-04）

**resume 通信能力当前为 DEGRADED**（不是 PASS + finding）：`evoke`/`spawn_cc_resume`
承诺并交付**进程/session 恢复**（`claude --resume` 落在原 cwd，check_alive → 1），
但恢复后的**通信 channel 不保证就绪**——CC v2.1.220 恢复后的 MCP 客户端可能以
disconnected 状态出现（T46，2/2 失败，CC 侧 quirk，无 cc-communicate 错误）。
按验收审核 AR-04 的处置选项 2 交付：

1. **上层 H1 不依赖 resume**：固定新建 worker（`spawn_collaborator`），恢复路径
   不可用时以 spawn-fresh 兜底；该降级已计入时间/费用模型。
2. **升级路径**：CC 更新后重跑 L2（恢复后 round-trip）；若连续通过，将 resume
   状态从 DEGRADED 升级为 PASS 并更新本报告。

---

## 4. 验证证据

### 4.1 自动化 gate

```
$ py -3 tools/run_regression.py
T0 syntax  PASS (44 .py + 2 .js parsed clean)
T1 pytest  PASS (220 passed in 16.92s)
T2 parity  PASS (PARITY OK (32 files compared, allowlist=['.mcp.json']))
T2 artifacts PASS (ARTIFACTS OK (33 files compared, templates pinned))
GATE: PASS
```

- **T0 语法**：两棵树的 44 个 `.py`（ast.parse）+ 2 个 `.js`（node --check）全部通过。
- **T1 pytest**：220 个测试全部通过（204 基线 + AR-01~03/N-01~03 新增 16 个：MCP 依赖门 3、传输故障注入 7、known_pids 4、close_connection degraded 1、gate stderr 1；unit + integration + parity + tooling）。
- **T2 parity**：v2_win ↔ v2_wsl 在 allowlist 之外字节一致（32 文件）。
- **T2 artifacts**：committed v2_wsl == generator 输出（33 文件，含 `.mcp.json` 模板 pin）。

### 4.2 0-diff 不变量

```
$ py -3 tools/build_artifacts.py generate
GENERATED v2_wsl/cc-communicate (33 files)
$ git diff --name-only v2_wsl
(empty)
```

canonical 树上的 generate 操作是 0-diff——committed artifact 已与 generator 输出字节一致。

### 4.3 Live gates（L1-L7）

| Gate | 内容 | 结果 |
|---|---|---|
| L1 | Spawn-race（每 token 恰好一个 worker） | PASS |
| L2 | Reconnect（dead -> evoke -> alive，cwd 正确；恢复后 round-trip） | DEGRADED (T46) |
| L3 | Cross-realm cursor（真实 WSL，per-store 独立） | PASS (T47) |
| L4 | Multi-collab stress（10/10 acked，5/5 reply 匹配） | PASS |
| L5 | Same-cwd spawn race（不同 token -> 不同 sid） | PASS |
| L6 | Correlated connect（correlation match + CONFLICT + reuse） | PASS |
| L7 | Wave-4 smoke（spawn/ack + WSL cross-realm probe） | PASS |

**T46 finding（AR-04 重分类为 DEGRADED）**：resumed CC 的 cc-communicate MCP 客户端断连（CC v2.1.220 resume quirk），2/2 resume delivery 失败。归因 CC 侧（cc-communicate server 进程健康，无 cc-communicate 错误，Wave 2 L2 同流程 PASS）。**契约表述**：进程/session 恢复 = 成功；恢复后的通信 round-trip = 当前版本 DEGRADED（spawn-fresh fallback，详见 §3.7）。建议 CC 更新后重测。

### 4.4 外部审核

每个 Wave 完成后由外部审核方（kimi-k3）独立审核全部代码、测试、live gate 证据和 review trail：

| Wave | 审核 | 结果 |
|---|---|---|
| Wave 1 | T28-T35 + 回归 gate | PASS，无 fix-before-merge |
| Wave 2 | HP-07/04/05 | PASS，无 fix-before-merge |
| Wave 3 | HP-08/09/10/11 | PASS，无 fix-before-merge |
| Wave 4 | HP-13-A | PASS，7 deferred minors ship-as-recorded |

---

## 5. 决策记录（D1-D10）

| # | 决策 | 值 | 理由 |
|---|---|---|---|
| D1 | listen API 形态 | 版本化工具（listen_v2 + query_my_cursors） | 避免 footgun：忘传 cursors 静默降级回 timestamp 模式 |
| D2 | data root | 只做 override + 迁移工具；默认切换推迟 | 默认切换爆炸半径最大（使已装插件失效 + peer 记录的 data_dir 作废） |
| D3 | 源码统一 | parity gate 提前到 Gate 0；canonical 单源推迟到 Wave 4 | gate 廉价且立即防分叉；迁移在协议稳定后做 |
| D4 | spawn 权限 | 新 API 默认 standard，显式 opt-in bypass；legacy 保留 bypass 并标记 | secure-by-default；把"要不要全自主"的选择权显式留给上层 |
| D5 | 内联载荷上限 | 默认 1 MiB，可配置 CC_COMMUNICATE_MAX_INLINE_BYTES | 真实消息多为文本，1 MiB 足够；超限走 artifact_refs |
| D6 | record 模块 | 新增 server/message_record.py | 信封 schema + 原子发布独立成模块，避免 conversations.py 膨胀 |
| D7 | 错误码枚举 | 提前到 Wave 1（随 HP-06） | HP-06 需要结构化错误，HP-07 才给信封；不提前会返工 |
| D8 | spawn_token | env 注入 + 先 live probe + plan B 兜底 | probe 廉价；plan B 保证不阻塞 |
| D9 | 连接 | 同 pair 单 active connection；新建 info.json | 单 active 大幅简化上层心智 |
| D10 | kernel 生命周期 | exit 只看 queue/activity/terminate，registration 不阻退 | registration 不应成永久进程租约；竞态由 client retry + wake 兜底 |

---

## 6. 已知延后项与残留风险

### 6.1 设计性延后（有文档记录）

| 项 | 原因 | 影响 |
|---|---|---|
| HP-12 diagnose_transport + 结构化事件日志 | 未纳入波次路线图；独立增量，不影响协议正确性；**AR-06 分阶段接受** | 日常诊断有替代（结构化 Result + backlog_stats + run_gc dry-run + kernel log）；缺 consolidated health snapshot。**重启条件**：进入 H2/H3 或第一次真实无法定位的传输故障 |
| HP-11 默认 data root 切换 | 爆炸半径最大（使已装插件失效 + peer data_dir 作废） | override（CC_COMMUNICATE_DATA_DIR）已交付，可手动指定 |
| legacy wrapper（create_collaborator / listen timestamp 模式）删除 | 经 telemetry 判定删除时机 | 一个 release 后删除（需 HP-12 telemetry 支撑判定） |

### 6.2 工程性延后（低风险，不影响正确性）

| 项 | 描述 | 风险 |
|---|---|---|
| `cc-communicate-marketplace/` 树 | **AR-05 处置**：标记为历史参考（README 顶部 banner），不支持安装；权威实现为 `v2_win/` + `v2_wsl/`（`tools/build_artifacts.py` 生成），不再同步该树 | 仅影响 marketplace 发布渠道；权威安装入口唯一且可识别 |
| T46 resume MCP 断连 | CC v2.1.220 resume quirk（CC 侧）；**AR-04 已重分类为 DEGRADED**（非"PASS + finding"） | CC 更新后重测 L2，通过则升级状态 |
| known_pids bound-trim TypeError | ~~`sorted(known, key=known.get)` 在 None start_time 上 TypeError（>8 events 时）~~ **AR-03 已修复**：按插入序裁剪，start_time 只用于 PID 复用验证；4 项新回归测试 | 无 |
| 背压是计数 cap 非字节 cap | `MAX_BACKLOG=1000` 条消息，对大消息可能不够精确 | 对 pipe 模型足够；文档记录 by-design |

### 6.3 验收的 PB 风险（pre-existing，有缓解）

| # | 风险 | 缓解 |
|---|---|---|
| PB-1 | 同毫秒 overwrite（极端竞态） | unique filename + sequence 消除；残留极窄 crash-window 重复可经 message_id 检测 |
| PB-2 | 时钟回拨 archive-without-return | per-store sequence 单调；cursor 不依赖 wall clock |
| PB-3 | 跨 realm 时钟差 | per-store cursor 独立；不跨 store 比较 |

---

## 7. 源码与版本同步状态

| 项 | 状态 |
|---|---|
| Git remote (origin/main) | ✅ 同步（local `febc803` == origin/main，无未推送 commit） |
| v2_win ↔ v2_wsl parity | ✅ 字节一致（32 文件 + 33 artifacts） |
| `.mcp.json` 平台入口 | ✅ 正确（win: `python` / wsl: `python3`，install entry `${CLAUDE_PLUGIN_ROOT}/server/mcp_server.py` 不变） |
| `.gitattributes` LF pinning | ✅ v2 trees + templates pin 到 LF（autocrlf=true 防御） |
| `cc-communicate-marketplace/` | ✅ **AR-05 处置**：README 顶部已标记"历史参考，不支持安装"；权威实现为 `v2_win/` + `v2_wsl/`（`tools/build_artifacts.py generate` 生成），该树不再同步 |
| 版本 tag | v0.3.0（`c114aa1`）→ **v0.4.0（本次交付 commit，2026-08-03）**：加固完成 + AR-01~06 修复 |

---

## 8. 结论

cc-communicate 加固程序已完成并通过验收修订（AR-01~06 + N-01~03）。14 条提案中 13 条已实现并通过自动化 gate（220 测试 + parity 32 + artifacts 33）、7 个 live gate（L2 以 `DEGRADED (T46)` 交付，见 §3.7）和 4 轮外部审核。1 条（HP-12 可观测性）为 `DEFERRED（分阶段接受，AR-06）`，H1 期间有明确替代观测面，重启条件已定义（见 §2）。

**交付语义**：`at-least-once delivery + per-store ordering + detectable duplicates + idempotent mutation`——已兑现并验证。

**交付边界**：不保证崩溃条件下严格 exactly-once（重复可检测）；不保证全局跨 store 有序（per-store cursor 的意义）；不保证对恶意本地进程安全（threat model 如实声明）；**不保证 resume 后通信 channel 就绪**（DEGRADED，spawn-fresh fallback，§3.7）——均已如实交付。

**对上层 Runtime 的建议**：
1. 使用 `listen_v2` + `query_my_cursors`（cursor = 传输 ACK，非任务完成；传输故障会以 `INTERNAL`/`PEER_UNREACHABLE` + `degraded_stores` 如实暴露，不会伪装成空成功）。
2. 使用 `spawn_collaborator` 拿 `WorkerHandle`（默认 standard 权限；无人值守自动化显式 `permission_mode="bypass"`）。
3. 错误处理按 `code` + `retryable` 分支，不解析 message 字符串。
4. 大载荷（> 1 MiB）走 `artifact_refs`。
5. H1 不依赖 resume：固定新建 worker，恢复路径不可用时 spawn-fresh 兜底（DEGRADED，§3.7）。
6. 如需 consolidated 诊断接口（进入 H2/H3 或第一次真实无法定位的传输故障时），重启 HP-12 作为独立增量（重启条件见 §2）。
7. 安装入口：`v2_win/`（Windows）+ `v2_wsl/`（WSL，`tools/build_artifacts.py generate` 生成）为唯一权威实现；`cc-communicate-marketplace/` 已标记为历史参考（AR-05）。版本 tag `v0.4.0`。

---

## 9. 验收修订处置（AR-01~06 / N-01~03）

需求方验收审核（`docs/superpowers/reviews/2026-08-03-hardening-acceptance-review.md`）以 `REVISE_REQUESTED` 返回，本报告随交付 commit 一并修订。逐条处置如下（全部 DONE）：

| # | 修订内容 | 处置 | 证据 |
|---|---|---|---|
| AR-01 (P0) | 锁定 MCP 主版本 | `server/requirements.txt`：`mcp>=1.28` → `mcp>=1.28,<2`；新增 clean-install/import 门（3 测试） | `tests/unit/test_mcp_dependency_gate.py`；全新环境按声明安装可导入 `mcp.server.fastmcp` |
| AR-02 (P0) | 禁止传输故障伪装空成功 | `listen_v2`/`query_my_cursors` 跟踪每侧扫描成功；本地零成功 → `INTERNAL` retryable；远端失败 → 本地结果 + `degraded_stores`；新增 7 个注入测试 | `v2_win/.../server/user_functions.py`；`tests/unit/test_cursor_ack.py`（注入三类故障） |
| AR-03 (P0) | known_pids 确定性有界 | 按插入序裁剪 `list(known.keys())[:-8]`，start_time 仅用于 PID 复用验证；新增 4 个回归测试（全 None / 混合 / PID 重复 / 旧日志 replay） | `v2_win/.../server/kernel.py`；`tests/unit/test_check_alive_fallback.py` |
| AR-04 (P1) | resume/L2 重分类 | L2 → `DEGRADED (T46)`（非 PASS+finding）；T46 条目 + §4.3 + §3.1 + 新增 §3.7 能力降级声明（spawn-fresh fallback + CC 更新后重测升级）；SKILL.md evoke 条目 DEGRADED 标注 | `tested&2betest.md` T45/T46；本报告 §3.1/§3.7/§4.3；`v2_win/.../skills/cc-communicate/SKILL.md` |
| AR-05 (P1) | 封闭发布面 | `cc-communicate-marketplace/README.md` 顶部历史参考 banner（方案 2）；tag `v0.4.0` 指向本交付 commit；验收记录 + 本报告 + 任务书 + 全部修复进入交付 commit | `cc-communicate-marketplace/README.md`；tag v0.4.0；git log |
| AR-06 (P1) | HP-12/G4 契约 | HP-12 → `DEFERRED（分阶段接受）`：§1.1/§2/§3.1/§3.4/§6.1 统一措辞 + H1 替代观测面 + 重启条件；总纲 §4.1 分阶段表述 | 本报告 + `plans/2026-07-24-cc-communicate-hardening-master-plan.md` §4.1 |
| N-01 | close_connection 降级细节 | 失败步骤入 `degraded_steps`（干净路径形状不变）；新增 1 测试 | `user_functions.py` close_connection；`tests/unit/test_cursor_ack.py` |
| N-02 | 回归输出 stderr | `run_regression.pytest_run` RED 时同时打印 stderr tail；新增 1 测试 | `tools/run_regression.py`；`tests/unit/test_run_regression.py` |
| N-03 | 开发依赖入口 | 新增仓库根 `requirements-dev.txt`（pytest）；门测试断言其存在 | `requirements-dev.txt`；`tests/unit/test_mcp_dependency_gate.py` |

**重验收门对照**（审核方 §5，全部满足）：①全新环境按声明安装可导入 MCP server（AR-01 门）；②220 测试 + T0/T2 全绿（本报告 §4.1 原始输出）；③`listen_v2`/`query_my_cursors` 区分"成功无数据"与"扫描失败"（AR-02 注入测试）；④9+ 含缺失/混合 start_time 的 SessionStart replay 不崩溃（AR-03 测试）；⑤L2 = DEGRADED + spawn-fresh fallback，不再标 PASS（AR-04）；⑥win/wsl parity 32 + artifacts 33 继续通过，权威安装入口唯一，工具数 20 与报告一致，版本 build identity = tag v0.4.0（AR-05）；⑦本报告（含 AR 处置）进入最终 commit，原始输出摘要见 §4.1（AR-06）。

**测试数说明**：220 = 204（Wave 1-4 基线）+ 16（AR 修订新增：test_mcp_dependency_gate 3 + test_cursor_ack AR-02 7 + test_cursor_ack N-01 1 + test_check_alive_fallback 4 + test_run_regression 1）。

---

## 附录 A：文件清单

### 新增 server 模块（v0.3.0 → 加固后）

| 文件 | 提案 | 职责 |
|---|---|---|
| `server/result.py` | HP-06/07 (D7) | 错误码枚举 + 5-field envelope |
| `server/validation.py` | HP-06/09/10 | 集中校验 + 路径约束 + ResourceExhaustedError |
| `server/message_record.py` | HP-01/09 | 版本化信封 + 原子发布 + artifact_refs |
| `server/operation_journal.py` | HP-03/11 | RPC operation_id + 领域幂等 + schema guard |
| `server/cleanup.py` | HP-08 | 安全 GC（白名单 + 路径组件 guardrail） |
| `server/schema.py` | HP-11 | schema version 常量 + check/stamp/wrap/layout |
| `server/machine_identity.py` | HP-10 (T32) | machine identity（type/id/claude_bin） |

### 新增 tools

| 文件 | 提案 | 职责 |
|---|---|---|
| `tools/check_parity.py` | HP-13-B (D3) | win/wsl hash 比对 gate |
| `tools/build_artifacts.py` | HP-13-A | canonical -> generated artifact (generate/verify) |
| `tools/migrate_data.py` | HP-11 | schema 迁移 CLI |
| `tools/run_regression.py` | HP-00 | 回归 gate (T0/T1/T2 + L1-L7) |
| `tools/artifact_templates/` | HP-13-A | mcp.win.json + mcp.wsl.json |

### 测试结构

```
tests/
  conftest.py                      # CC_COMMUNICATE_DATA_DIR 隔离 + 依赖序 reload
  unit/                            # 35 个测试文件
    test_message_record.py         # HP-01
    test_cursor_ack.py             # HP-02
    test_rpc_idempotency.py        # HP-03
    test_validation.py             # HP-06
    test_run_regression.py         # HP-00 (gate logic)
    test_build_artifacts.py        # HP-13-A
    test_gc.py                     # HP-08
    test_kernel_exit.py            # HP-08
    test_resource_limits.py        # HP-09
    test_artifact_refs.py          # HP-09
    test_permission_mode.py        # HP-10
    test_schema.py                 # HP-11
    test_kernel_schema_guard.py    # HP-11
    test_migrate_data.py           # HP-11
    test_spawn_env.py              # HP-08 (T38)
    test_proc_pid_matches.py       # HP-08
    ...（共 35 文件，204 测试）
  parity/
    test_parity.py                 # HP-13-B
```

### 记录文件

- `tested&2betest.md` §1：T1-T49（全部 bug fix + wave acceptance 记录）
- `docs/superpowers/specs/`：每个 HP 的设计 spec
- `docs/superpowers/plans/`：每个 HP 的实现 plan
- `docs/superpowers/reviews/`：Wave 1-4 外部审核 brief
