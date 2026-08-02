# cc-communicate 二轮修订交付 — 最终验收回应（Response to Re-acceptance Review）

> 回应日期：2026-08-03
> 回应对象：`docs/superpowers/reviews/2026-08-03-hardening-reacceptance-review.md`（第二轮，`REVISE_REQUESTED`，RAR-01~04）
> 交付标识：commit **`421a25e`**，annotated tag **`v0.4.0`** 已移至 `421a25e`（manifest 与 tag 一致），main 与 tag 均已推送 origin
> **本轮请求：按审核方 §5 的 6 条最小第三轮重验收门进行最终 `ACCEPTED` 判定**

## 1. 结论先行

第二轮审核发现的三个真实调用缺口（RAR-01~03）+ 一个文档项（RAR-04）全部完成处置。审核方已 `ACCEPTED` 的部分（AR-01/04/06、N-01~04）本轮未触碰；`ACCEPTED_IN_PART` 的两项（AR-02 cursor 组合、AR-03 PID recency）的残余缺口即 RAR-01/02，已闭环。Wave 1–4 主体架构不变，全部修复为局部最小改动并附带回归测试。

当前 gate 状态：**GATE PASS**（T0 syntax 44 .py + 2 .js；T1 pytest **227 passed**（220 + 7 新增）；T2 parity 32 files；T2 artifacts 33 files）。交付 commit `421a25e` 包含：代码修复 + 7 项新测试、双树 manifest 更正、README 安装路径、更正后的 completion report（§9.2 RAR 处置表）、重验收审核原文与一轮 response 文档。原始输出与复跑命令见 §4。本轮交付已经过开发侧独立审核（逐 diff + 全量 gate 重跑 + 插件 CLI 复验），审核记录见 §5。

## 2. RAR-01~04 处置

| # | 审核方发现 | 处置 | 证据 |
|---|---|---|---|
| **RAR-01** (P0) | `query_my_cursors` 远端失败时把 `degraded_stores` 塞进 cursor map；按文档传给 `listen_v2` 被 `validate_cursors` 拒为 `INVALID_ARGUMENT`——降级信息出现但返回值不可用 | 采纳审核方建议的**稳定 wrapper**：`data = {cursors: {store_id: seq}, degraded_stores: [...]}`，**两条路径同形**（干净时 `degraded_stores: []`，回应了"shape 分支必须非常明确"的风险提示）；cursor map 永不包含元数据。SKILL.md + 工具 docstring 同步改为"传 `data.cursors`"。调用方影响已核实：仅 mcp_server 入口 + 文档引用，无依赖旧 shape 的内部调用方 | `tests/unit/test_cursor_ack.py`：2 个 shape 测试改写 + **入口级组合测试** `test_query_my_cursors_degraded_composes_into_listen_v2`（降级结果 → `validate_cursors` 通过 → `listen_v2` 正常且 `degraded_stores` 仍可观察）——即审核方点名的验收方式 |
| **RAR-02** (P1) | 重复 PID 再写入不刷新 dict 位置：`1..8 → 1(重观察) → 9` 会把刚重观察的 1 当最旧项裁掉，仅 1 存活时 `check_alive == 0`（false-dead → 多余 resume/spawn） | `kernel.py _handle_start`：**pop-then-reinsert**（先移除旧 key 再按当前事件重新插入），recency 刷新；start_time 继续仅用于 PID 复用验证；无 schema 变更，旧 event log 可读 | `tests/unit/test_check_alive_fallback.py` 2 项：直接路径断言 `list(known) == [3..8, 1, 9]` 且仅 PID 1 存活时 `check_alive == 1`；同序列走持久化 replay（`process_session_ctrl_event`） |
| **RAR-03** (P1) | 权威 manifest 仍 `0.3.0`/"16 MCP tools"（实际 20 工具、tag v0.4.0）；README 无 clean-checkout 安装路径 | `plugin.json` + `marketplace.json`（win + wsl twin）→ `version 0.4.0` + "Exposes 20 MCP tools"；`build_artifacts.py generate` 同步双树（0-diff 不变量保持）。README 新增**唯一安装/加载路径**（directory-marketplace：`claude plugin marketplace add <v2_win>` → `install` → `list/details` 验证），即审核方"指向 canonical artifact 的最小权威 marketplace"方案，旧 marketplace 维持历史隔离。新增**防漂移 gate** `tests/unit/test_plugin_manifest_gate.py`（4 项：版本 == 0.4.0；工具数 == `mcp_server.py` 实计 `@mcp.tool` 数；marketplace source 指向 canonical 树；win/wsl twin 字节一致——repo 级 marketplace.json 在生成器镜像范围之外，twin 同步由此测试锁死）。**smoke**：`claude plugin validate` ✔；`claude plugin details` 上报 `cc-communicate 0.4.0 ... Exposes 20 MCP tools`（原 0.3.0/16） | 双 manifest；`README.md` 安装节；4 项 gate 测试；T51 smoke 记录 |
| **RAR-04** (P2) | completion report 顶部残留事实（终点 febc803 / 204 测试 / 含 integration 分类）+ 提前声称通过 | 报告顶部规模表更正（终点 `a8927a0` + tag `v0.4.0` + 二轮修订 commit、测试 **227**、分类 **unit + parity**（live gate 为手工证据）、外部审核含两轮验收）；§8 结论改为"最终 `ACCEPTED` 待第三轮重验收"；新增 §9.2 RAR 处置表；一轮 response 文档的门 6 错误陈述（"20 与 `.mcp.json` 一致"——`.mcp.json` 不枚举工具，权威 manifest 为 plugin.json）已在原文内更正；重验收审核原文 + 一轮 response 已随 `421a25e` 入库 | completion report §1.1/§4.1/§8/§9.2；`2026-08-03-reacceptance-response.md` §4/§8；`git show 421a25e --stat` |

