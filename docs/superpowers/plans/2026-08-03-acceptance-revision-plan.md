# 加固验收修订任务书（AR-01 ~ AR-06）

> **来源**：需求方验收审核 `docs/superpowers/reviews/2026-08-03-hardening-acceptance-review.md`
> **状态**：`REVISE_REQUESTED` -> 需完成 AR-01~06 + 重验收后 `ACCEPTED`
> **基线**：main `febc803`，204 tests，GATE PASS
> **执行约束**：inline 执行，edit `v2_win/` only -> `py -3 tools/build_artifacts.py generate` -> commit both trees

---

## 必读文件

按顺序读以下文件获取上下文（不要跳过）：

1. `docs/superpowers/reviews/2026-08-03-hardening-acceptance-review.md` -- 需求方完整审核意见，6 条 AR 的必要性/结构可行性/验收标准全部在此
2. `plans/2026-07-24-cc-communicate-hardening-master-plan.md` -- 总纲，§4 交付契约（AR-06 修订对象）
3. `docs/superpowers/reviews/2026-08-03-hardening-completion-report.md` -- 加固完成报告（AR-04/05/06 修订对象，当前 untracked，需进入交付 commit）
4. `tested&2betest.md` §1 T46 -- resume delivery 失败记录（AR-04 依据）
5. `v2_win/cc-communicate/server/user_functions.py:667-727` -- `listen_v2`/`query_my_cursors` 当前实现（AR-02 修复对象）
6. `v2_win/cc-communicate/server/kernel.py:297-308` -- `known_pids` bound-trim（AR-03 修复对象）
7. `v2_win/cc-communicate/server/requirements.txt` -- MCP 依赖声明（AR-01 修复对象）
8. `v2_win/cc-communicate/server/rpc_client.py` -- `call()`（抛 `KernelError`）/ `call_remote()`（返回 `None`，不抛）的语义区别（AR-02 修复需要理解）

---

## AR-01 - 锁定 MCP 主版本（P0，effort S）

**问题**：`requirements.txt` 声明 `mcp>=1.28`（无上界）；`mcp_server.py` 使用 `from mcp.server.fastmcp import FastMCP`（MCP 1.x API）。全新 `pip install` 解析到 MCP 2.0.0 -> `ModuleNotFoundError`。

**修复**：
- `v2_win/cc-communicate/server/requirements.txt`：`mcp>=1.28` -> `mcp>=1.28,<2`
- generate -> 双树同步

**新增测试**：clean-install/import gate -- 在测试中模拟全新环境，验证 `import mcp_server` 不因 MCP 版本冲突失败（可用 `subprocess` 隔离或 `monkeypatch` sys.modules 验证 import 路径）。具体形式自行判断，核心是"依赖声明与 import 路径一致"。

**验收**（审核方 §5.1）：全新环境按仓库声明安装，MCP server 可导入/启动。

---

## AR-02 - 禁止把传输故障伪装成空成功（P0，effort S-M）

**问题**：`listen_v2`（`user_functions.py:667-709`）在本地 kernel 不可达时，`rpc_client.call()` 抛 `KernelError` 被 `except Exception` 吞掉 -> `r = None` -> 当作空结果继续轮询 -> 超时后返回 `ok({messages: [], next_cursors: cursors})`。`query_my_cursors`（`:712-727`）同理，本地+远端都失败时返回 `ok({})`。

上层无法区分"worker 沉默"和"传输坏了"。

**关键语义**（你需要理解的）：
- `rpc_client.call()`（本地）：失败抛 `KernelError`；成功返回 result（dict 或其他）
- `rpc_client.call_remote()`（远端）：失败返回 `None`（不抛）；成功返回 result
- "成功扫描但无消息" -> 空成功是合法的
- "零次成功扫描" -> 必须返回结构化错误

