# cc-communicate 加固提案审核意见（cc-builder Review）

> 日期：2026-07-24
> 审核对象：`plans/2026-07-24-cc-communicate-hardening-proposals.md`（HP-00～HP-13）
> 审核人：cc-builder（项目负责者 / 最懂当前实现的 session）
> 审核依据：对 `v2_win/` 当前实现的三路并行代码核实（message/ACK、RPC/kernel、spawn/connect/hook/validation）；`v2_wsl/` parity 一致。
> 性质：首轮 review（按提案 §6 要求，不直接大规模实现）。所有 Decision 待上层规划者 ACCEPT/REVISE 后方可进入 build。

---

## 0. 总体意见

这是一份**高质量、可执行**的加固提案。作为负责者我给出三点总体判断：

1. **边界划分正确且被一致遵守。** §0 把 cc-communicate 限定为「身份/发现/存活 + Worker 创建恢复定位 + 跨 realm 路由 + 持久可重投可诊断传输 + 传输级生命周期」，并明确把 PlanGraph/Probe/Evidence 语义/git checkpoint/上层重规划挡在门外。逐条核对，HP-01～HP-13 全部遵守该边界：`kind` 保持传输通用、connect/close 归为传输生命周期、`artifact_refs` 不含领域解释、cursor 是传输 ACK 而非任务完成。**这一点我完全认可，是整套提案不偏离轨道的根基。**

2. **交付语义诚实。** 「at-least-once + per-store ordering + detectable duplicates + idempotent mutation」，明确**不承诺**崩溃条件下严格 exactly-once。这与文件系统传输 + 单线程 kernel 的现实完全匹配，是正确的工程取舍。我反对任何把它升级成 exactly-once 的尝试——那会把复杂度推到我们无法对上层负责的程度。

3. **波次划分符合 §1.3 实施纪律。** Gate 0 先固化基线、Wave 1 消静默丢失/错误 ACK/重复副作用/路径风险、Wave 4 才动源码布局——这与「不在同一个未验证大提交里同时重写协议+路由+布局」的纪律一致。**我接受整体波次结构。**

**结论：14 条全部 ACCEPT（无 REJECT），其中 5 条带具体修订意见，4 个决策点交上层规划者裁定（见 §6.7）。** 我的一条横切要求：所有触及持久格式/协议字段的改动，必须同步落到 `v2_win/` 与 `v2_wsl/` 两域，并由 HP-13 的 parity gate 兜底。

---

## 1. 事实核查：提案对当前实现的描述 vs 我核实的结果

提案对现状的诊断**基本全部属实**，以下是需要**修正或加重**的点（这些直接影响个别 proposal 的工作量与表述）：

| # | 提案描述 | 核实结果（file:line） | 影响 |
|---|---|---|---|
| C1 | 消息「直接写最终文件，可能观察到未写完」 | 确认且更重：`kernel_api.py:84-85` 普通 `open(path,"w")` 截断写，无 tmp/fsync/rename。已有 `_atomic_write_json`（tmp+`os.replace` 但**无 fsync**，`kernel_api.py:34-38`）从不用于消息 | HP-01 必要；fsync 是**新增**能力（现有 helper 也没有） |
| C2 | 同毫秒同方向 send 覆盖 | 确认：文件名唯一性仅 `ts+from+to`（`conversations.py:53-56`），同毫秒同向产生同名文件被截断 | HP-01 核心动机，坐实 |
| C3 | 「现有 conversation 目录已预留 `info.json` 概念」 | **失实**：`info.json` 不存在，仅注释占位（`conversations.py:9`、`kernel_api.py:92`）；`query_conversations` 每 partner 返回空 dict（`kernel_api.py:104`） | HP-05 是**新建** connection metadata，非扩展，工作量略升 |
| C4 | connect reply 靠 timestamp 过滤 | 确认且更脆：`hello_ts` 从 `"message_sent at <ts>"` **字符串解析**（`user_functions.py:382-385`），解析失败 `hello_ts=0` → **任意 target 消息都被当 reply**（`user_functions.py:186-205`） | HP-05 必要性加重；同时佐证 HP-07「不得解析字符串控流程」 |
| C5 | session id 参与路径/删除且无校验 | 确认且严重：全仓**零集中校验**；`..`/`/`/`\`/`__` 直入 `os.path.join`/`makedirs`（`conversations.py:30-34`）；`withdraw(init_connect=1)` → `shutil.rmtree`（`kernel_api.py:109-114`）。且 `registrar.js:47` sanitize 事件文件名，kernel/conversations 不 sanitize——**同一 sid 两处口径不一** | HP-06 必要性加重；需统一口径 |
| C6 | RPC retry 重复副作用 | 确认：retry 用**新 request_id** 且原请求残留队列（`rpc_client.py:73-90,161-173`）；`drain_queue` **无 dedup**（`kernel.py:189-220`）；`send_message`/`evoke` 非幂等 | HP-03 核心动机，坐实 |
| C7 | registration 使 kernel 常驻 | 确认：`alive_conversations` 非空即无条件阻止 idle exit（`kernel.py:278-279`） | HP-08 动机，坐实 |
| C8 | spawn 无法注入关联 token | 确认：`spawn.py` 不传 `env=`；但 hook 继承 CC 环境、`cmd /c start` 默认继承环境 → env→hook→SessionStart 链路**很可能可行**（registrar 一行改动） | HP-04 可行，但需 live probe（§6.4） |
| C9 | data 根绑定插件目录、无 schema version | 确认：`DATA_DIR=PLUGIN_ROOT/data`（`paths.py:17`）；全仓**无 `schema_version`**；`machine_identity` 把 `data_dir` 发布给 peer（`machine_identity.py:128-146`）→ 移动后 peer 记录失效 | HP-11 动机，坐实 |
| C10 | （提案未强调）data 根解析有**两处** | `paths.py`（Python）**和** `scripts/lib/paths.js`（registrar 写 `data/session_ctrl`） | HP-00/HP-11 的 override 必须**双边同步**，否则 kernel 与 hook 对 data/ 位置分歧 |

---

## 2. 逐条审核（按 §1.2 必填格式）

---

### HP-00 Review — 自动化基线与故障注入测试

- **Decision: ACCEPT**
- **Rationale:** 这是一切的前置。我们即将触碰消息格式/ACK/RPC retry/spawn/路由，没有可重复基线，修一个竞态极易引入另一个。提案「只固化外部契约与已确认不变量、不固定内部轮询次数/日志文本」的原则正确，避免把测试写成锁死错误实现的枷锁。
- **Conflicts with current implementation:** 无代码冲突；冲突在「测试不得写真实 `data/`」——当前 data 根硬编码于插件目录。提案选项 1（`CC_COMMUNICATE_DATA_DIR`）正好解决，且被 HP-11 正式采用，是正确的协同。
- **Proposed revision:** 选项 1 的 override **必须同时加到 `paths.py` 和 `scripts/lib/paths.js`**（见 C10）。否则测试/自定义 data root 下，Python kernel 与 JS registrar 会写不同位置。这是提案未充分强调、但我核实到的硬约束。
- **Files/modules expected to change:** 新增 `tests/{unit,integration,live}/`；`server/paths.py`；`scripts/lib/paths.js`；pytest 配置；kernel fixture（依赖注入 data root）。
- **Tests to add:** 提案 §「首批必须固化的 v0.3 行为」六条全部纳入；integration 覆盖 kernel restart / cancel redelivery / concurrent senders / dual-store cursor。
- **Compatibility impact:** 纯增量；`CC_COMMUNICATE_DATA_DIR` 缺省时行为不变。零破坏。
- **Residual risks:** 测试误锁内部实现细节（靠提案原则规避）；Windows 专属测试在 CI 不可用（单元测试跨平台、真实 CC/WSL 留作 live gate 规避）。

---

### HP-01 Review — 版本化消息记录、message_id、单调 sequence、原子发布

- **Decision: ACCEPT**
- **Rationale:** 直接消除 PB-1（同毫秒覆盖）/PB-2（时钟回拨）/PB-3（跨 realm 时钟差）三个已接受风险，以及 C1 的非原子写。设计——per-store 单线程 kernel 分配持久单调 sequence 作排序/ACK 单位、UUID `message_id` 作端到端去重单位、`created_at_ms` 降级为仅展示、tmp+fsync+`os.replace` 原子发布——与「mutation 已集中经单线程 kernel」的现实天然契合，无需跨 kernel 全局计数器，也无需 SQLite 前置。这是正确且克制的方案。
- **Conflicts with current implementation:** 文件名与消息体格式全变（`<ts>__f__t.md` 纯文本 → `<seq:020d>__<message_id>.json` 信封）；`listen_scan` 的 ts 来源从「解析文件名」变为「读记录 sequence」。
- **Proposed revision:**
  1. 持久计数器 `message_sequence.json` 的写入本身必须原子（复用并**升级** `_atomic_write_json` 补上 fsync——现有 helper 无 fsync，见 C1）。
  2. 分配顺序「先持久化 counter 再发布消息」（提案步骤 2→3）正确，允许 gap、绝不复用，我背书。
  3. **跨 realm 原子性必须 live 验证**：`os.replace` 在 `/mnt/c`(DrvFs) 与 `//wsl.localhost`(9P) 上的 rename 原子性一般成立，但 fsync 持久性较弱/不可预测。正确性关键是「reader 永不见 partial」（rename 原子性保证），崩溃后持久性由持久 counter + message_id 去重兜底——这点提案已列，我强调其为 Wave 1 的 live gate。
  4. 建议把「record schema + 原子发布」收进独立小模块（如 `server/message_record.py`），避免 `conversations.py` 继续膨胀。
