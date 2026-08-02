# cc-communicate 加固验收审核（Proposal–Revise–Accept）

> 审核日期：2026-08-03  
> 审核对象：`docs/superpowers/reviews/2026-08-03-hardening-completion-report.md` 与 `febc803` 上的实现  
> 审核视角：未来 Clever User / 上层 Agent Runtime 的底层依赖  
> **总体决策：`REVISE_REQUESTED`**

## 1. 结论先行

本轮不驳回加固架构，也不要求重做 Wave 1–4。消息记录、per-store cursor、幂等副作用、WorkerHandle、连接生命周期、资源限制、权限策略与 win/wsl 单一事实源的主体设计可以保留。

但交付物尚不能从 `VERIFIED` 推进到最终 `ACCEPTED`，原因是：

1. 全新环境按当前依赖声明会解析到 MCP 2.0，而服务器仍使用 MCP 1.x 的 `mcp.server.fastmcp` 导入，导致收集/启动失败。
2. 首选 API `listen_v2` 和 `query_my_cursors` 会把本地 kernel/RPC 故障吞掉，以成功结果返回“无消息/无 cursor”。这会让上层把传输故障误判为 worker 沉默，与 HP-07 的结构化失败契约相冲突。
3. `known_pids` 上限裁剪存在已知 `None` 混合排序崩溃；对长时间运行、多次 compact/resume 的上层 Runtime，这不应按“罕见”接收。
4. 最新 live 证据中，resume 后的消息交付 2/2 失败；它可以是 CC 客户端问题，但对消费契约的上层而言仍是“当前不可靠的能力”，不能作为 L2 完整通过。
5. 可安装交付面仍标注 14/16 个工具和 v0.1.0/v0.3.0，marketplace 树仍是旧实现，加固报告本身也未进入版本控制。因此“开发树可验证”尚未封装成“可被上层稳定消费的版本”。

因此，本轮状态应是：**核心方向接受，交付验收请求修订**。

## 2. 独立验证证据

### 2.1 通过项

在不修改产品代码的前提下，用隔离依赖环境和可写临时目录复跑：

```text
204 passed in 18.25s

T0 syntax     PASS (44 .py + 2 .js)
T1 pytest     PASS (204 passed)
T2 parity     PASS (32)
T2 artifacts  PASS (33)
GATE          PASS
```

这证明报告的主体自动化结果可复现，不是仅凭文档声称。`tested&2betest.md` 中也有 L1、L3–L7 的真实环境记录。

### 2.2 环境说明

当前审核环境没有 `py` launcher，而 `tools/run_regression.py` 的文档示例使用 `py -3`。审核改用隔离的 Python/uv 运行同一组 gate。这不影响上述测试结论，但暴露了回归入口尚未包含完整的可复现开发依赖说明。

### 2.3 独立发现

- `server/requirements.txt` 声明 `mcp>=1.28`。全新解析得到 MCP 2.0.0 后，`server/mcp_server.py` 的 `from mcp.server.fastmcp import FastMCP` 失败，报 `ModuleNotFoundError`。将环境约束为 `mcp>=1.28,<2` 后，204 项测试全部通过。
- `user_functions.py` 中 `listen_v2` 捕获本地 RPC 异常后当作空结果继续轮询，超时后返回 `ok({messages: []})`；`query_my_cursors` 也在本地 RPC 异常后返回 `ok({})`。
- `kernel.py` 对 `known_pids` 做 `sorted(known, key=known.get)`，但 `parse_start_time` 可返回 `None`。现有 bound 测试把所有时间 monkeypatch 为 `0.0`，未覆盖 9+ 个事件且值为 `None`/混合类型的路径。
- `tested&2betest.md` 的 T46 明确记录 resume 后消息交付 2/2 失败；报告同时将 L2 写为 `PASS + finding`。这两句在能力契约上不能同时成立。

## 3. 必须修订的 proposal

### AR-01 — 锁定或迁移 MCP 主版本契约

- **Decision**：`REVISE`
- **必要性**：P0。当前状态会让全新安装直接无法导入 MCP server，属于启动阻断。
- **结构可行性**：高。短期选择是将运行时依赖改为 `mcp>=1.28,<2`；如果决定支持 MCP 2.x，则单独做 API 迁移，不要仅放宽依赖范围。
- **实现/插入难度**：S（锁版）；M（迁移）。改 canonical `v2_win` 后生成 WSL artifact。
- **风险**：锁版会推迟 MCP 2.x 支持，但比静默安装一个不兼容主版本更诚实。
- **验收**：在无旧 site-packages 的全新环境中，仅安装仓库声明的依赖，能导入并启动 MCP server；加一个 clean-install/import gate。