**修复方向**（审核方 §3 AR-02 已给出结构，按此实现）：
- poll 循环中跟踪"是否有至少一次成功的本地 scan"和"是否有至少一次成功的远端 scan"
- 到 deadline 时：
  - 本地零成功 + 远端零成功（或无远端）-> `err(INTERNAL, "kernel unreachable", retryable=True)` 或 `err(PEER_UNREACHABLE, ...)` 视场景而定
  - 本地成功 + 远端失败 -> 返回本地结果 + 远端 degraded 标记（在 data 中附带 `degraded_stores` 或类似字段）
  - 本地成功 + 远端成功 -> 正常返回（含空消息）
- `query_my_cursors` 同理：本地+远端都失败时返回错误，不返回 `ok({})`
- 复用 HP-07 的 `_err` helper 和 `Code` 枚举

**新增测试**（审核方 §3 AR-02 验收）：注入三类故障：
1. 本地 kernel 不可达 -> `listen_v2` 返回错误（不是空成功）
2. 远端不可达 -> 返回本地结果 + degraded 标记
3. 部分可达（本地 OK 远端失败）-> 不丢已扫描到的消息，有 degraded 标记

**验收**（审核方 §5.3）：`listen_v2`/`query_my_cursors` 对"成功无数据"和"扫描失败"给出不同的结构化结果。

---

## AR-03 - 修复 known_pids 确定性有界策略（P0，effort S）

**问题**：`kernel.py:307` 的 `sorted(known, key=known.get)` 在 `known.get(pid)` 返回 `None` 时触发 `TypeError`（`None` 与 `float` 不可比较）。触发条件：`len(known) > 8` 且任一 entry 的 start_time 为 `None`。

**修复方向**（审核方 §3 AR-03 已给出结构）：
- 不用 start_time 做裁剪依据。Python 3.7+ dict 保持插入序，用 `list(known.keys())[:-8]` 裁剪最旧的 entry。
- start_time 仅用于 `proc.pid_matches` 的 PID 复用验证，不承担排序职责。
- 不引入 schema 变更。

**新增测试**（审核方 §3 AR-03 验收）：
1. 9+ 个 SessionStart 事件，全部 `start_time=None` -> 不崩溃
2. `None` + `float` 混合 -> 不崩溃
3. PID 重复（同 pid 多次 start）-> 有界且 check_alive 不回归
4. 旧日志 replay（kernel restart 加载已持久化的 sessions）-> 不崩溃

**验收**（审核方 §5.4）：9+ 个含缺失/混合 start time 的 SessionStart replay 不崩溃。

---

## AR-04 - 重新定义 resume/L2 的"通过"（P1，effort S）

**问题**：T46 记录 resume 后消息交付 2/2 失败（CC v2.1.220 MCP 客户端断连）。报告中 L2 写为 "PASS + finding"--契约上自相矛盾。

**修复**（文档/契约更正，不改代码）：
1. `tested&2betest.md` T46 条目：将 L2 状态从 "PASS + finding" 改为 `DEGRADED`。明确：进程/session 恢复成功（cwd 正确），但通信恢复失败（CC 侧 MCP 断连）。
2. `docs/superpowers/reviews/2026-08-03-hardening-completion-report.md`：
   - §4.3 Live gates 表：L2 从 "PASS + finding T46" 改为 `DEGRADED (T46)`
   - §3.1 交付保证表：resume 能力标记为 `DEGRADED`，注明 spawn-fresh fallback
   - 新增一段"能力降级声明"：H1 不依赖 resume，固定新建 worker；CC 更新后重测，若恢复则升级状态
3. `v2_win/cc-communicate/SKILL.md` 或 `README.md`：如有 resume 相关文档，补充 DEGRADED 标注和 spawn-fresh fallback 建议

**验收**（审核方 §5.5）：L2 按"恢复后 round-trip"定义；若仍因外部 CC 失败，以 `DEGRADED + spawn-fresh fallback` 交付，不再标 PASS。

---

## AR-05 - 封闭可安装、可识别的发布面（P1，effort S-M）

**问题**：
- `cc-communicate-marketplace/` 仍停留在 v0.x（缺 11 个 server 文件，工具数/版本滞后）
- 无新版本 tag（v0.3.0 后 89 commits）
- 加固报告和验收记录均 untracked

