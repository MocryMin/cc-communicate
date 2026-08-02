# cc-communicate 加固修订交付 — 重验收回应（Response to Acceptance Review）

> 回应日期：2026-08-03
> 回应对象：`docs/superpowers/reviews/2026-08-03-hardening-acceptance-review.md`（`REVISE_REQUESTED`，AR-01~06 + N-01~04）
> 交付标识：delivery commit `a8927a0`（一轮修订）+ 二轮修订 commit（RAR-01~04）；annotated tag **`v0.4.0`** 打在二轮修订交付 commit 上（manifest 已同步 0.4.0，与 tag 一致）
> **本轮请求：按审核方 §5 的 7 条最小重验收门进行最终 `ACCEPTED` 判定**

## 1. 结论先行

审核方提出的 6 条必须修订项（AR-01~06）全部完成处置，4 条非阻断建议中 N-01~03 已落实、N-04 按审核方意见继续延后。修订未触碰已 `ACCEPTED_IN_PRINCIPLE` 的 Wave 1–4 主体架构：AR-01~03 为最小化代码修复（各附带回归测试），AR-04~06 为契约/发布面更正。

交付 commit `a8927a0` 包含：代码修复 + 16 项新回归测试、更正后的 completion report（§9 AR 处置表 + §4.1 原始输出）、审核方的验收审核原文、任务书、T50 测试记录。当前 gate 状态：**GATE PASS**（T0 syntax 44 .py + 2 .js；T1 pytest **220 passed**（204 基线 + 16 新增）；T2 parity 32 files；T2 artifacts 33 files）。原始输出与复跑命令见 §5。

## 2. AR-01~06 处置

| # | 审核方要求 | 处置 | 证据 |
|---|---|---|---|
| **AR-01** (P0) | 锁定或迁移 MCP 主版本契约 | 采纳锁版方案：`server/requirements.txt` 改为 `mcp>=1.28,<2`（注释说明 MCP 2.x 移除 `mcp.server.fastmcp`）。**不**放宽到 2.x 后再静默不兼容；MCP 2.x API 迁移留作未来独立 proposal | `tests/unit/test_mcp_dependency_gate.py`（3 项）：① 声明 pin 断言（防回归）；② **fresh-interpreter 测试**——subprocess 剥离 PYTHONPATH 后 `from mcp.server.fastmcp import FastMCP` 并断言主版本为 1.x；③ requirements-dev.txt 声明断言（兼 N-03）。WSL artifact 已由 `tools/build_artifacts.py generate` 重新生成 |
| **AR-02** (P0) | 禁止把传输故障伪装成空成功 | `listen_v2`/`query_my_cursors` 增加 per-store 扫描成功跟踪（本地 `rpc_client.call` 抛 `KernelError`；远端 `call_remote` 返回 None）。deadline 判定：**本地零成功 → `err(INTERNAL, retryable=True)`**；本地成功 + 远端失败 → 正常结果 + **`degraded_stores`** 标记；已成功扫描的消息**绝不丢失**（本地死 + 远端有消息时，消息随 `degraded_stores: [LOCAL]` 一并返回）；全部成功时无 `degraded_stores` key，干净路径 shape 不变 | `tests/unit/test_cursor_ack.py` 新增 7 项注入测试，覆盖审核方点名的三类故障（本地不可达 / 远端不可达 / 部分可达）+ 边界（本地死但远端消息不丢、全成功无 key 的 shape 稳定性、`query_my_cursors` 对偶两项）。契约已在 `SKILL.md` 相应条目注明（"Transport honesty (AR-02)"） |
| **AR-03** (P0) | 修复 `known_pids` 确定性有界策略 | 按审核方建议：`kernel.py` 裁剪依据从 `sorted(known, key=known.get)` 改为**插入序** `list(known.keys())[:-8]`；start_time 仅用于 PID 复用验证（`proc.pid_matches`），不再承担新旧次序。无 schema 变更，旧 event log 可读性不受影响 | `tests/unit/test_check_alive_fallback.py` 新增 4 项：全 `None`（12 事件 → 有界 8、序保持）、`None`+浮点混合、PID 重复 + `check_alive` 不回归、**真实重启 replay 路径**（`process_session_ctrl_event` 读持久化 start 事件） |
| **AR-04** (P1) | 重新定义 resume/L2 的"通过" | 采纳**处置选项 2**：L2 重分类为 **`DEGRADED (T46)`**——进程/session 恢复成功，CC v2.1.220 上恢复后消息交付 2/2 失败（CC 侧问题）。completion report 新增 §3.7 能力降级声明（spawn-fresh fallback 为 H1 推荐路径 + CC 更新后重测的升级条件）；T45/T46 记录、§3.1 表、§4.3 live gates 表、§8 结论全部统一为 DEGRADED；`SKILL.md` evoke 条目与 `README.md` 新增 DEGRADED 说明；`run_regression.py` L2 checklist 增加 DEGRADED 记录要求。全库已无任何 "PASS + finding" 表述残留 | `tested&2betest.md` T45/T46；completion report §3.1/§3.7/§4.3/§8；`v2_win/cc-communicate/skills/cc-communicate/SKILL.md`；`v2_win/cc-communicate/README.md`；`tools/run_regression.py` |
| **AR-05** (P1) | 封闭可安装、可识别的发布面 | 采纳**方案 2**：`cc-communicate-marketplace/README.md` 顶部 banner 标注"历史参考，不支持安装"，权威实现指向仓库根 `v2_win/` + `v2_wsl/`（`build_artifacts.py` 生成）。版本/build identity：annotated tag **`v0.4.0`** 打在 delivery commit `a8927a0` 上并推送。工具数与 manifest 无变更（自 Wave 2 起即 20 个 FastMCP 工具，与 `.mcp.json` 一致，非旧 marketplace 的 14/16）。审核原文 + 更正后 completion report 已进入交付 commit | `cc-communicate-marketplace/README.md`；`git tag -n v0.4.0`；`git log a8927a0` |
| **AR-06** (P1) | 正式修订 HP-12 与 G4，接受分阶段延后 | HP-12/G4 统一标记 **`DEFERRED (分阶段接受)`**（report §1.1/§2/§3.1/§3.4/§6.1/§8 措辞一致，不再出现"13/14 完成却整体 gate 全通过"的表述）。H1 替代观测面按审核方 §6 建议写明：结构化 Result/Error（含 AR-02 新增的 degraded 标记）+ `backlog_stats` + `run_gc(dry_run)` + kernel log。**HP-12 重启条件**：进入 H2/H3，或第一次真实无法定位的传输故障。总纲 §4.1 可观测承诺改为分阶段表述，§4.4 增加 `diagnose_transport` 延后注释 | completion report 各节；`plans/2026-07-24-cc-communicate-hardening-master-plan.md` §4.1/§4.4 |