- **Files/modules expected to change:** `server/conversations.py`（新 record、文件名 parse/format、双 reader）；`server/kernel.py`（sequence state 加载/保存）；`server/kernel_api.py::send_message`（分配/封装/原子发布）；`server/user_functions.py`（兼容返回与路由）；`server/paths.py`（sequence 路径）；（建议新增 `server/message_record.py`）。**两域同步。**
- **Tests to add:** `test_message_record.py`——1000 次同毫秒同向 send 无覆盖无丢失；时钟回拨仍按 sequence 有序；唯一 message_id；reader 不读 partial JSON；counter 崩溃 gap 不复用；legacy `.md` 可读且迁移有测试。
- **Compatibility impact:** **高**（消息格式变更），靠双 reader（旧 `.md` + 新 `.json` 并存至少一个版本）+ writer 只写新格式 + 明确 deprecation window 管理。**旧 ACK 不得静默把 timestamp 当 sequence**——我背书此条，旧消息进 legacy namespace，新 cursor 从显式迁移点起算。
- **Residual risks:** 格式迁移影响旧 listener（双 reader + deprecation 缓解）；counter state 损坏（启动扫现存最大 sequence 自愈）；跨 realm rename/fsync 语义差（live gate 验证）。

---

### HP-02 Review — per-store cursor 替代单一 timestamp watermark

- **Decision: ACCEPT（API 选项选 A，理由如下；B 为备选）**
- **Rationale:** PB-3 坐实——单 timestamp watermark 在两个时钟不一致的 store 间会把「未真正交付」的消息错误归档。per-store cursor + 归档规则 `message.store_id==S AND message.sequence<=cursors[S]`（不跨 store 比 sequence、不用 `created_at_ms`）是正确解。可行性论据核实为真：WSL caller 本就分别调 local 与 host 的 `listen_scan`，store 边界在代码里已存在，改动是「保留两个 scan 的独立 cursor」而非「取一个最大 timestamp」，不动现有跨机器路由决策。
- **Conflicts with current implementation:** `ack_timestamps.json` 由 flat `{sid: ts}` 变为带 `schema_version` 的 per-session per-store cursor map；`listen` 返回从单一 `watermark` 变为 `next_cursors`。
- **Proposed revision（API 选项论证）:** 我**选 A**（新增 `listen_v2(cursors=...)` + `query_my_cursors()`，旧 `listen` 保留一个 deprecation release），而非 B。
  - **关键论据（B 的 footgun）：** B 让同一 `listen` 工具按「是否传 cursors」切换语义。一次对话中，若调用方某次传了 cursors（v2）、下一次忘传，B 会**静默降级**回 legacy timestamp 模式——PB-3 立刻回归，且无报错。上层 Runtime 是 greenfield，本就按新 API 编写，给一个**无歧义的版本化工具**比「少一个工具名」更有价值。
  - A 让协议边界显式、legacy 路径清晰围栏。若上层规划者把「最小工具数」置于首位，B 可接受，但须接受上述降级风险并在 SKILL 中强制「一旦用 cursors 不可回退」。
  - 我**反对** C（直接破坏式升级现有 `listen`）：在尚无大调用基数时也没必要，A 已给出干净迁移。
