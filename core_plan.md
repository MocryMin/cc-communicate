内核应该由多个模块组成。
内核本身是一个退避的循环。循环基础频率为1khzsleep 0.001s，如果连续10000个循环没有事件，循环周期*10，直到退避到sleep 1s。
所有事件都以文件的形式存在在queue临时文件夹中，外部调用某个内核功能（function:x），需要通过启动对应x.py中的x()，x会以只增的方式在queue添加一个排队文件，文件内包含外部调用生成的事件，等待内核处理。
内核的启动是被动的。系统中同时只能有一个内核进程实例。具体地，所有用户函数执行时都会默认调用check_core.py：check_core 用 filelock 互斥地访问全局文件 core_status.json，其结构为 {status: 0|1, pid, start_time}。status=1 不代表"kernel 此刻在跑"，只代表"上次记录在跑"——check_core 必须用 psutil 验证 pid+start_time 仍存活（防 kernel 崩溃后 status 残留为 1），不活则视同 status=0。若判定需要启动，则保持锁、启动 kernel 进程，等待 kernel init 完成后写回 status=1+pid+start_time 作为 ready 信号，随后释放锁。详见技术难点 #11。
内核的退出条件：alive_conversations 全部死亡，并且 10 分钟内没有新的 conversation 到来，并且 queue 为空。三者同时满足时，kernel 修改 core_status.json 为 status=0，将 alive_sessions 中还活着的 session 写入磁盘（data/ 下快照文件，下次 init 加载），随后自杀。退出竞态与兜底见 #11。


## datebase结构
### sessions.json
记录当前机器上所有被发现的session的基本信息。为持久化文件，有process_session_ctrl_event维护。

### conversations结构
conversations
    sessionid1(分隔符)sessionid2
        info.json
        pipe
            fromid（分隔符）toid-timestamp.md
            ...
        log
            fromid（分隔符）toid-timestamp.md
            ...
    ...

#注意两个session之间的连接本身地位相同，是p2p关系，因此名称sessionid1(分隔符)sessionid2和sessionid2（分隔符）sessionid1无差异，且只会存在一个。检查时逻辑应该设为包含判断而非字符串严格匹配。


### 内核函数
内核函数是内核.py自身，或者只能由内核.py进程直接引用的库中的函数。只能通过内核进程直接调用。  
用户函数进程想要调用内核函数，只能即通过统一内核函数调用格式，编写调用请求文件放在queue文件夹中排队。  
目前有如下内核函数：
1. process_session_ctrl_event()
这个函数在每个循环周期开始时触发一次。检查data/session_ctrl文件夹，如果有未处理的事件，则按照timestamp先后顺序处理每一个事件。
处理每一个事件的时候，维护两个数据结构：一个持久化数据结构：database中的sessions.json，sessions.json是一个字典，键是session_id，值是session_inf，如果事件是start类，则查看start的session是否被记录，如果没有则添加；另一个是内存数据结构，alive_sessions。它需要动态维护当前的session_id，pid，session存活状态信息。需要结合start和end事件按时序动态维护。
alive_sessions写需要互斥。
2. withdraw(fromid,toid，init_connect)
如果init==1，直接清除fromid,toid在conversations中的文件夹。
反之，清除fromid在pipe中最新的一条消息。
3. query_session(session_id)
在database sessions.json中查询sessionid，返回其值；如果没有，返回0.
4. check_alive(session_id)
需要进行两个检查：1、查询内核中维护的alive_sessions表格是否存在此sessionid的pid，如果不存在，返回0.
如果存在，查询此session_id对应的pid是否还在os中真的存在（防止alive_sessions存在错误），如果pid不存在，说明内核中维护的这条数据存在过时，删掉内核中该数据结构中的该记录。如果pid在系统中存在，检查pid的创建时间是不是内核中维护的时间，如果不是，说明可能遇到了pid复用，同样修改内核记录。返回0.
通过全部检验，返回1.
5. evoke(sessionid)
创建该session进程，并输入初始消息，让其listen。
alive_sessions写操作需要使用互斥。