## 3. N-01~04 处置

| # | 建议 | 处置 |
|---|---|---|
| **N-01** | `close_connection` 返回降级细节 | 已落实。失败的 best-effort 步骤以 `degraded_steps` 列表报告（`upload_ack_timestamp` / `upload_cursor:<store>` / `notify_peer` / `deactivate_connection`）；干净时该 key 不存在（shape 不变），语义仍为非阻塞、`closed` 恒为 True。+1 测试（四步骤全部注入失败并逐一断言） |
| **N-02** | 回归输出显示 stderr | 已落实。`run_regression.pytest_run` 在 RED 时打印 stderr 尾部（最后 20 个非空行）——依赖缺失类崩溃只写 stderr，此前输出为空摘要。+1 测试（mock 带 stderr 的 CompletedProcess 断言输出）。测试分类表述已在报告中区分"自动 unit/parity"与"手工 live gate" |
| **N-03** | 开发依赖入口 | 已落实。仓库根新增 `requirements-dev.txt`（`pytest>=8` + 用途注释），由 AR-01 的 gate 测试断言，clean checkout 的"一条命令验证"成立（复跑命令见 §5） |
| **N-04** | 继续保留的延后项 | 按审核方意见**维持延后**：默认 data-root 切换不做；backlog 按条数而非字节限制作为已知设计边界保留。不阻断 H1 |

## 4. 重验收门逐条证据（对应审核方 §5）

> **门 1**：全新环境按仓库声明安装，MCP server 可导入/启动。

`test_requirements_pins_mcp_major_1` + `test_mcp_fastmcp_imports_in_fresh_interpreter`（subprocess、env 剥离 PYTHONPATH、断言 `mcp` 主版本 1.x 且 `FastMCP` 可导入）。审核方可在隔离 venv 中 `pip install -r v2_win/cc-communicate/server/requirements.txt` 后独立复验。

> **门 2**：原 204 项测试和 T0/T2 gate 继续全绿，加上 AR-01–03 的新回归测试。

220 passed = 204 基线 + 16 新增（AR-01: 3，AR-02: 7，AR-03: 4，N-01: 1，N-02: 1）；T0/T2 全 PASS。原始输出见 §5。

