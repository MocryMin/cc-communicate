# cc-communicate 加固重验收审核（第二轮）

> 审核日期：2026-08-03  
> 审核对象：delivery commit `a8927a0`、tag `v0.4.0`、后续说明 commit `8be326f`，以及 `docs/superpowers/reviews/2026-08-03-reacceptance-response.md`  
> 审核依据：`docs/superpowers/reviews/2026-08-03-hardening-acceptance-review.md` §5 的 7 条最小重验收门  
> 审核视角：未来 Clever User / 上层 Agent Runtime 的稳定底层依赖  
> **总体决策：`REVISE_REQUESTED`（窄范围二次修订，不重开 Wave 1–4）**

## 1. 结论先行

本轮修订不是无效返工。AR-01、AR-04、AR-06 已满足；AR-02 与 AR-03 的主要故障已修复；220 项自动测试与全部静态/产物 gate 可独立复现。

但当前交付仍不能标为最终 `ACCEPTED`，因为独立审核发现三个会影响真实调用的缺口：

1. `query_my_cursors` 在远端 store 不可达时，把 `degraded_stores` 塞进原本应当是纯 `{store_id: sequence}` 的 cursor map。文档又要求把该结果直接传给 `listen_v2`；公开入口的 `validate_cursors` 会立即以 `INVALID_ARGUMENT` 拒绝它。也就是说，AR-02 的“诚实降级”信息虽然出现了，但其返回值不能按公开契约继续使用。
2. `known_pids` 改用 dict 插入序后解决了 `None`/浮点混排崩溃，但重复 PID 的再次写入不会刷新 dict 顺序。一个刚刚重新观察到的 PID 仍可能在下一次裁剪时被当成最旧项删除，使 `check_alive` 漏掉仍存活的 fallback PID。现有“PID 重复”测试没有覆盖“旧 PID 被重新观察后再加入新 PID”的顺序。
3. 权威 artifact 的 `.claude-plugin/plugin.json` 仍声明 `version: 0.3.0` 和 `16 MCP tools`，实际代码有 20 个工具，release tag 是 `v0.4.0`。此外 canonical README 没有给出从 clean checkout 实际加载/安装该权威 artifact 的步骤。因此 AR-05 的“manifest、版本/build identity、唯一可安装入口一致”尚未满足。

这些都可以用小型局部修订解决；无需改变消息协议、Wave 架构或重新执行完整设计流程。

## 2. 独立复验结果

### 2.1 已通过

审核方创建全新隔离环境，仅安装：

```text
requirements-dev.txt
v2_win/cc-communicate/server/requirements.txt
```

实际解析并验证：

```text
mcp=1.29.0
FastMCP import=PASS
MCP stdio server process successfully entered its run loop

T0 syntax     PASS (44 .py + 2 .js)
T1 pytest     PASS (220 passed, 1 warning)
T2 parity     PASS (32 files)
T2 artifacts  PASS (33 files)
GATE          PASS
```

第一次 pytest 运行因审核沙箱禁止写系统默认临时目录而出现 `PermissionError`；将 `TEMP/TMP` 指向项目内专用临时目录后，220 项全部通过。该现象属于审核环境约束，不计为产品失败。

同时确认：

- `v0.4.0` 是 annotated tag，正确指向 `a8927a0`。
- win/wsl canonical artifact 当前 parity 通过。
- L2 已统一写为 `DEGRADED (T46)`，并提供 spawn-fresh fallback；未再把通信恢复写成 PASS。
- HP-12/G4 已统一标记为分阶段延后，并给出 H1 替代观测面及 H2/H3 再入条件。

### 2.2 未被现有测试发现的复现证据

#### A. 降级 cursor 结果不可组合

当前 `query_my_cursors` 的远端失败分支生成类似：

```python
{
    "local-store": 6,
    "degraded_stores": ["host-store"],
}
```

将该对象按 SKILL/工具 docstring 的要求作为 `cursors` 传入公开 `listen_v2`，会进入 `validate_cursors` 并得到：

```text
InvalidArgumentError: INVALID_ARGUMENT: store_id must be ...; got 'degraded_stores'
```

证据位置：