## 3. 第三轮重验收门逐条证据（对应审核方 §5）

> **门 1**：`query_my_cursors → listen_v2` 在远端 store 降级时可直接按文档组合，且降级信息不丢。

入口级组合测试：构造"本地 cursor 成功 + 远端失败"→ 取 `data.cursors` 经公开入口 `validate_cursors` 通过（无 `INVALID_ARGUMENT`）→ `listen_v2` 正常返回，`degraded_stores == [HOST]` 仍可观察。RAR-01。

> **门 2**：`1..8 → 1(re-observed) → 9` 的 PID 顺序与 `check_alive` 回归测试通过，包含 replay。

直接路径：重观察后 1 移至末尾，裁剪最旧的 2，`list(known) == [3,4,5,6,7,8,1,9]`，仅 PID 1 存活时 `check_alive == 1`；持久化 replay 路径同序列验证。RAR-02。

> **门 3**：canonical manifest 为 `0.4.0 / 20 tools`，win/wsl regenerate 后 parity/artifact gate 通过。

双 manifest 已更正；防漂移 gate 4 项全绿；T2 parity 32 files + T2 artifacts 33 files PASS；0-diff 不变量成立（`generate` 后 `git diff v2_wsl` 为空）。RAR-03。

> **门 4**：canonical README 给出并 smoke 验证唯一安装/加载路径。

README "Install / load" 节给出从 clean checkout 的三步路径（marketplace add → install → list/details 验证）及 load smoke 判据（`my_session_id` 应答）。builder 执行 smoke：`claude plugin validate` ✔ + `claude plugin details` 上报 `0.4.0 / Exposes 20 MCP tools`（T51 记录）；开发侧审核独立复验 `claude plugin validate v2_win` 通过。RAR-03。

> **门 5**：completion report 的 commit、测试数、测试分类与验收状态修正；本 response 与本审核文档进入交付 commit。