### AR-02 — 禁止把传输故障伪装成空成功

- **Decision**：`REVISE`
- **必要性**：P0。Clever User 会依据“worker 没有回复”决定等待、重试、回退或更换 builder。若底层隐藏 kernel 失联，上层的收敛率、成本和时间判断全部会被污染。
- **结构可行性**：高。保留轮询内的有界重试，但必须记录本地/远端 store 的连续失败；如果到达 deadline 仍无一次成功扫描，返回结构化 `INTERNAL` 或 `PEER_UNREACHABLE`，并如实设置 `retryable`。“成功扫描但没消息”才能返回空成功。
- **实现/插入难度**：S–M。主要位于 `listen_v2`/`query_my_cursors`，可复用 HP-07 Result/Error envelope。
- **风险**：历史上依赖空结果的调用者可能看到新错误分支；这是必要的契约修正，应在发布说明中明示。
- **验收**：注入本地 kernel 不可达、远端不可达、部分 store 可达三类故障，验证空成功与失败可区分，且不丢已成功扫描到的消息。

### AR-03 — 修复 `known_pids` 的确定性有界策略

- **Decision**：`REVISE`
- **必要性**：P0。该路径会处理 SessionStart 重放，与长程 Runtime 的 restart/resume 高度相关；超过 8 个事件不是不可触发的边角情况。
- **结构可行性**：高。不应用可缺失的 process start time 同时承担“新旧次序”。可使用事件序号/插入顺序作为裁剪依据，start time 仅用于 PID 复用验证。
- **实现/插入难度**：S。
- **风险**：改动 replay 状态结构时要保持旧 event log 可读；不应为修此 bug 引入破坏性 schema 迁移。
- **验收**：9+ 个 SessionStart 事件，覆盖全 `None`、`None`+浮点混合、PID 重复、旧日志 replay；最终有界且 `check_alive` 不回归。

### AR-04 — 重新定义 resume/L2 的“通过”

- **Decision**：`REVISE`
- **必要性**：P1。归因可以是外部 CC，但验收状态必须以用户可见能力为准。“进程恢复且 cwd 正确”不等于“通信恢复”。
- **结构可行性**：中。建议拆成两层：`evoke` 只承诺 process/session 恢复；`connect`+关联 hello/reply 才承诺 channel ready。L2 必须以恢复后的实际往返为终点。
- **实现/插入难度**：S（重分类和文档）；M–L（对当前 CC 版本做自动恢复/降级）。
- **风险**：如果是 CC v2.1.220 外部缺陷，底层无法保证原 session 可恢复。系统应暴露明确失败，而不是伪装成功。
- **验收/处置选项**：
  1. 修复或实现可靠 workaround，恢复后 round-trip 连续通过；或
  2. 将 resume 标记为当前版本 `DEGRADED/UNSUPPORTED`，上层 H1 明确采用 spawn-fresh fallback，并保留版本化重测条件。

### AR-05 — 封闭可安装、可识别的发布面

- **Decision**：`REVISE`
- **必要性**：P1。上层必须能确定它加载的是加固后实现，而不是工具数、版本和源码均滞后的 marketplace 副本。
- **结构可行性**：高。确定一个权威安装入口，并在以下两种方案中二选一：
  1. 同步 marketplace 树、manifest 和版本；或
  2. 明确将旧 marketplace 树隔离/标注为历史参考，只支持由 canonical artifact 生成的安装入口。
- **实现/插入难度**：S–M。
- **风险**：过早宣称公开发布可能增加维护负担；本 proposal 不要求公开发布，只要求内部可安装物不含混。
- **验收**：工具数字与实际 20 个一致；版本高于加固前 v0.3.0；可从 clean checkout 按文档安装到唯一权威 artifact；打新 tag 或给出同等不可混淆的 build identity；加固报告和本验收记录进入交付 commit。

### AR-06 — 正式修订 HP-12 与 G4，不将未交付写成已通过