- `v2_win/cc-communicate/server/user_functions.py`：`query_my_cursors` 将 `degraded_stores` 写入 `out`。
- `v2_win/cc-communicate/server/validation.py`：`validate_cursors` 要求 map 的每一项都是合法 store id 到非负整数。
- `v2_win/cc-communicate/skills/cc-communicate/SKILL.md` 与 `server/mcp_server.py`：要求把 `query_my_cursors` 结果传给 `listen_v2`。

#### B. 重复 PID 的 recency 没有刷新

复现序列：先记录 PID `1..8`，再重新记录 PID `1`，最后记录 PID `9`。按“最近 8 个 PID”语义，PID 1 应保留；当前实现实际得到：

```text
known_pids = [2, 3, 4, 5, 6, 7, 8, 9]
```

若此时只有 PID 1 仍存活，独立调用结果为：

```text
check_alive = 0
```

原因是 Python dict 对既有 key 再赋值不会把它移动到末尾。当前测试里的重复 PID 第一次出现时已经位于末尾，因此没有暴露该问题。

#### C. 发布 manifest 与交付声明不一致

当前两个权威 artifact 的 manifest 均为：

```json
{
  "description": "... Exposes 16 MCP tools.",
  "version": "0.3.0"
}
```

而 `server/mcp_server.py` 实际有 20 个 `@mcp.tool()`，tag 为 `v0.4.0`。重验收回复中“20 与 manifest 一致”的陈述因此不成立；`.mcp.json` 只声明 server 启动命令，并不枚举工具数，不能充当工具 manifest 证据。

## 3. 第二轮必须修订项

### RAR-01 — 让 cursor 降级元数据与 cursor map 可组合

- **Decision**：`REVISE`
- **必要性**：P0 契约缺口。它发生在系统最需要降级恢复信息的时候；上层按文档继续调用反而会得到参数错误。
- **结构可行性**：高。必须保持“不把元数据伪装成 cursor”这一不变量。可选择稳定 wrapper（例如 `{cursors, degraded_stores}`）并明确迁移，或在 partial failure 时返回结构化 retryable error，把 partial cursors 与 degraded stores 放入独立字段；具体方案由 builder 结合兼容性决定。
- **实现/插入难度**：S–M，集中在 `query_my_cursors`、公开 docstring/SKILL、少量调用方和测试。
- **风险**：修改 clean-path shape 会影响已有调用者；若选择只在 degraded path 使用 wrapper，也必须让 shape 分支非常明确。不要仅靠文档要求模型手工删除保留字段。
- **验收**：新增一项公开入口级组合测试：构造“本地 cursor 成功 + 远端失败”，取得 `query_my_cursors` 结果，然后严格按文档传给下一次 `listen_v2`；不得得到 `INVALID_ARGUMENT`，且降级 store 仍可观察。

### RAR-02 — 重复 PID 必须刷新 recency

- **Decision**：`REVISE`
- **必要性**：P1，属于 AR-03 未完整覆盖。它不会再导致 TypeError，但会制造 false-dead，进而诱发不必要的 resume/spawn 与重复 worker 风险。
- **结构可行性**：高。更新既有 PID 前先移除旧 key，再按当前事件重新插入；或显式维护有界 recency 结构。start_time 继续只用于 PID 复用验证。
- **实现/插入难度**：S。
- **风险**：必须保持旧 event log 可读和最多 8 项；不要引入 schema 迁移。
- **验收**：新增序列 `1..8 → 1(re-observed) → 9`，断言 1 仍在最近集合；仅 PID 1 存活时 `check_alive == 1`。同一序列再走一次持久化 replay 路径。

### RAR-03 — 完成真正一致且可执行的发布面

- **Decision**：`REVISE`
- **必要性**：P1，AR-05 的明确重验收门尚未满足。上层需要能确认加载的是哪一版、包含多少工具，并能从 clean checkout 实际启用它。
- **结构可行性**：高。编辑 canonical `v2_win` manifest，再生成 WSL artifact；将版本改为 `0.4.0`、工具数改为 20。canonical README 增加唯一受支持的依赖安装与插件加载/启动方式；旧 marketplace 继续保持历史隔离即可。
- **实现/插入难度**：S。
- **风险**：仅改文字而不给可执行安装方式，仍不能闭环；若 Claude Code 的受支持入口是 `--plugin-dir`，应明确写出并实际 smoke；若必须经过 marketplace，则应建立一个指向 canonical artifact 的最小权威 marketplace，而不是复活旧源码副本。
- **验收**：manifest 版本 `0.4.0`、描述 20 tools、tag/build identity 一致；从 clean checkout 严格按 README 操作，MCP server 可见且 `my_session_id` 可调用；增加 manifest/version/tool-count 防漂移 gate。