**修复**：
1. **marketplace 处置**（审核方建议二选一，推荐方案 2）：
   - 方案 2（推荐）：在 `cc-communicate-marketplace/README.md` 顶部标注"历史参考，不支持安装；权威实现为 `v2_win/` + `v2_wsl/`（由 `tools/build_artifacts.py` 生成）"
   - 或方案 1：同步 marketplace 树到 v2 代码 + 更新 manifest（effort 更大）
2. **版本 tag**：打 `v0.4.0`（或你判断合适的版本号），指向修复后的 commit。tag message 注明加固完成 + AR 修复。
3. **提交报告**：`git add` 加固报告 + 验收记录 + 本任务书 + 修复后的所有文件，进入交付 commit。

**验收**（审核方 §5.6）：工具数与实际 20 个一致；版本高于 v0.3.0；clean checkout 按文档安装到唯一权威 artifact；新 tag 或同等 build identity。

---

## AR-06 - 正式修订 HP-12 与 G4 契约（P1，effort S）

**问题**：报告说"L1-L7 全通过"但 HP-12（G4 `diagnose_transport`）未交付。状态机不严谨。

**修复**（契约更正）：
1. `docs/superpowers/reviews/2026-08-03-hardening-completion-report.md`：
   - §1.2 波次表或 §4 验证证据：G4（HP-12 `diagnose_transport`）标记为 `DEFERRED (分阶段接受)`，不与其他 gate 混列为"全通过"
   - §3.4 API 面：`diagnose_transport` 标注 `❌ 未交付（HP-12 DEFERRED）`
   - §3.1 可观测性行：明确 H1 期间替代观测方法（结构化 Result/Error + `backlog_stats` + `run_gc(dry_run)` + kernel log）
   - 新增"HP-12 重启条件"：进入 H2/H3 或第一次真实无法定位的传输故障时重启 HP-12
2. `plans/2026-07-24-cc-communicate-hardening-master-plan.md` §4.1：将"可观测"承诺改为分阶段表述（H1 替代面 + HP-12 重启条件）

**验收**（审核方 §5.7）：报告不再同时出现"全通过"与未通过的 capability；明确 HP-12 替代观测方法 + 重启条件。

---

## 非阻断建议（建议一并处理，effort 均 S）

| # | 内容 | 文件 |
|---|---|---|
| N-01 | `close_connection` 返回 `degraded_steps`/warnings 而非恒 `{closed: true}` | `user_functions.py` close_connection |
| N-02 | `run_regression.py` pytest 失败时同时显示 stderr（当前只显示 stdout tail） | `tools/run_regression.py` pytest_run() |
| N-03 | 新增 `requirements-dev.txt`（pytest）或在回归文档中声明 gate 依赖 | repo root |

N-04（默认 data-root 延后、count-cap 延后）继续保留，不处理。

---

## 执行顺序

1. **AR-01/02/03 代码修复**（可并行）-> 每个加新测试 -> `py -3 -m pytest -q` 全绿
2. **N-01/02/03** 一并处理
3. `py -3 tools/build_artifacts.py generate` -> 双树同步
4. `py -3 tools/run_regression.py` -> GATE PASS（含新测试）
5. **AR-04/05/06 文档/契约更正**
6. `git add` 全部 -> commit -> 打 tag
7. 更新 `tested&2betest.md` §1 新增 T# 记录 AR-01~06 处置

## 重验收门（审核方 §5，缺一不可）

1. 全新环境按仓库声明安装，MCP server 可导入/启动
2. 原 204 项测试 + T0/T2 gate 继续全绿 + AR-01~03 新回归测试
3. `listen_v2`/`query_my_cursors` 对"成功无数据"和"扫描失败"给出不同结构化结果
4. 9+ 个含缺失/混合 start time 的 SessionStart replay 不崩溃
5. L2 标记 DEGRADED + spawn-fresh fallback（不再标 PASS）
6. win/wsl artifact parity 继续通过；权威安装入口、manifest 工具数、版本/build identity 一致
7. 更正后的 completion report 进入最终 commit，列出 AR-01~06 处置 + 原始输出摘要