- **Decision**：`REVISE`，接受分阶段延后。
- **必要性**：P1（契约），HP-12 本体对 H1 可降为 P2。原总验收门 G4 要求 `diagnose_transport`，原 handoff contract 也承诺所有降级/重试/残留可查。当前 13/14 完成却声称整体 gate 全通过，状态机不严谨。
- **结构可行性**：高。不强迫本轮立即实现完整 HP-12；但要更新 master contract/报告，将 G4 标记为“分阶段接受”，写明 H1 可用的替代诊断面和 HP-12 的再入条件。
- **实现/插入难度**：S（契约更正）；M（未来 HP-12）。
- **风险**：若上层进入 H2/H3 仍无 consolidated health snapshot，跨多 worker 的故障定位成本会快速增长。
- **验收**：报告不再同时出现“L1–L7 全通过”与未通过的 capability；明确 HP-12 在 H1 期间的替代观测方法，并将“进入 H2/H3 或第一次真实无法定位的传输故障”设为 HP-12 重启条件。

## 4. 不阻断本轮验收的修改建议

### N-01 — `close_connection` 返回降级细节

`close_connection` 定位为 best-effort 是合理的，但目前 ACK 上传、通知、unregister 和 deactivate 的所有异常都被吞掉，最终恒返回 `{closed: true}`。建议保留非阻塞语义，同时增加 `degraded_steps`/warnings，便于上层决定是否留下清理任务。

### N-02 — 修正测试分类和回归输出

报告宣称 `unit + integration + parity`，但仓库测试目录的自动分类主要是 `unit` 与 `parity`，integration/live 证据在 `tested&2betest.md` 中。建议文档区分“自动 integration test”与“手工 live gate”。`run_regression.py` 在 pytest 失败时还应同时显示 stderr，避免依赖缺失时只输出空失败摘要。

### N-03 — 开发依赖入口

新增 `requirements-dev`/lockfile 或在回归文档中声明 pytest 等 gate 依赖，使“一条命令验证”在 clean checkout 中真实成立。

### N-04 — 继续保留的延后项

默认 data-root 切换可继续延后；backlog 当前按条数而非字节限制也可作为已知边界保留。两者都不阻断 Clever User H1。

## 5. Builder 修订后的最小重验收门

Builder 不需重跑全部设计流程，但下列证据缺一不可 `ACCEPTED`：

1. 全新环境按仓库声明安装，MCP server 可导入/启动。
2. 原 204 项测试和 T0/T2 gate 继续全绿，加上 AR-01–03 的新回归测试。
3. `listen_v2`/`query_my_cursors` 对“成功无数据”和“扫描失败”给出不同的结构化结果。
4. 9+ 个含缺失/混合 start time 的 SessionStart replay 不崩溃。
5. L2 按“恢复后 round-trip”重跑；若仍因外部 CC 失败，以 `DEGRADED/UNSUPPORTED + spawn-fresh fallback` 交付，不再标 PASS。
6. win/wsl artifact parity 继续通过；权威安装入口、manifest 工具数、版本/build identity 一致。
7. 更正后的 completion report 进入最终 commit，并列出本轮 AR-01–06 的处置和原始输出摘要。

## 6. 对上层 Clever User 项目的影响

- **可以继续**：上层契约、harness 边界、H1 测试任务与控制组的纸面设计。现有传输 schema 已足够支撑这部分。
- **应等待 AR-01–03 后再做**：把 cc-communicate 真正接入 H1 执行环，否则一次“worker 无回复”无法区分是 builder 不收敛还是传输已坏。
- **H1 可暂不依赖 resume**：第一阶段可固定为新建 worker，当恢复路径不可用时 spawn fresh；但该降级必须被明确记录并计入时间/费用。
- **HP-12 的建议时机**：H1 可使用结构化错误 + backlog stats + GC dry-run 起步；进入多 worker 的 H2/H3 前，应重新评估 `diagnose_transport` 和结构化事件日志。

## 7. 最终状态表

| 对象 | 本轮状态 | 说明 |
|---|---|---|
| Wave 1–4 主体架构 | `ACCEPTED_IN_PRINCIPLE` | 不重做 |
| 当前实现 | `IMPLEMENTED` | 代码存在且大部分门通过 |
| 自动回归 | `VERIFIED_WITH_ENV_CONSTRAINT` | MCP 1.x 下 204/204；无界依赖的 clean install 失败 |
| Resume 通信能力 | `DEGRADED` | 进程恢复成功，最新交付 2/2 失败 |
| HP-12 完整可观测性 | `DEFERRED` | H1 可分阶段接受，必须修正契约 |
| 作为上层 Runtime 稳定依赖 | `REVISE_REQUESTED` | 完成 AR-01–06 并重验收后才能 `ACCEPTED` |