### RAR-04 — 修正最终报告中的残留事实

- **Decision**：`REVISE`（文档项，可随 RAR-03 一次完成）
- **必要性**：P2。completion report 顶部仍写终点 `febc803`、自动测试 204，并将测试分类写成含 integration；实际交付为 `a8927a0`/后续修订、220 项，仓库自动测试目录为 `unit` 与 `parity`，live gate 是手工证据。
- **结构可行性/难度**：高/S。
- **风险**：报告继续提前声称“最终外部审核已通过”会让后续追踪混淆；应在真正 ACCEPTED 后再写最终状态。
- **验收**：顶部规模表、测试分类、交付 commit/tag 和最终状态与仓库事实一致；本轮 response 与重验收审核一并进入下一交付 commit。

## 4. 本轮已接受的处置

| 项目 | 判定 | 说明 |
|---|---|---|
| AR-01 MCP 主版本锁定 | `ACCEPTED` | 全新环境解析到 MCP 1.29.0，导入和 server run-loop 均通过 |
| AR-02 传输故障不再伪装为空成功 | `ACCEPTED_IN_PART` | 本地/远端故障可区分；但 cursor 降级返回值不可组合，见 RAR-01 |
| AR-03 None/混合 start_time 崩溃 | `ACCEPTED_IN_PART` | 崩溃已消除；重复 PID recency 仍错误，见 RAR-02 |
| AR-04 Resume/L2 重分类 | `ACCEPTED` | DEGRADED + spawn-fresh fallback + 重测条件一致 |
| AR-05 发布面 | `REVISE` | tag 正确；canonical manifest/安装闭环未完成 |
| AR-06 HP-12/G4 分阶段契约 | `ACCEPTED` | H1 替代观测面和再入条件明确 |
| N-01～N-03 | `ACCEPTED` | 降级细节、stderr、开发依赖均已落实 |
| N-04 | `DEFERRED_ACCEPTED` | 继续不阻断 H1 |

## 5. 最小第三轮重验收门

无需重跑 live L1–L7，也无需重开 14 条加固 proposal。Builder 完成下列证据即可请求最终验收：

1. `query_my_cursors → listen_v2` 在远端 store 降级时可直接按文档组合，且降级信息不丢。
2. `1..8 → 1(re-observed) → 9` 的 PID 顺序与 `check_alive` 回归测试通过，包含 replay。
3. canonical manifest 为 `0.4.0 / 20 tools`，win/wsl regenerate 后 parity/artifact gate 通过。
4. canonical README 给出并 smoke 验证唯一安装/加载路径。
5. completion report 的 commit、测试数、测试分类与验收状态修正；本 response 与本审核文档进入交付 commit。
6. 原 220 项测试继续通过；加上上述新测试后 T0/T1/T2 全绿。

## 6. 对 Clever User H1 的影响

- **可以继续纸面设计和上层 harness 设计**：AR-01、主要传输错误诚实性、spawn-fresh 路线已成立。
- **暂不宣布 cc-communicate 最终加固完成**：RAR-01 会影响上层在跨 store 故障时的恢复动作；RAR-03 会影响它是否加载到正确版本。
- **H1 若必须提前做实验**：可限定为单机、spawn-fresh、确认加载 canonical 源码，并把结果标为 pre-acceptance exploratory run；不要把它计入正式 H1 生死线结果。

## 7. 最终状态表

| 对象 | 状态 |
|---|---|
| Wave 1–4 主体架构 | `ACCEPTED_IN_PRINCIPLE` |
| 干净依赖安装与自动 gate | `VERIFIED` |
| Resume 通信能力 | `DEGRADED`（已如实交付） |
| HP-12 完整可观测性 | `DEFERRED_ACCEPTED_FOR_H1` |
| 当前修订实现 | `REVISE_REQUESTED`（RAR-01～04） |
| 作为 Clever User H1 的正式稳定依赖 | `NOT_YET_ACCEPTED` |