- **Files/modules expected to change:** `server/kernel.py`（cursor map 加载/持久化）；`server/kernel_api.py::listen_scan/query_ack_timestamp/upload_ack_timestamp`（升级为 store cursor）；`server/user_functions.py::listen/close_connection`（分 store 扫描合并消息、**不合并 cursor 数值**）；`server/mcp_server.py`（`listen_v2`/`query_my_cursors`）；`SKILL.md`。**两域同步。**
- **Tests to add:** `test_cursor_ack.py`——两 store 用完全不同伪造时钟仍无跨 store 误归档；ACK store A 不影响 B；listen 取消本批可重投；cursor 跨 kernel restart 恢复；重复提交幂等；提交更小 cursor 不得回退；legacy API 支持期与迁移行为有记录。
- **Compatibility impact:** **高**（ACK 协议变更），靠选项 A 的版本化工具 + 一个 release 的 `listen` legacy wrapper + `query_my_cursors` 恢复管理。cursor ACK 语义须写清「仅代表传输层持久接收，不代表上层任务完成」（提案已述，背书）。
- **Residual risks:** CC 忘带完整 cursor map（`query_my_cursors` 恢复 + 上层确定性客户端负责 ACK）；一次返回多条但模型只处理部分（默认整批不确认，必要时加显式 `ack_through(store_id, sequence)`）；旧 timestamp state 无法无损转换（保留 legacy namespace，不猜测性转换）。

---

### HP-03 Review — RPC operation_id、领域幂等与安全重试

- **Decision: ACCEPT**
- **Rationale:** C6 坐实——retry 用新 request_id、原请求残留、kernel 无 dedup，`send_message`/`evoke` 恰好是会产生用户可见重复副作用的操作。设计——区分 `request_id`（单次传输尝试）与 `operation_id`（跨 retry 的逻辑操作稳定身份）、为每种 mutation 定义领域幂等键、维护有 TTL/容量上限的 operation journal——**恰当地没有**用一个庞大事务系统包住整个 kernel，而是把幂等锚定在领域对象（HP-01 的 message_id、HP-04 的 spawn_token）上。这是正确的克制。
- **Conflicts with current implementation:** RPC payload 增加 `operation_id` 字段；`drain_queue` 增加 journal 查/记。旧 kernel 的 `_dispatch` 只读 `req["function"]/req["args"]`，**会忽略未知字段**——故前向兼容，无冲突。
- **Proposed revision:** crash-window 幂等边界见 §6.3（诚实声明）。补充：`send` 的领域幂等依赖 HP-01 的 message_id、`spawn` 依赖 HP-04 的 spawn_token，因此 **Wave 1 的 HP-03 落地范围 = operation_id + journal + 天然幂等的 mutation（register/unregister/create_folder/cursor-max/withdraw-by-id）+ 经 HP-01 的 send 去重；spawn/evoke 去重在 Wave 2 随 HP-04 完成**。这与依赖图（HP-03→HP-04）一致，不视为缺口，但要在报告里写明。
- **Files/modules expected to change:** `server/rpc_client.py::_submit/call/_submit_remote/call_remote`（携带并跨 retry 复用 operation_id）；`server/kernel.py::drain_queue/_dispatch`（journal）；`server/kernel_api.py` 各 mutation（幂等键）；（建议新增 `server/operation_journal.py`）。**两域同步。**
- **Tests to add:** `test_rpc_idempotency.py`——人为丢弃首个 response，retry 不产生第二条消息；同 spawn operation retry 不建第二个 CC（Wave 2 补）；local 与 remote 均携稳定 operation_id；kernel restart 后仍可按 message_id/spawn_token 去重；报告明确列出仍非幂等的 API 及保留原因。
- **Compatibility impact:** 增量（新字段，旧代码忽略）。低破坏。
- **Residual risks:** 副作用完成后、journal 标记完成前崩溃（靠领域对象自带幂等 id、重启后从 message/spawn registry 判断而非只信内存 journal 缓解）；journal 无限增长（TTL+容量上限，未完成 operation 不得被普通 GC 丢）；withdraw「撤回最新一条」不可可靠重试（新协议按 message_id 撤回，旧接口标记 legacy/non-idempotent）。

---

### HP-04 Review — spawn_token 与结构化 WorkerHandle

- **Decision: ACCEPT（以 §6.4 live probe 通过为前置）**
- **Rationale:** C8 坐实——`create_collaborator` 靠「同 cwd + `started_at > since_ts`，最新者胜」认领（`kernel_api.py:344-362`），无 nonce，并行同 cwd spawn 会串 session；返回纯 connect 字符串（`user_functions.py:487`），上层拿不到结构化 Worker 身份。spawn_token 经子进程 env 传递 + registrar 写入 SessionStart + kernel 建 `spawn_token→session` registry + 结构化 `WorkerHandle` 返回，是正确解。拆分低层 `spawn_collaborator`（spawn+register）与组合层 `create_collaborator`（spawn+connect）符合上层「先取 handle 再决定何时连接」的需求，我背书。
- **Conflicts with current implementation:** `spawn.py` 当前不传 `env=`；`registrar.js` 当前不读 env（事件 payload 无 token 字段）；kernel `_handle_start` 只读已知 key（新增字段会被忽略，向后兼容）。无硬冲突，均为增量。
- **Proposed revision:**
  1. 结构化 `WorkerHandle` 的返回**应与 HP-07 的 result 信封共用 schema**（`ok/code/data`，`data.worker`/`data.connection`），避免两套结构。
  2. Windows 侧 `cmd /c start` 默认继承环境、WSL 侧用 `env VAR=x <claude>` 或 `tmux set-environment`——两条路径都进 §6.4 probe；若 CC/hook 过滤 env，落到提案的 plan B（`pending_spawn/<token>.json`，Worker 首次调工具时认领）。
  3. token 仅作关联 id，**不作长期授权密钥**（与 HP-10 一致）。
- **Files/modules expected to change:** `server/spawn.py`（注入 env）；`scripts/registrar.js`（事件带 spawn_token）；`server/kernel.py::_handle_start`（记录 token）；`server/kernel_api.py::find_session_by_spawn_token`；`server/user_functions.py::create_collaborator`（结构化返回 + legacy wrapper）；`server/mcp_server.py` + `SKILL.md`。**两域同步。**
- **Tests to add:** 同 cwd 并发建 ≥5 Worker，各返回不同且正确的 session_id；同 spawn_token retry 只产一个 Worker；Windows 与 WSL 均验证 token 出现在 SessionStart registry；caller 无需解析自然语言 connect result 即知 Worker 身份；旧 `create_collaborator` 标记 legacy wrapper。
- **Compatibility impact:** `spawn_collaborator` 为增量；`create_collaborator` 转为 legacy wrapper（行为保留）。中。
- **Residual risks:** token 未传到 hook（live gate + plan B）；token 复用（registry 唯一，已完成 token 的 retry 返回原 handle）；token 被当权限凭据（明确仅关联 id）。

---

### HP-05 Review — connection_id 与握手消息关联