> **门 3**：`listen_v2`/`query_my_cursors` 对"成功无数据"和"扫描失败"给出不同的结构化结果。

7 项注入测试：扫描失败 → `err(INTERNAL, retryable=True)`（本地）或 `ok` + `degraded_stores`（远端/部分）；成功无数据 → `ok({messages: []})` 且无 degraded key。两类结果在 envelope 层可区分。
**RAR-01 更正**：`query_my_cursors` 的 `degraded_stores` 不再写入 cursor map——返回稳定 wrapper `data = {cursors, degraded_stores}`（见 §8），`data.cursors` 可直接按文档传给 `listen_v2` 且通过入口 `validate_cursors`。

> **门 4**：9+ 个含缺失/混合 start time 的 SessionStart replay 不崩溃。

4 项测试：全 `None`、`None`+浮点混合、PID 重复（`check_alive` 不回归）、持久化事件经 `process_session_ctrl_event` 的真实重启 replay。全部有界 ≤8 且不崩溃。

> **门 5**：L2 按"恢复后 round-trip"重跑；若仍因外部 CC 失败，以 `DEGRADED/UNSUPPORTED + spawn-fresh fallback` 交付，不再标 PASS。

按选项 2 交付：L2 = `DEGRADED (T46)`，spawn-fresh fallback 写入 report §3.7 + `SKILL.md` + `README.md`；保留 CC 更新后重测的版本化升级条件。当前文档中无 L2 的 PASS 表述。

> **门 6**：win/wsl artifact parity 继续通过；权威安装入口、manifest 工具数、版本/build identity 一致。

T2 parity 32 files + T2 artifacts 33 files（templates pinned）全 PASS；0-diff 不变量成立（`build_artifacts.py generate` 后 `git diff v2_wsl` 为空）；权威安装入口 = `v2_win/` canonical + `v2_wsl/` 生成物（marketplace 已标注历史参考）；tag `v0.4.0` == 交付 commit。
**RAR-03 更正**：上一版"工具数 20 与 `.mcp.json` 一致"的表述不成立——`.mcp.json` 只声明 server 启动命令，不枚举工具数；权威 manifest 是 `.claude-plugin/plugin.json`（及外层 marketplace.json），当时仍为 0.3.0/"16 MCP tools"。现已修正为 `0.4.0 / 20 MCP tools` 并新增防漂移 gate，`claude plugin details` 实测上报 `cc-communicate 0.4.0 ... Exposes 20 MCP tools`（见 §8）。

> **门 7**：更正后的 completion report 进入最终 commit，并列出本轮 AR-01–06 的处置和原始输出摘要。

`docs/superpowers/reviews/2026-08-03-hardening-completion-report.md` §9（AR 处置表）+ §4.1（原始 gate 输出）已随 `a8927a0` 提交；审核原文与任务书同 commit 入库。

## 5. 原始 gate 输出与复跑

```
$ py -3 tools/run_regression.py
T0 syntax  PASS (44 .py + 2 .js parsed clean)
T1 pytest  PASS (220 passed in 18.92s)
T2 parity  PASS (PARITY OK (32 files compared, allowlist=['.mcp.json']))
T2 artifacts PASS (ARTIFACTS OK (33 files compared, templates pinned))
GATE: PASS
```

Clean checkout 复跑：

```bash
pip install -r requirements-dev.txt -r v2_win/cc-communicate/server/requirements.txt
py -3 tools/run_regression.py                 # 或等价的 python3 调用（见下方说明）
py -3 tools/build_artifacts.py verify         # artifact 一致性
py -3 tools/build_artifacts.py generate && git diff --name-only v2_wsl   # 0-diff 不变量（输出为空）
```

说明：`py -3` 是 Windows launcher 写法；无 `py` 的环境用任一 Python 3.10+ 解释器执行同一脚本即可，gate 逻辑与解释器启动方式无关（审核方 §2.2 指出的环境问题已由 N-03 的 `requirements-dev.txt` 补齐依赖声明）。

## 6. 对审核方 §7 最终状态表的建议更新