报告 §1.1/§4.1/§8/§9.2 已更正（`a8927a0`+tag、227、unit+parity、ACCEPTED pending）；重验收审核原文 + 一轮 response 文档均在 `421a25e` 中。本二轮 response 文档将随下一 commit 入库。RAR-04。

> **门 6**：原 220 项测试继续通过；加上上述新测试后 T0/T1/T2 全绿。

227 passed = 220 + 7（组合 1 + recency 2 + manifest gate 4）；T0/T1/T2 全 PASS。原始输出见 §4。

## 4. 原始 gate 输出与复跑

```
$ py -3 tools/run_regression.py
T0 syntax  PASS (44 .py + 2 .js parsed clean)
T1 pytest  PASS (227 passed in 19.90s)
T2 parity  PASS (PARITY OK (32 files compared, allowlist=['.mcp.json']))
T2 artifacts PASS (ARTIFACTS OK (33 files compared, templates pinned))
GATE: PASS
```

Clean checkout 复跑（与一轮相同，无新增依赖）：

```bash
pip install -r requirements-dev.txt -r v2_win/cc-communicate/server/requirements.txt
py -3 tools/run_regression.py                 # 无 py 的环境用任一 Python 3.10+ 解释器
py -3 tools/build_artifacts.py verify         # artifact 一致性
py -3 tools/build_artifacts.py generate && git diff --name-only v2_wsl   # 0-diff（输出为空）
claude plugin validate v2_win                 # manifest 合法性
```

## 5. 开发侧独立审核记录（本轮新增）

本轮交付经开发侧高级审核逐 diff 核验，结论 **通过**：

- RAR-01/02 代码语义与测试钉住的顺序断言逐行核对一致；RAR-01 稳定 wrapper 的两路径同形设计正面回应了审核方的 shape 分支风险。
- RAR-03 防漂移 gate 的工具数断言以 `mcp_server.py` 实计 `@mcp.tool` 为真值来源（非文档声明）；twin-sync 测试识别并锁住了 repo 级 marketplace.json 在生成器镜像范围之外这一手工同步隐患。
- 全量 gate 重跑（227 passed）、0-diff 不变量、WSL twin 字节一致、`claude plugin validate` ✔、tag/远端同步（origin `v0.4.0` == local，指向 `421a25e`）均独立复验。
- 非阻断备注 1 条：completion report §4.1 测试数枚举"manifest gate 4、marketplace twin 1"字面相加为 8，实际 twin 同步测试是 manifest gate 4 项之一，总数 **227 = 220 + 7 经核实正确**——仅枚举措辞有歧义，不影响事实。

## 6. 对审核方 §7 最终状态表的建议更新

| 对象 | 上轮状态 | 本轮建议 | 依据 |
|---|---|---|---|
| Wave 1–4 主体架构 | `ACCEPTED_IN_PRINCIPLE` | 不变（本轮未触碰） | RAR 均为局部最小修复 |
| 干净依赖安装与自动 gate | `VERIFIED` | 不变 | 227/227 + GATE PASS 可复现 |
| Resume 通信能力 | `DEGRADED`（已如实交付） | 不变 | AR-04 契约维持；T46 重测为既定升级路径 |
| HP-12 完整可观测性 | `DEFERRED_ACCEPTED_FOR_H1` | 不变 | AR-06 契约维持 |
| 当前修订实现 | `REVISE_REQUESTED`（RAR-01~04） | **`IMPLEMENTED + VERIFIED`** | 六门证据齐备（§3） |
| 作为 Clever User H1 的正式稳定依赖 | `NOT_YET_ACCEPTED` | **请求 `ACCEPTED`** | 两轮 REVISE 的全部阻断项已闭环 |

## 7. 遗留事项（已记录，不阻断）