- **Decision: ACCEPT**
- **Rationale:** C3/C4 坐实且比提案所述更脆——`hello_ts` 从字符串解析、失败即 `hello_ts=0` 导致任意 target 消息被当 reply；timestamp 过滤还继承 HP-01 要消除的时钟问题。`control.connect.request/accept` + `correlation_id` 精确匹配是正确解，connect/close 属传输生命周期、不越 §0 边界。
- **Conflicts with current implementation:** 提案称「现有目录已预留 `info.json` 概念」——**失实（C3）**，实为新建 connection metadata（`info.json` 或等价）。`query_conversations` 现返回空 dict，需真正落地 metadata。工作量略升，但设计不变。
- **Proposed revision:**
  1. **并发 connect 策略我锁定为提案推荐**：同一 pair 同时只允许一个 active connection，retry 返回当前状态。拒绝「多 connection_id 并存」（徒增上层复杂度）。
  2. legacy fallback（旧 Worker 只会普通 `send_message`）**仅一个版本内、且仅在单一 pending 且无歧义时启用**（提案已述，背书）；并**接入 HP-12 telemetry**：每次走 legacy 路径都记录事件，作为「何时可安全删除」的判据。
  3. 新增 `reply_message(in_reply_to, text)` / `accept_connection(connection_id, text)`，避免要求模型手抄复杂 envelope——背书，这对降低上层出错率很关键。
- **Files/modules expected to change:** `server/conversations.py`（新建 `info.json`/connection metadata）；`server/kernel_api.py`（按 connection_id 注册/接受/关闭）；`server/user_functions.py::_poll_reply/connect`（timestamp 条件 → correlation 条件）；`server/mcp_server.py`（`accept_connection`/`reply_message` helper）；`SKILL.md`。**两域同步。**
- **Tests to add:** stale close notice / 普通业务消息不得被当 accept；两次 connect/reconnect 不消费对方 reply；connect timeout 后状态可清理或安全重试；connection_id 出现在日志/诊断；不再依赖 `hello_ts` 判握手归属。
- **Compatibility impact:** 握手协议变更；靠单一 pending + legacy fallback（限时）+ 显式 migration 管理。中。
- **Residual risks:** 旧 Worker 只发普通消息（限时 legacy fallback）；控制消息看似入上层（connect/close 属传输 lifecycle，允许；Plan/Probe 状态仍禁入）；同 pair 多次 connect（已锁定单 active 策略）。

---

### HP-06 Review — 集中输入验证、路径约束、destructive target 校验

- **Decision: ACCEPT（建议保持 Wave 1，优先级实际最高之一）**
- **Rationale:** C5 坐实且严重——零集中校验，模型生成的字符串可达 `os.path.join`/`makedirs`/`shutil.rmtree`。即便单用户可信模型，也不应让不可信字符串参与路径与删除。这是**局部、可先行、不依赖新协议**的高价值改动。
- **Conflicts with current implementation:** 无——纯新增约束层。注意 C5 的口径不一致（`registrar.js` sanitize 事件文件名，kernel/conversations 不 sanitize），HP-06 须**统一**为「边界校验 + 规范形」。
- **Proposed revision:**
  1. 校验失败返回结构化 `INVALID_ARGUMENT`，**不得静默 sanitize 成另一个合法 id**（否则两非法 id 可能映射同路径）——背书。
  2. **双层校验**（MCP 入口 + kernel dispatch 信任边界）背书——防未来内部调用绕开 MCP 入口，且 remote RPC 也经 kernel dispatch。
  3. **sequencing 提示**：HP-06 在 Wave 1、HP-07 结构化错误在 Wave 2。Wave 1 的 `INVALID_ARGUMENT` 可先以清晰错误串/最小本地错误类型返回，Wave 2 升级为结构化 code。**建议把错误码枚举（很小）提前到 Wave 1 定义**，避免返工（见 §6.6）。
  4. 验收「中文/空格 cwd、UNC、`/mnt/<drive>` 合法场景仍工作」**必须保留**——本项目真实路径 `C:\研究生\实习\...` 含中文+空格，是正例而非边角。
- **Files/modules expected to change:** 新增 `server/validation.py`（唯一 validator/resolve helper）；`mcp_server.py` 入口校验；`kernel.py` dispatch 二次校验；`conversations.py::conv_dir/pipe_filename`（只接受已验证 typed value 或内部强制校验）；`kernel_api.py::withdraw`、GC、migration 等 destructive path 操作点做最终 containment check。**两域同步。**
- **Tests to add:** `test_validation.py`——`../`/绝对路径注入/`__` 注入/控制字符全拒；fuzz 输入不能使任何写/删逃出测试 data root；destructive 测试在临时 workspace 内确认根未被删；中文/空格 cwd、UNC、`/mnt/<drive>` 合法场景工作；所有 MCP 入口与 remote RPC dispatch 经同一校验层。
- **Compatibility impact:** 低（非法输入从「静默通过」变「明确拒绝」；合法输入不变）。
- **Residual risks:** 历史 synthetic test id 不符严格 UUID（允许受限 slug，禁路径/分隔符）；合法 cwd 含中文空格（cwd 不做字符白名单，只做绝对路径/存在性/范围校验）；已登记 machine data_dir 是 UNC/WSL 特殊路径（平台感知路径校验，不用字符串前缀替代 resolved containment）。

---

### HP-07 Review — 结构化 Result/Error 与兼容适配层

- **Decision: ACCEPT**
- **Rationale:** C4 佐证——`"failed" in result`、解析 `"message_sent at <ts>"` 控流程，使调用方无法区分 retryable/invalid/peer-unreachable/timeout/internal，阻碍上层确定性调度。code 集合小而稳定、`data` 承载细节、不引重型依赖，是正确的克制。
- **Conflicts with current implementation:** 16 个 MCP tool 的返回形态变更；内部字符串分支逐条移除。工作量大但机械。
- **Proposed revision:**
  1. 与 HP-01（message record）、HP-04（WorkerHandle）**共设 schema**（提案已述，背书）——`data` 的载荷结构要一次设计到位。
  2. 兼容：新 v2 tools 用结构化结果；旧 tools 作 wrapper **生成**旧字符串、但内部**不得再解析**旧字符串控流程；error message 不作分支条件。背书。
  3. 建议错误码枚举在 Wave 1 随 HP-06 先落地（见 HP-06 revision 3），信封结构 Wave 2 补齐。
- **Files/modules expected to change:** 新增 `server/result.py`（统一 ok/error 构造）；`kernel.py::drain_queue`（transport error envelope 与业务 error 分离）；`kernel_api.py`/`user_functions.py`（先内部结构化，legacy wrapper 转回旧串）；`mcp_server.py`（区分 legacy 与 versioned tool）。**两域同步。**
- **Tests to add:** 内部业务代码不再搜 `"failed"` 或解析自然语言时间戳定流程；timeout 与 peer unreachable 可区分；所有 mutation 返回 operation_id 与结构化 data；legacy wrapper 有测试与明确删除条件。
- **Compatibility impact:** 中高（16 tool 调用习惯变更），靠版本化工具 + 一个 release wrapper + 更新 SKILL/examples 管理。
- **Residual risks:** 既有调用习惯改变（版本化 + wrapper）；过度设计错误层级（code 集合先小稳）。

