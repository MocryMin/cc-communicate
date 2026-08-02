# cc-communicate 加固第三轮重验收审核

> 审核日期：2026-08-03  
> 审核对象：commit `421a25e` 与 `docs/superpowers/reviews/2026-08-03-reacceptance-round2-response.md`  
> 审核依据：上一轮审核 §5 的 6 条最小第三轮重验收门  
> **总体决策：`REVISE_REQUESTED_RELEASE_ONLY`**  
> **代码判定：`ACCEPTED`；可安装发布判定：`NOT_YET_ACCEPTED`**

## 1. 结论先行

RAR-01 与 RAR-02 的真实运行缺口已正确修复，RAR-03 的 manifest 漂移也已修复。审核方在全新隔离依赖环境中复跑 227 项测试和全部 T0/T1/T2 gate，结果全绿。因此，不再要求修改消息协议、cursor 实现、PID recency、Wave 1–4 架构或重跑 L1–L7。

当前只剩第三轮门 4——“从 clean checkout 按 README 安装并实际加载”——没有完成：

1. canonical README 的 clean-checkout 步骤没有安装 `server/requirements.txt`。但 `.mcp.json` 直接调用系统 `python`/`python3` 启动 server；在真正干净的 Python 环境中，只执行 README 的三步插件命令会因缺少 `mcp`、`psutil`、`filelock` 而无法启动 MCP server。
2. T51 记录的 smoke 是 `claude plugin validate` 与 `claude plugin details`。它们证明 marketplace/manifest 可解析，却没有启动 MCP server，也没有获得 README 自己定义的 `my_session_id` 应答。因此“插件元数据可见”被误当成了“插件已加载可用”。
3. `v0.4.0` 此前已推送并指向 `a8927a0`，本轮又被移动到 `421a25e`。同一个已推送 tag 对不同消费者可能解析为不同代码，不符合 AR-05 要求的不可混淆 build identity。

这是发布封装问题，不是 runtime 实现问题。完成下面两个小项后即可最终 `ACCEPTED`。

## 2. 独立复验证据

在全新隔离 Python 环境中，仅安装仓库声明的开发与运行依赖后：

```text
mcp=1.29.0

T0 syntax     PASS (44 .py + 2 .js)
T1 pytest     PASS (227 passed, 1 warning)
T2 parity     PASS (32 files)
T2 artifacts  PASS (33 files)
GATE          PASS
```

代码检查同时确认：

- `query_my_cursors` 两条成功路径均返回稳定 `{cursors, degraded_stores}` wrapper。
- 文档要求传递 `data.cursors`，cursor map 不再混入元数据。
- `1..8 → 1(re-observed) → 9` 通过 pop-then-reinsert 正确刷新 recency；直接路径和 replay 均有测试。
- canonical plugin/marketplace manifest 已统一为 `0.4.0 / 20 tools`；实际 `@mcp.tool()` 数量为 20。
- win/wsl parity 与 artifact generation 不变量保持通过。

当前审核环境没有 `claude` CLI，因此无法代替 builder 独立复跑真实 CC load smoke；但这不改变 T51 证据本身只执行到 `validate/details`、没有执行 `my_session_id` 的事实。

## 3. 最后必须修订项

### FR-01 — 补齐 clean-checkout 依赖安装与真实 load smoke

- **Decision**：`REVISE`
- **必要性**：P0 发布门。当前 README 的步骤可以安装插件元数据，但不能保证 MCP server 启动。
- **结构可行性**：高。Windows 安装步骤在 marketplace add 之前加入 `python -m pip install -r v2_win/cc-communicate/server/requirements.txt`；WSL 使用 `python3 -m pip ...`。明确“必须安装到 `.mcp.json` 中 command 实际解析到的同一个解释器”。
- **实现/插入难度**：S，仅文档与一次 live smoke。
- **风险**：若 shell 中的 `python` 与 Claude Code 启动时 PATH 不同，即使安装过依赖仍会失败；smoke 必须从真正的 CC plugin session 调用，而不是单独 import Python 模块。
- **验收**：在隔离/可恢复的 Claude 配置中严格从 clean checkout 按 README 执行；新 CC session 中 `my_session_id` 返回结构化 `ok` 与 sid，并在 `/mcp` 或等价状态中确认 server connected。记录原始结果，测试后恢复外部配置。

### FR-02 — 使用不可变的新发布身份

- **Decision**：`REVISE`
- **必要性**：P1。已推送 tag 被移动会让缓存、旧 clone 与新 clone 对“v0.4.0”产生不同理解。
- **结构可行性**：高。不要再次移动 `v0.4.0`；将最终发布版本设为 `v0.4.1`（或下一未使用版本），同步 plugin/marketplace manifest、防漂移测试、README 和完成报告，再创建一次新的 annotated tag。
- **实现/插入难度**：S。
- **风险**：硬编码 `RELEASE_VERSION = "0.4.0"` 的测试需要同步；这正是它应当捕获的 release 变更。
- **验收**：新 tag 只创建一次并指向最终交付 commit；manifest、marketplace、CLI details 与 tag 均为同一版本。保留旧 `v0.4.0` 的现状作为历史记录，不再 force-move。

## 4. 非阻断的报告清理

- completion report §4.1 的新增测试枚举把“manifest gate 4”与其中的“marketplace twin 1”重复列出，字面合计错误；总数 227 本身正确。修正文案即可。
- 当前二轮 response 文件尚未入库；应与本审核、最终 load smoke 记录一并进入最终交付 commit。
- 最终审核通过前，completion report 保持“ACCEPTED pending”；通过后再更新为最终状态。

## 5. 最小最终验收门

Builder 不需继续修改 runtime，也不需重跑 live L1–L7。只需交付：

1. README 包含同解释器运行依赖安装步骤。
2. 隔离 clean-checkout 安装后，真实 CC session 的 `my_session_id` load smoke 通过。
3. 发布为新的不可变版本（建议 `v0.4.1`），manifest/marketplace/details/tag 一致。
4. 版本 gate 更新后，227 项基线与 T0/T1/T2 继续通过。
5. 修正报告枚举，并把 round2 response、本审核和 smoke 证据纳入最终 commit。

## 6. 当前状态

| 对象 | 状态 |
|---|---|
| Wave 1–4 架构 | `ACCEPTED_IN_PRINCIPLE` |
| Runtime 代码 | `ACCEPTED` |
| 227 项自动 gate | `VERIFIED` |
| Resume | `DEGRADED`（如实交付，H1 使用 spawn-fresh） |
| HP-12 | `DEFERRED_ACCEPTED_FOR_H1` |
| 可安装发布封装 | `REVISE_REQUESTED_RELEASE_ONLY` |
| 作为 Clever User H1 正式依赖 | `PENDING_FINAL_RELEASE_SMOKE` |