| 对象 | 上轮状态 | 本轮建议 | 依据 |
|---|---|---|---|
| Wave 1–4 主体架构 | `ACCEPTED_IN_PRINCIPLE` | 不变（本轮未触碰） | AR-01~03 均为局部最小修复 |
| 当前实现 | `IMPLEMENTED` | 不变 | 220/220 + GATE PASS |
| 自动回归 | `VERIFIED_WITH_ENV_CONSTRAINT` | **`VERIFIED`** | AR-01 锁版 + fresh-interpreter gate，环境约束已消除 |
| Resume 通信能力 | `DEGRADED` | 不变（契约已对齐） | AR-04 选项 2：DEGRADED + spawn-fresh fallback + 重测条件 |
| HP-12 完整可观测性 | `DEFERRED` | 不变（契约已对齐） | AR-06：分阶段接受 + H1 替代观测面 + 重启条件 |
| 作为上层 Runtime 稳定依赖 | `REVISE_REQUESTED` | 二轮 `REVISE_REQUESTED`（RAR-01~04）→ **请求最终 `ACCEPTED`** | 门 1–7（§4）+ 二轮门 1–6（§8）证据齐备 |

## 7. 遗留事项（已记录，不阻断）

- **T46 重测**：CC 客户端更新后重跑 resume round-trip，是 L2 从 DEGRADED 回升 PASS 的既定升级路径；不涉及 cc-communicate 代码。
- **N-04**：默认 data-root 切换、backlog 按条数限制——按审核方意见继续延后。
- **`cc-communicate-marketplace/`**：已标注为历史参考，无同步计划（AR-05 方案 2）。
- **HP-12**：按 AR-06 重启条件待命（进入 H2/H3 或第一次真实无法定位的传输故障）。
- Wave 4 工具链小项（dead constants、missing-template 报错形式等）维持 ship-as-recorded，均已在此前审核记录中列明。

---

## 8. 二轮修订处置（RAR-01~04，对应重验收审核 §3）

| # | 审核方修订项 | 处置 | 证据 |
|---|---|---|---|
| **RAR-01** (P0) | 降级 cursor 结果不可组合（元数据进 cursor map，按文档传给 `listen_v2` 被 `validate_cursors` 拒为 INVALID_ARGUMENT） | `query_my_cursors` 返回**稳定 wrapper** `data = {cursors, degraded_stores}`（两路径同形，干净时 `degraded_stores = []`）；cursor map 永不含元数据；SKILL/工具 docstring 同步为传 `data.cursors` | `user_functions.py`；`mcp_server.py`；`SKILL.md`；新增**入口级组合测试**（降级结果 → `validate_cursors` 通过 → `listen_v2` 正常且降级可观察） |
| **RAR-02** (P1) | 重复 PID 重观察不刷新 recency（dict 更新保持旧位置 → 刚重观察的 PID 可能被裁掉 → false-dead） | `kernel.py`：重观察 PID **pop-then-reinsert**；start_time 仍只用于 PID 复用验证；无 schema 变更 | 2 测试：`1..8 → 1(重观察) → 9` 断言 1 在最近集合、仅 1 存活时 `check_alive == 1`；同序列持久化 replay |
| **RAR-03** (P1) | 权威 manifest 仍 0.3.0/"16 MCP tools"（实际 20 工具 + tag v0.4.0）；README 无 clean-checkout 安装路径 | `plugin.json` + `marketplace.json` → `0.4.0 / "Exposes 20 MCP tools"`；generate 同步双树；README 新增唯一安装/加载路径；新增防漂移 gate（版本/工具数/source） | `tests/unit/test_plugin_manifest_gate.py`（3 测试）；`claude plugin validate` ✔；`claude plugin details` 实测上报 `cc-communicate 0.4.0 ... Exposes 20 MCP tools` |
| **RAR-04** (P2) | 报告顶部事实过期（febc803/204/integration 分类）+ 提前声称通过 | 报告顶部规模表（终点 `a8927a0` + tag `v0.4.0`、测试 227、分类 unit+parity、含两轮验收审核）、§8 结论措辞（待最终 ACCEPTED）、§9.2 RAR 处置表更新；本 response 与重验收审核原文随交付 commit 入库 | completion report 各节 |

**二轮重验收门（审核方 §5，全部满足）**：①`query_my_cursors → listen_v2` 降级时按文档直接组合且降级不丢（RAR-01 组合测试）；②`1..8 → 1(重观察) → 9` + `check_alive` 回归含 replay（RAR-02）；③canonical manifest `0.4.0 / 20 tools`，regenerate 后 parity/artifact gate 通过（RAR-03）；④README 唯一安装/加载路径已 smoke（`claude plugin details`）；⑤报告 commit/测试数/分类/状态一致，response + 重验收审核进入交付 commit（RAR-04）；⑥220 + 新增 7 项全绿，T0/T1/T2 全 PASS（227 tests）。

---

**请求**：请审核方按重验收审核 §5 的 6 条最小门核验 §8 证据，给出最终 `ACCEPTED` / 进一步修订的判定。