---

### HP-08 Review — kernel 生命周期与 registration 解耦；安全 GC

- **Decision: ACCEPT（带一条竞态注意）**
- **Rationale:** C7 坐实——`alive_conversations` 非空即无条件阻退，close/unregister 若中断会让陈旧 conversation 使 kernel 永久驻留。而 kernel 已能 lazy-start 并恢复 conversation（C：`_load_sessions/_load_alive_convs/_load_ack_timestamps` + session_ctrl replay），故 registration 不应成为永久进程租约。设计「退出只看 queue/activity/lease/不可中断 mutation，registration 为持久逻辑状态不阻退，GC 永不删 unacked message/conversation log」正确。
- **Conflicts with current implementation:** `_should_exit` 移除 registration 的永久阻塞语义（`kernel.py:278-279`）。注意 `_last_activity` 当前**仅 queue 活动刷新**、session_ctrl 事件不刷新（`kernel.py:331-332`）——listen 是 queue RPC 会刷新，故活跃 listener 不会误退，OK。
- **Proposed revision:** 「kernel 在 remote request 到达前退出」的竞态——「退出前第二次 queue scan」缓解但**不消除**（文件可能在二次扫描后、进程退出前落盘）。真正兜底是 **client retry + remote wake**（`call_remote` 已会 `_wake_remote` 唤醒死 peer），故该竞态**良性**：miss 的请求在 retry 时唤醒 peer 重处理。我要求保留提案的「exit 与 remote request 竞争测试不丢 request」，并在报告里把「retry+wake 是兜底、二次扫描是优化」写清。
- **Files/modules expected to change:** `server/kernel.py::_should_exit/main`（移除 registration 永久阻塞、退出前 queue double-check）；`kernel.py`/`paths.py`（短 lease 与 GC 元数据，若采用）；`check_core.py`/`rpc_client.py`（确认 lazy restart 与 retry 仍成立）；`diagnose`（HP-12）暴露 stale/pending/GC。**两域同步。**
- **Tests to add:** 有 registered conversation 但无活动时 kernel 可 idle exit；下一次 send/listen 可 lazy-start 并恢复 registration；exit 与 remote request 竞争不丢 request；故意遗留 pending/response/tmp，GC 只删满足规则者；unacked message 在任意 GC 测试中不变。
- **Compatibility impact:** 低（仅 daemon 生命周期变更，不动 message store，可独立回滚）。
- **Residual risks:** kernel 过早退出（retry+wake 兜底 + 二次扫描）；listen 间隔大于 idle timeout（listen RPC 本身是活动 + 合理 timeout/短 lease）；GC 误删（白名单目录、最小 age、dry-run、永不碰 unacked）。

---

### HP-09 Review — 资源上限、背压与大载荷边界

- **Decision: ACCEPT（要求保持最小实现，防过度设计）**
- **Rationale:** 上层 Agent 会返回长日志/编译输出/批量 probe 结果，当前 pipe/queue/response/journal 无大小与积压策略。「可配置内联上限 + 通用 `artifact_refs`（路径/URI、size、sha256、media_type）+ 可查积压 + 软阈值返回结构化错误 + 硬上限拒绝新写而非删旧 unacked」正确，且**守住 §0 边界**（artifact 生命周期归上层 Evidence Store，cc-communicate 只传引用 + 校验基本 schema）。
- **Conflicts with current implementation:** 无——纯增量策略层。
- **Proposed revision:** 这是最易过度设计的一条。**Wave 3 只交付最小集**：内联大小上限 + `artifact_refs` schema + per-session/connection 积压计数（unacked count/bytes/oldest sequence）+ 软阈值结构化错误。**推迟**精细 per-connection 配额与 broker 式特性。内联上限默认值属产品决策，列入 §6.7 由上层定（建议 256KiB～1MiB 区间，按真实数据定）。为避免每次 send 全目录扫描，可在 kernel 内维护**可重建**计数缓存（磁盘文件仍是事实源）——背书。
- **Files/modules expected to change:** `validation.py`（payload/artifact ref 限额与 schema）；`kernel_api.py::send_message`（发布前算 size、查积压）；`conversations.py`（统计 per-session/conversation unacked count/bytes）；`result.py`（`RESOURCE_EXHAUSTED/BACKPRESSURE`）；`diagnose`（暴露阈值与积压）。**两域同步。**
- **Tests to add:** 超限 payload 返回结构化错误且不生部分文件；积压达软阈值可观测、不丢旧消息；artifact ref 含 hash/size、篡改可检测；GC 与背压不碰 unacked；压力测试记录最大积压/恢复时间/磁盘增长。
- **Compatibility impact:** 低（新增限制与字段；超限从不拒绝变明确错误）。
- **Residual risks:** 固定阈值不适所有任务（配置化 + 诊断展示）；artifact path 泄露/越界（默认只允许受控 root 或明确 URI，结合 HP-06）；背压死锁（control message 预留小额度 + 诊断指出哪个 consumer 未 ACK）。

---

### HP-10 Review — spawn 权限策略、身份边界与 threat model

- **Decision: ACCEPT**
- **Rationale:** 坐实——spawn/resume 无条件 `--dangerously-skip-permissions`（`spawn.py:83,85,99,102`），多个低层 tool 接受调用者自报 from/session id。适合受控实验，但**不能未经声明就成为上层自主 Agent 的默认安全模型**。
- **Conflicts with current implementation:** spawn flag 集中在两函数，改 policy builder 成本低；调用者身份绑定触及 API 兼容。
- **Proposed revision:**
  1. **A（明确 threat model）是本条最重要交付物**，必须进 README：「trusted single-user, trusted registered peer realm, **not safe against a malicious local process with data-dir access**」。**不得**把「同用户可写文件夹」描述成已认证通道。我强烈背书。
  2. **B（permission policy）**：`permission_mode = standard | bypass`，集中由 spawn policy 转 CLI argv，不散落多函数。新低层 spawn API 默认 `standard`；受控实验/测试显式 `bypass`；legacy `create_collaborator` 若续默认 bypass 须在返回与日志标记。**注意**：standard 会重现 trust dialog、破坏全自动 spawn——提案已承认，逃生口是显式 bypass，正确。
  3. **C（调用者身份）**：agent-facing 高层 tool 优先用 MCP server 从进程树解析的 self session id（`my_session_id` 机制核实可行）；接受任意 fromid 的 raw/admin tool 与普通 tool 区分并标记；remote request 至少记 `source_machine_id` + operation_id；**不把** spawn_token/message_id 当授权密钥。完整密码学认证留待 threat model 扩大到跨主机/不可信进程时另立 proposal——本轮不得伪装成已认证。背书。
  4. **决策点**：上层 Runtime 自身的 spawn 默认 `standard` 还是 `bypass`，是产品/安全权衡，列入 §6.7 由上层裁定。