- **T46 重测**：CC 客户端更新后重跑 resume round-trip，是 L2 从 DEGRADED 回升 PASS 的既定升级路径；不涉及 cc-communicate 代码。
- **N-04**：默认 data-root 切换、backlog 按条数限制——按审核方意见继续延后。
- **HP-12**：按 AR-06 重启条件待命（进入 H2/H3 或第一次真实无法定位的传输故障）。
- **`cc-communicate-marketplace/`**：历史参考，无同步计划（AR-05）。
- Wave 4 工具链小项维持 ship-as-recorded（此前审核记录已列明）。

---

**请求**：请审核方按 §5 的 6 条重验收门核验上述证据，给出最终 `ACCEPTED` / 进一步修订的判定。

---

## 9. 三轮修订处置（FR-01~02，对应第三轮审核 §3）

第三轮重验收（`docs/superpowers/reviews/2026-08-03-hardening-reacceptance-round3-review.md`）判定：**Runtime 代码 `ACCEPTED`；可安装发布封装 `REVISE_REQUESTED_RELEASE_ONLY`**。两项发布封装修订完成如下：

| # | 审核方修订项 | 处置 | 证据 |
|---|---|---|---|
| **FR-01** (P0) | README clean-checkout 步骤缺 `server/requirements.txt` 安装（`.mcp.json` 直调系统 `python`，干净环境 MCP server 无法启动）；T51 smoke 只到 `validate/details`，未证明插件真实加载 | README 补齐**同解释器依赖安装步骤**（`python -m pip install -r v2_win/cc-communicate/server/requirements.txt` + `python -c "import mcp, psutil, filelock"` 验证；WSL `python3`；注明 Store stub 场景用 `py -3`/完整路径）。**真实 load smoke**（3 个 worker）：script-import 协调器 + 真实 spawn CC（bypass）→ `my_session_id` 返回自身 sid + worker 确认 "cc-communicate MCP server CONNECTED and fully functional"（4 个工具全部 ok）。最终态探针实证 worker MCP server 进程 cmdline 指向 **v2_win canonical**（注册表 update 后加载源不漂移，非 cache 副本） | `README.md`；`tested&2betest.md` T52（原始输出）；worker 1b4283f7 / 4a113f12 / 09571d6b；探针 pid 1624 cmdline |
| **FR-02** (P1) | 已推送 tag `v0.4.0` 被移动，不同消费者可能解析到不同代码 | `v0.4.0` **不再移动**（保留在 `421a25e` 作历史）；发布为新的**不可变版本 `v0.4.1`**：双 manifest → `0.4.1`；防漂移 gate `RELEASE_VERSION = "0.4.1"`；README 同步；`claude plugin update` 刷新注册表 → `list`/`details` 均报 `0.4.1 / 20 MCP tools`；新 annotated tag `v0.4.1` 一次性创建于最终交付 commit | 双 manifest；`tests/unit/test_plugin_manifest_gate.py`；`claude plugin list`/`details` 输出；tag v0.4.1 |
| 报告清理 | §4.1 枚举重复列 manifest gate 4 + twin 1（字面合计 8 ≠ 7）；二轮 response 未入库 | 枚举更正（manifest gate 3 + twin 1 = 7）；本文件（审核方引用名）+ 第三轮审核 + smoke 证据随最终交付 commit 入库；报告维持 ACCEPTED pending | completion report §4.1/§9.2/§9.3；git log |

**最终验收门（审核方 §5，全部满足）**：①README 含同解释器依赖安装步骤；②隔离 clean-checkout 安装后真实 CC `my_session_id` load smoke 通过（T52）；③不可变 `v0.4.1`（manifest/marketplace/details/tag 一致，v0.4.0 保留）；④版本 gate 更新后 227 项 + T0/T1/T2 继续全绿；⑤报告枚举修正 + 本 response + 第三轮审核 + smoke 证据进入最终 commit。

**请求**：请审核方核验 FR-01/02 与上述 5 条门，给出最终 `ACCEPTED` 判定。