### 用户函数  
用户函数应该是一个独立的.py，可以同时存在该函数的若干进程实例。不同函数、同一个函数不同实例**不存在**争用关系。 
内核向cc LLM应该提供如下基础**用户函数**：（插件提供的TOOL 通过MCP tool注册）
1. query_conversations.py:
输入: querier's session id.  
输出：一个list, 每一项为：{querier在过去联系过的session id: {info（这里的info来自记录该id2id conversations文件夹中的inf.json，是日后拓展的功能）}}  
依赖资源和操作：对database进行只读查询.  
描述：在一个session没有compact之前，session上下文中应该保留着全部的conversation inf，因此理论上不需要query。但如果session对应的LLM对conversation的对象列表等记忆出现模糊，例如调用出错，可以通过query来加强记忆；或者compact之后相关信息丢失，可以query一次恢复。  
2. connect.py:  
输入：自己的id, 想要连接的session的id；（hold time,可选，默认10分钟，表示超时计时器）  
输出：connect succeed/fail; session not exists!  
处理逻辑：先调用**内核函数**query_session(session_id), 如果返回查无此session，则返回no exits。先调用**内核函数**check_alive(session_id)查询用户想要连接的那个session是否存活；如果不存活，则调用**内核函数**evoke(session_id)。
先检查database,conversations中有没有存在这两个id的通话记录，如果是第一次尝试连接，先初始化创建对应目录；同时置init_connect为1。
然后调用用户函数send_message(fromid, toid,message), message内容为标准hello报文。  
然后调用keep_listen进行单点（只听发起id-target）监听，得到回复则表示连接建立成功，在内核中注册这对sessionid，告知内核多了一对活着的、需要每轮询问并处理的对话。随后返回succeed; 如果监听10分钟未得到回应，则调用**内核函数**withdraw(fromid,toid，init_connect)，并返回failed。 
依赖资源和操作： 内存中的保持连接对话表，写，需要互斥。
3. send_message.py  
输入：fromid, toid, message
操作：首先检查内核中登记的conversation中当前connection是否依然有效；如果有效，根据fromid，toid和当前系统时间timestamp拼接出路径名，文件名，将message作为内容写入到conversations对应conversation的pipe文件夹中。
返回一条消息：message_sent at (time)；或者返回failed, connection terminated by 对方。
依赖资源和操作： 内存中的保持连接对话表，读。
4. keep_listen.py — 异步监听机制（MCP + Bash poller 组合方案）
**设计原则：** 及时唤醒 + 节省 token + 稳定短调用。listen 期间 CC 可以干别的事，空闲时不消耗 token，收到文件变更通知才被唤醒。
**实现：** 拆成两个短 MCP tool + 一个固定路径的 Bash 后台脚本。
  (a) MCP tool: arm_poller(session_id, fromid_list?, timeout?)
      输入：session_id（必填），fromid（可选，默认全部对话对象），timeout（可选，默认 30min）
      操作：调用 query_conversations 获取对象列表，生成 poller 脚本或写入固定路径的脚本配置，更新目标文件和 baseline。
      返回：poller 已 arm 的确认 + 超时时间
      说明：这是一个极短的 MCP tool——做完准备工作（获取对象列表、写入配置）即返回，不阻塞 CC。
  (b) Bash: bash <plugin_root>/scripts/listen-poller.sh (run_in_background: true)
      操作：后台轮询 pipe 文件夹中发往 session_id 的新文件（检测 mtime/size 变化），退避策略 5s→10s→...→5min 上限。
      退出码：0 = 检测到变更（消息到了），2 = 到达 timeout 无变更
      说明：脚本路径固定且预写在插件中，CC 只用一行 `Bash("bash <path>/listen-poller.sh", run_in_background: true)` 调用，不每次生成脚本。
  (c) MCP tool: collect_messages(session_id)
      输入：session_id
      操作：扫描 pipe 文件夹中发往 session_id 的未处理消息，读取内容，将对应文件移至 log 归档，按时间排序
      返回：[{time: Time, from_id: FromId, message: Message}, ...]
      说明：极短的 MCP tool——纯文件读取+移动操作，立即返回。
**CC 视角的完整循环：**
  1. arm_poller(sid)                              ← MCP tool，~5 tokens
  2. bash <plugin_root>/scripts/listen-poller.sh  ← 一行 Bash，后台
  3. [CC 空闲，或干别的任务，不消耗 token]
  4. <task-notification> poller exit 0            ← harness 自动注入，CC 被唤醒
  5. collect_messages(sid)                        ← MCP tool，~5 tokens
  6. 处理消息 → 回复 → re-arm poller (回到步骤 1)