- **Files/modules expected to change:** `server/spawn.py`（集中 permission policy→argv）；`mcp_server.py`/`user_functions.py`（区分 self-bound 高层 API 与 raw/admin API）；`rpc_client.py`（附 `source_machine_id`/operation_id）；plugin config + `SKILL.md` + README（声明默认 policy 与 threat model）。**两域同步。**
- **Tests to add:** spawn 调用与 WorkerHandle 可见实际 permission_mode；standard/bypass 两种命令构造有测试；普通 agent-facing send/close 默认绑 self identity（或说明为何暂不可）；raw/admin API 清晰标记、不在 SKILL quick start 鼓励；文档含明确 threat model 与未覆盖攻击面。
- **Compatibility impact:** 中高（API 变更 + 安全默认改变）；新 API 安全默认 + legacy wrapper 保持行为并告警。
- **Residual risks:** standard 重现 trust dialog（policy 显式选择）；改现有 API（新 API 安全默认 + legacy 告警）；本地 data dir 仍可被同用户进程改（threat model 承认；HP-06/HP-11 提供检测与审计，不虚假承诺）。

---

### HP-11 Review — 稳定 data root、状态 schema version 与无损迁移

- **Decision: ACCEPT（分两阶段：先 override + 迁移工具，后评估默认切换）**
- **Rationale:** C9 坐实——data 在插件根下，插件更新/重装/缓存路径变化/win-wsl 复制会丢 session registry/ACK/machine identity/conversation history，或携带错误机器身份；`machine_identity` 把 `data_dir` 发布给 peer，移动后 peer 记录失效。上层要依赖恢复与审计，运行状态不能与可替换的插件代码目录绑定。
- **Conflicts with current implementation:** 无代码冲突；风险在「默认目录切换 + 跨 realm re-registration」——提案正确地允许分两阶段接受。
- **Proposed revision:**
  1. **override 必须双边同步**（C10）：`paths.py` 与 `scripts/lib/paths.js` 用同一 `CC_COMMUNICATE_DATA_DIR` 解析规则，否则 Python kernel 与 JS registrar 对 data/ 位置分歧。这是我对提案的最重要补充。
  2. 解析优先级「explicit env/config → platform user-state default（未来切换）→ legacy PLUGIN_ROOT/data（兼容期）」背书；**先增 override + 迁移工具，再在后续版本切默认**。
  3. 迁移「detect → validate → backup → migrate atomically → serve」，只复制/转换、不自动删 legacy；machine identity 与 data root 绑定时检测 realm/type 不一致并**要求显式 regenerate**。背书。
  4. HP-00 选项 1 的 `CC_COMMUNICATE_DATA_DIR` 与此直接协同（测试即用它隔离），顺势落地。
  5. **默认切换时机列入 §6.7** 由上层裁定（涉跨 realm handshake 与已装插件迁移风险）。
- **Files/modules expected to change:** `server/paths.py` 与 `scripts/lib/paths.js`（同一 data root 解析）；`machine_identity.py`（data root/realm 绑定校验）；新增 `migration.py` 或一次性 CLI（schema 检测/backup/copy/convert）；handshake scripts（重新发布 peer perspective 的 data_dir）；`check_core.py`/hooks（统一用解析后 root）。**两域同步。**
- **Tests to add:** 自定义 data root 下完整跑 local tests；模拟插件代码目录更换后指定同一 data root 可恢复 session/conversation/cursor；schema 不支持时 fail closed 并给可操作诊断（不按空状态启动）；迁移中断后可从备份/旧目录恢复；Windows/WSL handshake 对新 data_dir 的双向路径转换重新 live 验证。
- **Compatibility impact:** 中（override 增量零破坏；默认切换为破坏性，单独版本 + 迁移说明）。
- **Residual risks:** host/WSL 互记的 data_dir 失效（迁移后更新 machine registration 或 re-register 检查，诊断显示 stale peer path）；两插件实例指同一 data root（machine/store identity 校验 + 单 kernel lock，realm 不一致拒启）；迁移中断（backup+temp+atomic replace，旧目录保留可回退）。

---

### HP-12 Review — 结构化可观测性与 health/diagnose

- **Decision: ACCEPT（P2，排在 Wave 3–4 正确）**
- **Rationale:** 上层需区分「消息未发送/已发未 ACK/Worker 未启动/connect 关联失败/remote kernel 未唤醒」，靠自然语言 result + 散落 log 无法可靠恢复。append-only 结构化事件日志（默认不记完整 message content）+ 只读 `diagnose_transport` 与项目擅长的文件协议模式契合。
- **Conflicts with current implementation:** 无——纯增量。
- **Proposed revision:**
  1. **默认只记 metadata、content opt-in**——对 prompt 泄露风险至关重要，背书。日志有轮转/大小上限。
  2. telemetry 写失败不得使 send/listen 主流程崩溃，但须产生**可见降级信号**（接 HP-07 result）。背书。
  3. `diagnose` **只读**、不得顺手 GC 或改状态。背书。
  4. 事件须带稳定 id（operation/message/spawn/connection），使任一失败可串联——这是 HP-03/04/05 落地后才可能，故排序在后正确。
- **Files/modules expected to change:** 新增 `server/telemetry.py`（统一追加结构化事件 + 轮转）；operation/message/spawn/connection 创建点发事件；`kernel_api.py`（只读 health snapshot）；`mcp_server.py`（`diagnose_transport`）；`paths.py`（日志位置与限额）。**两域同步。**
- **Tests to add:** 任一失败可经 operation/message/spawn/connection id 在结构化日志串联；diagnose 只读报告积压/cursor/pending operation/data root；日志默认不含完整 payload；telemetry 写失败不崩主流程但有降级信号；日志轮转不删 mailbox/operation state。
- **Compatibility impact:** 低（新增只读接口与日志）。
- **Residual risks:** 日志泄露 prompt/message（默认 metadata、content opt-in、轮转上限）；diagnose 改状态（强制只读）。

---

### HP-13 Review — 源码单一事实源与跨 realm parity gate

- **Decision: ACCEPT（短期 B，协议稳定后评估 A/C）**
- **Rationale:** 坐实——`v2_win/` 与 `v2_wsl/` 两份近乎相同实现，协议加固会同时改多个核心模块，手工双写易产生隐蔽分叉。提案「不在 Wave 1 同时大重构目录」的克制**完全符合 §1.3 纪律**，我强烈背书。
- **Conflicts with current implementation:** 无——B 为增量门禁；A/C 是后续迁移。
- **Proposed revision:**
  1. **先上 B**（保留双目录 + CI/hash 强制除允许名单外 byte-identical）。当前两套 server 源码本就等价（我 T25 工作时 parity 一致），建 hash/parity gate 短期成本极低，且能立即防止协议 churn 期间的静默分叉。
  2. **允许差异名单须显式且最小**。核实：`v2_win` 与 `v2_wsl` 的 `plugin.json`/`marketplace.json` 内容当前**一致**（均为 0.3.0 / 16 MCP tools），故连 manifest 都可纳入 parity gate；真正需 allow-list 的仅 `.mcp.json` 与平台入口/launcher。建议把「server 源码 + scripts + manifest」全纳入比对，allow-list 只留平台必需文件。
  3. A（canonical `src/cc_communicate/` + release script 生成）/C（单 plugin tree + 平台 launcher）留待 Wave 1–3 协议稳定后评估；采用 C 前须确认 Claude plugin MCP command 的跨平台行为。
- **Files/modules expected to change:** Gate 0 增 parity test 与允许差异清单；若 A：新增 canonical source + materialize/release script（生成目录可重复）；若 C：新增跨平台 launcher，`.mcp.json` 只调 launcher；marketplace/plugin manifest 由同一版本源生成。
- **Tests to add:** CI 在 win/wsl 非允许文件出现差异时失败；builder 报告说明最终选 A/B/C 及迁移计划；clean checkout 一条命令生成/验证两个 plugin artifact；生成前后 Windows/WSL 安装入口与 live behavior 不变。
- **Compatibility impact:** B 零破坏；A/C 涉 plugin packaging/path，单独 Wave、单独 commit、完整 live regression。
- **Residual risks:** 目录重构破坏 `${CLAUDE_PLUGIN_ROOT}` 与安装流程（先生成与现有布局完全相同 artifact 再改内部位置）；generated files 被手改（CI 检查 clean regeneration + 文件头标 generated）；与协议修复混杂难定位回归（单独 Wave/commit/live regression）。

---

## 3. Decision 汇总表

| Proposal | Decision | 关键修订/条件 |
|---|---|---|
| HP-00 | **ACCEPT** | override 双边同步（paths.py + paths.js） |
| HP-01 | **ACCEPT** | counter 原子写补 fsync；跨 realm rename/fsync live gate；record schema 独立小模块 |
| HP-02 | **ACCEPT** | API 选 **A**（listen_v2 + query_my_cursors）；B 为备选（有静默降级 footgun）；反 C |
| HP-03 | **ACCEPT** | Wave 1 落地范围不含 spawn/evoke 去重（待 HP-04）；crash-window 见 §6.3 |
| HP-04 | **ACCEPT** | 以 §6.4 live probe 为前置；WorkerHandle 与 HP-07 共 schema |
| HP-05 | **ACCEPT** | 纠正 info.json 失实（新建非扩展）；锁单 active connection；legacy fallback 接 telemetry |
| HP-06 | **ACCEPT** | 统一 registrar/kernel 口径；错误码枚举提前到 Wave 1；保留中文/UNC/space 验收 |
| HP-07 | **ACCEPT** | 与 HP-01/04 共 schema；错误码枚举手 Wave 1 先落地 |
| HP-08 | **ACCEPT** | exit-vs-remote 竞态靠 retry+wake 兜底（二次扫描是优化），需竞争测试 |
| HP-09 | **ACCEPT** | 保持最小实现；内联上限默认值交上层（§6.7） |
| HP-10 | **ACCEPT** | threat model 必进 README；Runtime 默认 permission_mode 交上层（§6.7） |
| HP-11 | **ACCEPT** | override 双边同步；分阶段；默认切换时机交上层（§6.7） |
| HP-12 | **ACCEPT** | 默认 metadata-only；telemetry 失败不崩主流程但有降级信号；diagnose 只读 |
| HP-13 | **ACCEPT** | 先 B（parity gate，allow-list 最小化），后评估 A/C |

**14 条全部 ACCEPT，无 REJECT / DEFER。** 5 个决策点需上层规划者裁定（§6.7）。

---

## 4. 波次计划评估

接受整体波次结构。两点 sequencing 说明（不视为缺陷，但要在报告写明）：

1. **HP-03 的 Wave 1 落地范围**：operation_id + journal + 天然幂等 mutation + 经 HP-01 的 send 去重。**spawn/evoke 的领域去重依赖 HP-04 的 spawn_token（Wave 2）**，与依赖图一致。
2. **HP-06（Wave 1）与 HP-07（Wave 2）的错误码先后**：建议把**错误码枚举**（很小）提前到 Wave 1 随 HP-06 定义，信封结构 Wave 2 补齐，避免 HP-06 先返回纯字符串、Wave 2 再返工。

依赖图（§3）与实际依赖一致，无循环、无「验收时须兼容」被误读为「机械串行」的问题（§3 注已澄清 HP-07 可与 HP-01/04 并行设计）。

---

## 5. 对 §0 边界与交付语义的复核

- **边界**：HP-01～HP-13 无一越界。`kind`/`correlation_id`/`payload`/`artifact_refs` 均保持传输通用；connect/close 属传输 lifecycle；cursor 是传输 ACK 非任务完成；artifact 生命周期归上层 Evidence Store。**符合。**
- **交付语义**：「at-least-once + per-store ordering + detectable duplicates + idempotent mutation」诚实且可达。我作为负责者**拒绝**任何升级为崩溃条件下严格 exactly-once 的提议。**符合。**

---

## 6. 首轮审核请求的 7 项输出（提案 §6）

### 6.1 HP-00～HP-13 逐条 Decision
见 §2 / §3 汇总表。全部 ACCEPT（5 带修订）。

### 6.2 对 HP-01/HP-02 的替代协议设计
**我同意 sequence + per-store cursor，无需替代方案。** 仅补三条工程修订：① 持久 counter 写须原子且补 fsync（现有 `_atomic_write_json` 无 fsync）；② 跨 realm（`/mnt/c` DrvFs、`//wsl.localhost` 9P）的 rename 原子性 + fsync 持久性须 live 验证，正确性锚点是「reader 永不见 partial」，崩溃持久性由持久 counter + message_id 去重兜底；③ 旧 ACK 迁移不静默把 timestamp 当 sequence——旧消息进 legacy namespace，新 cursor 从显式迁移点起算。