**与 cc-wake-guide.md 的关系：** 本方案复用了 cc-wake-guide.md（C:\Users\Mocry\cc-wake-guide.md）中验证的 `run_in_background` + 进程退出通知机制。那里的 bash poller 是基础原型，本项目的 listen-poller.sh 是其固定化、参数化的插件内嵌版本。
5. close_connection.py
输入：sessionid, toid;
sessionid发起的，关闭一个已经建立的到toid的连接。
操作：首先将内核中登记的当前对话状态注销；处理pipe:将pipe还未处理的前往sessionid的消息全部交付sessionid（以返回值的方式，格式同keep_listen.py）；同时，在pipe中自动添加一条no-rely通知，告知toid连接被对方关闭。
6. create_collaborator.py
描述：在自己希望的位置创建一个全新cc session.
输入：sessionid，期望的工作目录，机器（日后可能拓展hostcc-wslcc。此值可选，默认主机）
操作：创建session，让其加载plugin（如果没有），并初始化设置listen。然后调用connect(sessionid, new_session's id)


---

## 技术难点与解决方案

本文档记录了设计阶段识别出的所有技术障碍及其解决方案。后续 builder CC 在实现时，遇到同名概念即可在此查阅，无需重新讨论。

---

### 0. 先决架构决策

| 决策 | 结论 |
|---|---|
| 工具注册方式 | MCP tool（非 skill 描述的 Bash 调用）。每个用户函数 = 一个 `.py` MCP tool 文件，CC 通过 MCP 协议调用 |
| 用户函数与内核通信 | 写 queue 文件排队。用户函数不能直接调内核函数——只写请求文件；内核轮询处理 |
| 临时文件位置 | 全部放在 `PLUGIN_ROOT/data/` 下。`session_ctrl/` 专属事件日志（已占用），`server/` 放内核产物，`queue/` 放排队事件 |
| 上层语言 | **Python**（非 Node）。下层 Node 已 freeze 不再变；上层选 Python 以保留人工审计与文件结构掌控能力。代价：插件依赖 Python 运行时（Win-only 目标下可接受；CC 不 bundle Python） |
| 下层模块复用 | `proc.js` / `paths.js` 不直接 import，而是维护**冻结等价的 Python 实现** `proc.py`（psutil）/ `paths.py`。此为对 README §2.3 "import 同一模块"的放宽——下层已定型，漂移风险一次性、单向，做完对齐即可 |
| 内核函数返回值投递 | kernel 处理完 queue 请求后，将结果写入 `queue/responses/<request_id>.json`（request_id 由请求文件名携带）。工具侧轮询该文件，带超时；超时则重跑 check_core 重试（见 #11c） |
| 互斥原语 | `filelock` 包（纯 Python、跨平台）。core_status.json 访问互斥用它；alive_sessions 仍靠内核单线程串行免锁（见 #9） |

---

### 1. CC 无守护模式 → evoke / create_collaborator 如何让目标 CC 存活？

**问题：** CC 只有两种原生模式：(a) `-p` 一次性——处理完退出；(b) 交互式——需要 TTY 和人类输入。不存在"无头持续待命"模式。

**根因分析：** 不是 Python 进程被杀导致 CC 退出——`subprocess.DETACHED_PROCESS` 可让子进程完全独立。真正的根因是 CC 在 `-p` 模式下自己退出（设计如此），而无 `-p` 的交互模式需要终端。

**解决方案：** 
- 使用 `cmd /c start claude --cwd <dir> <initial_prompt>` 打开 Windows cmd 窗口，在里面跑交互式 CC
- `<initial_prompt>`（positional 参数，无 `-p`）会被处理，但 CC 处理后**不退出、进入交互 REPL**（实测验证通过）
- 也就是说：`claude "msg"` ≠ `claude -p "msg"`。前者处理后驻留，后者处理后退出
- CC 进程独立于 Python（`start` 语义 = 新窗口分离运行），Python 退出无影响

**实现代码片段（evoke 内核函数的重要部分）：**
```python
import subprocess
subprocess.Popen(
    ['cmd', '/c', 'start', 'claude', '--cwd', working_dir,
     f'Check for pending messages using keep_listen tools'],
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
)
```

---

### 2. keep_listen 的形态选择

**问题：** 如果 keep_listen 写成 MCP tool 内部做长轮询（30分钟退出后批量返回消息），CC 被阻塞，无法"有任务就干别的"。如果让 CC 自行写脚本、自行设置轮询策略，每次 token 消耗大且稳定性差（可能写错）。

**需求：**
1. 及时唤醒——消息到了尽快感知
2. 节省 token——空轮询不消耗 token（CC 不被唤醒就不触发 turn）
3. 封装好的短调用——CC 只调固定 MCP tool，语句短且稳定

**解决方案（MCP + Bash poller 组合）：**
- CC 唯一的异步通知渠道是 `Bash(run_in_background: true)` + 后台进程退出时 harness 注入 `<task-notification>`（参见 `C:\Users\Mocry\cc-wake-guide.md`，已验证通过 12h 稳定运行测试）
- 等候机制用 Bash 后台 poller（不花 token），生成和收取用 MCP tool（封装好，短调用）
- 拆为三个组件：`arm_poller`（MCP tool，极短）、`listen-poller.sh`（Bash 后台）、`collect_messages`（MCP tool，极短）
- 详见上文 keep_listen.py 的完整描述

**token 对比：** CC 自行写脚本代写：~50-200 tokens/次；本方案组合：~5-15 tokens/次

---

### 3. check_alive 的 OS 级活性校验

**问题：** `alive_sessions` 内存表可能因为进程异常退出、pid 复用等原因与实际不一致。`check_alive` 需要可靠判断一个 session 是否真正活着。

**解决方案：**
- `proc.py`（psutil 实现，`proc.js` 的冻结等价）提供 `live_procs()` → 返回全机 `pid → start_time` 映射
- `alive_sessions` 中有 `session_id → {pid, start_time}`
- 四步校验：查 alive_sessions 有无 pid → OS 中 pid 是否存活 → 存活则比对 start_time（PID 复用防御）→ 不匹配则更新记录
- 完全可行，实现复杂度低（一次 dict 查询 + 一次比较）

**底层依赖：** `proc.py` 用 `psutil`（跨平台，Windows 下底层即 CIM/WMI，与 `proc.js` 的 PowerShell 分支等价）。Linux 下 psutil 同样可用，无需手工解析 `/proc`。

---

### 4. evoke 时 cwd 来源

**问题：** `evoke(session_id)` 需要知道目标 session 的工作目录才能 `claude --cwd <dir> ...`。

**解决方案：**
- `sessions.json` 中每条记录有 start 事件中的 `cwd` 字段
- 内核 `process_session_ctrl_event` 在 replay 时已将 `cwd` 纳入 session_inf
- `evoke` 从 sessions.json 读 cwd，直接使用

**限制：** 
- session 必须有 start 事件（有 cwd）才能被 evoke
- 仅 end 事件无对应 start（异常情况）：该 session 无 cwd 记录，evoke 无法定位目录，应返回错误
- 插件安装前启动的 session：无 start 事件 → 无 cwd → 无法 evoke

---

### 5. create_collaborator 时 session_id 获取时差

**问题：** 新 CC 被 spawn 出去后，其 SessionStart hook 触发 → start 事件写入 `session_ctrl/` → `process_session_ctrl_event` 检测到 → `sessions.json` 新增 → 内核此时才知道 new_session_id。但在 `create_collaborator` 里需要立刻调用 `connect(caller_sid, new_sid)`。

**解决方案：** `create_collaborator` 设计为异步：
1. Spawn CC 进程（此时不知道 new_session_id）
2. 在内核 queue 中写一个 pending 状态："caller_sid 在等待新 session 的 sid，目标 cwd = X"
3. 内核在下一轮 `process_session_ctrl_event` 检测到新 start 事件 → 比对 pid 和预期 cwd → 确认 new_session_id
4. 内核自动调用 `connect(caller_sid, new_session_id)`（替代 create_collaborator 步骤中的最后一步）
5. create_collaborator 返回 "cc spawned, pending connect" 或最终结果

**可接受延迟：** 2-10 秒（CC 启动 + hook 触发 + 内核轮询周期）

---

### 6. 插件安装前启动的 session 无法参与 p2p

**问题：** cc-monitor 是被动生产者——只有 hook 触发后才写事件。插件安装前的 session 从未触发过 SessionStart hook，因此没有 session_id 可寻址。

**可恢复的：** `proc.py`（psutil）可以扫描全机进程，找到所有 `claude.exe` 的 pid、cwd、start_time。

**不可恢复的：** `session_id` 只有 CC hook 触发时才知道（由 CC 通过 hook stdin 注入），从外部进程扫描无法恢复。

**处理策略：**
- 扫描发现的"无 session_id"进程在 sessions.json 中标记 `UNKNOWN_SID<pid>`，活性仍可判断，作为只读影子条目
- conversation 系统拒绝路由到 UNKNOWN_SID——安全默认，因为无法确保消息投递到正确的 session
- 建议操作指南写明：安装 plugin 后重启所有 CC。旧 session 是过渡期现象，每次有几条 CC 很短生命周期。预装 session 不可被 p2p 发现。

---

### 7. 全机 session 统一入口

**问题：** 需要知道全机的 CC session，不限于在某个项目中注册的。

**解决方案：** cc-monitor 本身就是统一入口。它的 hook 是**用户级**挂载——所有 CC 的 SessionStart/End 都写入同一个 `session_ctrl/` 文件夹。不需要再建设额外的入口。

**注意：** 见难点 #6——仅对插件安装**后**启动的 session 有效。

---

### 8. conversations 存储键名双向匹配

**在 plan 正文中已约定（此处重申）：** 两个 session 之间的连接是 p2p 平权的。文件夹名 `sessionid1<分隔符>sessionid2` 和 `sessionid2<分隔符>sessionid1` 只会存在一个。查找时用"包含判断"而非字符串严格匹配——即检查某个文件夹名是否同时包含两个 session_id，不关心它们的顺序排列。

---

### 9. alive_sessions 写互斥

**问题：** `alive_sessions` 是内核维护的内存数据结构，`process_session_ctrl_event`（每循环写一次）和 `check_alive`（发现过期数据时会修改）都可能写它。

**解决方案：** 内核是单线程轮询循环。所有内核函数串行执行，天然互斥。不需要锁。但如果将来内核改为多线程，则需要给 `alive_sessions` 加 mutex。

---

### 10. `--append-system-prompt` 不能替代初始消息注入

**问题：** `claude --append-system-prompt "xxx"` 修改的是系统提示，但 CC 仍等待人类输入第一行。它不能自动触发首轮处理。

**解决方案：** 不使用 `--append-system-prompt`，改用 `claude <prompt>` 位置参数作为首条消息。实测验证有效：CC 处理首条消息后进入 REPL，不退出。

---

### 11. 内核懒启动的生命周期与竞态

**问题：** 内核是懒启动守护进程（check_core 按需拉起、空闲自杀），生命周期管理引入三类竞态：启动竞态、启动握手、退出竞态。`core_status.json` 结构为 `{status: 0|1, pid, start_time}`。

**(a) 启动竞态——两个工具同时调 check_core：**
- 互斥原语用 `filelock` 包（纯 Python、跨平台），不要自搓 msvcrt/fcntl 两套。
- 获锁方读 status，判定需启动则拉起 kernel、等 ready 信号、写 status=1+pid+start_time、释放锁；无锁方阻塞等锁，获锁后读到 status=1、验活通过、直接返回。单实例由此保证。

**(b) 启动握手——工具不能在 kernel 未就绪时写 queue：**
- check_core 启动 kernel 后不立刻返回，轮询 core_status.json 直到 kernel 写回 status=1+pid+start_time（ready 信号），带超时。
- Kernel init 第一步即写此文件，确保握手尽快完成。

**(c) 退出竞态——kernel 自杀与工具写 queue 的小窗口：**
- 场景：工具读到 status=1、验活通过 → kernel 此刻决定退出 → 工具写 queue 请求 → kernel 已死 → 请求永不被处理。
- 兜底 1（kernel 侧）：退出条件含 queue 为空（已写入正文）。kernel 写 status=0 前再扫一次 queue，非空则撤销退出、继续循环。
- 兜底 2（工具侧）：工具等响应必须带超时；超时则重跑 check_core（发现 kernel 没了 → 重启 → 重新提交 queue 请求）。工具侧永远要有 response 超时 + check_core 重试，不能无限等。

**持久化与恢复：**
- Kernel 退出时将 alive_sessions 落盘到 `data/` 下快照文件。
- Kernel init 时若发现快照，加载之；随后 replay `session_ctrl/` 事件增量更新——事件日志是 ground truth，快照只是加速恢复。