### 6.3 HP-03 crash-window 幂等边界（诚实声明）
无法让外部副作用（spawn 一个 CC 进程、写一条 peer 可能已读的消息）在任意崩溃下严格 exactly-once。能做到的是：
- **(a)** `operation_id` 跨 retry 稳定，使「找到已 journal 的 operation」的 retry 返回原结果而非重执行；
- **(b)** 幂等锚定在**持久领域对象**（message store 的 message_id、spawn registry 的 spawn_token），而非仅内存 journal——重启后去重查持久记录；
- **(c)** 残留窗口：崩溃发生在「副作用完成」与「持久幂等锚点写盘」之间时，retry 仍可能重复——靠**先写锚点再/随副作用**缓解（HP-01 先分配持久 message_id 再发布；HP-04 plan B 先写 `pending_spawn/<token>` 再 spawn）。

**诚实保证：at-least-once 执行 + 以持久领域状态为锚的 retry 去重；仍存在一个极窄崩溃窗口会产生重复，但重复必然可检测（经 message_id/spawn_token）、绝不静默。**

### 6.4 spawn_token 是否可经 child env 传入 SessionStart hook — 最小 live probe
**评估：很可能可行**（hook 继承 CC 进程环境；`cmd /c start` 默认继承环境；registrar 一行改动即可读 env）。最小 probe（先 Windows 后 WSL）：
1. **传输层（零代码改动）**：用小段 Python `Popen(..., env={**os.environ, "CC_COMMUNICATE_SPAWN_TOKEN":"probe-<uuid>"})` 经**与生产一致的 `cmd /c start`** 路径启动一个打印 `%CC_COMMUNICATE_SPAWN_TOKEN%` 的进程，确认 token 穿透 `start`。WSL 侧用 `tmux new-session ... 'env CC_COMMUNICATE_SPAWN_TOKEN=x ...'` 验证。
2. **hook 层（scratch 副本一行改动）**：在 registrar.js 的 start payload 临时加 `spawn_token: process.env.CC_COMMUNICATE_SPAWN_TOKEN || null`，用改造后的 spawn 在隔离 data root 启动一个 CC，检查生成的 `data/session_ctrl/start_*_<sid>.json` 是否含 token；同时确认现 kernel `_handle_start` 对未知字段**优雅忽略**（核实为 `ev.get(...)` 只读已知 key，是）。
3. **判据**：两 realm 的 SessionStart registry 均出现 token → 采用 env 方案；若 `start`/tmux 剥离 env → 落 plan B（`pending_spawn/<token>.json`，Worker 首次调工具认领）。

### 6.5 建议的兼容版本策略
- 所有新持久记录带 `schema_version`（message record、cursor map、operation journal、machine identity、connection metadata）。
- 协议 bump 用**版本化工具**（HP-02 选 A：`listen_v2`），legacy `listen` 保留一个 release 作 wrapper。
- **双 reader**：旧 `.md` 与新 `.json` 并存至少一个版本；writer 只写新格式。
- **legacy namespace**：旧 ack/message 不猜测性转换为 sequence；新 cursor 从显式迁移点起算。
- 每个破坏性变更带 schema/version + 迁移说明（符合 §1.3）。
- deprecation window 明确写入 SKILL 与报告， legacy 路径接 telemetry 以判定删除时机。

### 6.6 Wave 1 精确文件修改清单与测试清单
**HP-00**：新增 `tests/{unit,integration,live}/`；`server/paths.py` + `scripts/lib/paths.js`（`CC_COMMUNICATE_DATA_DIR`，**双边**）；pytest 配置；kernel fixture。
**HP-01**：`server/conversations.py`；`server/kernel.py`（sequence state）；`server/kernel_api.py::send_message`；`server/user_functions.py`；`server/paths.py`；（新增 `server/message_record.py`）。
**HP-02**：`server/kernel.py`（cursor map）；`server/kernel_api.py::listen_scan/query_ack_timestamp/upload_ack_timestamp`；`server/user_functions.py::listen/close_connection`；`server/mcp_server.py`（`listen_v2`/`query_my_cursors`）；`SKILL.md`。
**HP-03**：`server/rpc_client.py`；`server/kernel.py::drain_queue/_dispatch`；`server/kernel_api.py`；（新增 `server/operation_journal.py`）。
**HP-06**：新增 `server/validation.py`；`mcp_server.py`；`kernel.py` dispatch；`conversations.py::conv_dir/pipe_filename`；`kernel_api.py::withdraw`。
**错误码枚举**（提前自 HP-07）：新增 `server/result.py`（仅枚举，信封 Wave 2 补）。
> 以上全部 **两域（`v2_win/`、`v2_wsl/`）同步**，由 HP-13 parity gate 兜底。

**测试清单（Wave 1）**：`test_message_record.py`、`test_cursor_ack.py`、`test_rpc_idempotency.py`、`test_validation.py`（unit）；`test_kernel_restart.py`、`test_cancel_redelivery.py`、`test_concurrent_senders.py`、`test_dual_store_cursor.py`（integration）。每条均满足「旧代码失败、新代码通过」。

### 6.7 需上层规划者决定的争议点
1. **HP-02 API 形态**：我选 A（版本化 `listen_v2`）。若上层把「最小工具数」置于首位，可改 B，但须接受「忘传 cursors 即静默降级回 timestamp 模式、PB-3 回归」的风险。
2. **HP-11 默认 data root 切换时机**：先 override + 迁移工具（我建议本周期落地），**默认目录切换**涉跨 realm handshake 与已装插件迁移，请上层定切换版本。
3. **HP-13 长期形态**：A（canonical src + 生成）vs C（单 tree + launcher）。我建议先 B，协议稳定后由上层定 A/C。
4. **HP-10 Runtime 默认 permission_mode**：上层 Runtime 自主 Agent 的 spawn 默认 `standard`（更安全、有 trust dialog）还是 `bypass`（全自动、须显式声明受控）。这是产品/安全权衡，我倾向新 Runtime 显式选 `bypass` 并在文档标记受控，但**默认应交上层拍板**。
5. **HP-09 内联 payload 上限默认值**：256KiB～1MiB 区间，按上层真实数据（日志/编译输出/probe 结果量级）定。

---

## 7. 给上层规划者的结论

我作为 cc-builder，对全部 14 条给出 **ACCEPT**（5 条带具体修订，已在上文逐条写明修订内容）。提案的诊断与我的代码核实高度一致，边界与交付语义正确，波次纪律符合要求。**在上层返回 ACCEPT/REVISE 之前，我不会默认任何 proposal 已获批准、不启动大规模实现**（遵守 §6 与 §1.3）。

待上层裁定 §6.7 的 5 个决策点后，我建议按 **Gate 0（HP-00）→ Wave 1（HP-01/02/03/06）** 启动，首个交付物为「可重复自动化基线 + Wave 1 四条的实现与故障测试」，并在每个 Wave 后跑完整回归供上层决定是否继续。
