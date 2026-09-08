# Fairy V3 稳定性恢复实施记录

依据：[全项目审查及补充章节](2026-09-08-project-stability-usability-audit.md)。用户已批准 Phase 0–8；此文件追加实际证据，不覆盖审查历史。

## 固定约定

- 工作目录 `D:\桌面\~\deskllmchat\fairy-v3`，本地分支 `codex/fairy-stability-recovery`。
- 保留 DSH/SVG；不触碰 CLAUDE.md，不上传，不运行 Docker/Release，不用子智能体。
- 默认本机完整桌宠效果，远程兼容手动开启；模型无活动消费者/排队请求 5 分钟后释放。
- 命令、模型、会话权威在 Core；Rust 管原生与传输；TS 管交互展示。Scope/Approval 不绕过。
- 每个修复先红后绿；真实 Provider/WSL/PostgreSQL/原生验收单列，不能用单元测试代替。

## 阶段跟踪

| 阶段 | 工作 | 状态 |
| --- | --- | --- |
| 0 | 分支、用户改动恢复副本、独立 DSH 基线 | 完成 |
| 1 | 录音 Scope；Browser revision；隐藏面板；桌宠启动 | 实现及完整桌面自动化通过；原生未验收 |
| 2 | 持久执行意图、目标、副作用约束、多入口一致性 | 策略/Steering/入口接线已实现；复合 objective 的逐节点完成凭据与 Phase 6 联合闭环 |
| 3 | 单 reader RPC、控制通道、期限、协商及事件推送 | 请求分流与事件通道已实现；取消控制及原生联合待闭环 |
| 4 | 宿主 broker、桌宠脱离主 UI 生命周期、统一领域命令 | Core 文本入口实现中；宿主/界面/统一斜线命令待接线 |
| 5 | 空闲退避、批量查询、有限历史、Browser 资源预算 | 未开始 |
| 6 | 新版真实模型/工具节点，全局并发与恢复 | 未开始 |
| 7 | 音频焦点、模型空闲释放、资源预留、DDA 生命周期 | 未开始 |
| 8 | 全面联合验收、诊断、架构与恢复文档、缓存维护 | 未开始 |

## 验收矩阵

- 录音：A/B 双向切换、权限迟到、stop/转写迟到、Profile/Task 改变、卸载；原 scope 不写新聊天，音轨取消且旧回调不得改 UI。
- Browser：session/tab revision 变化后导航；旧 Element Ref 拒绝；隐藏 Settings/Inspector/标签后截图归零，恢复一次；后台动作继续。
- RPC：10 秒慢请求中控制确认小于 1 秒；乱序响应正确关联；超时不假称已取消；进程重启拒绝旧 generation。
- 意图：解释不执行、审查不修改、引用/否定保真、目标歧义澄清、Steering 旧约束停止派发、Classifier 降级不扩大权限。
- Workflow：4 个全局执行节点、普通2/Deep4；跨 Run 公平、读并行写串行、工具结果排序、审批/重启/副作用不确定恢复。
- 资源：空闲 SQL 降低90%；最近历史读取量有界；4 个驻留 Browser Session/12 个全局 Tab；活动资源不驱逐，空闲5分钟释放。
- 原生：双窗口/双聊天、主 WebView 重载、Native/SVG 热切换20次、本机完整和手动远程兼容、无残留属性/进程。

## 执行证据

### 2026-09-08：基线保护

- 起点 main `98f2f4ab438f96c4daf20e75d3be8aba0b291bf6`，12 个已修改跟踪文件及 DSH 未跟踪源码/许可。
- 创建修复分支，36 个源码/许可/设计/审查文件复制至 ignored 的 `.tmp/recovery-baseline-20260908/`，逐文件 SHA256 比对一致。
- 未复制凭据、数据库、模型、构建目录或 CLAUDE.md。当前未进行 Schema 迁移，因此尚无需数据库一致性备份；任何迁移开始前必须先做。
- 只读进程检查未发现项目 Node/Python/Cargo/Fairy/Browser Worker；没有停止无关进程。
- DSH 保存提交是现状检查点，不宣称完整桌面门禁通过；原审查中的 DualSurface 异步启动失败留待 Phase 1 修复。
- 已单独提交 DSH `660945c94`，TypeScript 通过，DSH/Render Settings 3 文件14测试通过，未上传。

### Phase 1 提交与完整回归

- `8155c2dd2`：F01 录音 generation/作用域隔离。
- `14f511738`：F04 Browser snapshot/tab revision 一致性。
- `2ee83e0ae`：F03 隐藏 Browser 视觉查询暂停。
- `fa1b72c63`：F11 异步桌宠启动契约门禁。
- 最后一次 TypeScript 通过；`npx vitest run --maxWorkers=2`：105文件、601测试全部通过（71.04秒）。
- jsdom Canvas context 警告属于模拟环境限制，未通过安装新包或吞掉日志伪装为 GPU 实测。真实麦克风、Playwright、原生 WebView2/远程显示仍待对应联合验收。
- Phase 2 已完成执行入口和存储扩展点核对，尚未修改其生产代码。现有 interpretation、工具候选、审批恢复、工具执行、Schedule/Steering 必须同时接入约束，不能只过滤工具列表就标记完成。

## 恢复方法

### Phase 2A：执行意图持久化验收契约

- 每次追加 interpretation 时，在同一事务内保存执行意图快照；绑定 tenant（行隔离）、Turn、Task、Conversation、Project、Workspace、base/target Version、执行目标、解释 Revision 和来源消息 SHA256。
- 逐 objective 保留动作、依赖和目标描述，保留禁止事项/澄清状态；Classifier 输出中的目标只是待解析描述，不转成 Scope 路径或扩大权限。该快照本身不是授权凭证。
- 最新读取以 Turn 的 active interpretation pointer 为准，不用最大历史 Revision 代替；显式旧 Revision 仅用于审计，不允许覆盖。双聊天/双租户读取隔离，错误来源及并发 Revision 追加必须整体回滚。
- SQLite 增量增加 nullable JSON 列，旧记录保持空，不猜测回填权限；新库及升级后重开均测试。Cloud 增加独立可逆 Alembic 迁移，离线 SQL 与真实 PostgreSQL 分开记录。
- 自动化使用临时 SQLite 实库、真实 Repository 和共享 Schema；不调用真实模型，不启动 Fairy、不迁移用户数据库。首次启动新版迁移用户数据库前仍必须一致性备份。
- 本子任务只建立存储契约；工具列表过滤、执行前检查、审批恢复、Steering 目标解析/失效属于 Phase 2 后续子任务，未接通前不宣称意图约束已端到端生效。

#### Phase 2 后续接线顺序

1. 按工具语义区分证据读取、受控内部缓存、用户可见修改和外部副作用；不能只看 `side_effect` 就把 research/Browser 的所有操作一刀切。
2. 由可信 Scope 解析目标；Classifier 的自由文本、置信度和降级结果都不能直接生成写权限。目标/禁令/复合 objective 未解析完成时保留只读查证及必要澄清。
3. Context 工具展示、`_execute_candidate`、`_execute_running_tool` 和审批恢复共用同一策略；旧 Revision 只能审计，Steering 后重新解析目标。约束缺失的旧 Turn 走明确兼容规则，不静默授予权限。
4. 用脚本化 Provider 验证“审查却调用写工具”、越目标调用、审批后更正、Classifier 降级与普通聊天/桌宠/STT/Schedule 一致性，再做真实 Provider 验收。

#### Phase 2A 实施证据（2026-09-08）

- 新增冻结的 `ExecutionIntentSnapshot`，在现有 interpretation 行保存 nullable JSON，与解释及 active pointer 同事务提交；新增 getter 默认只读 active Revision，保留显式历史审计读取。
- 增量 SQLite 迁移可重复执行，旧行保持 SQL NULL；Cloud Alembic head 为 `20260908_0054`，支持离线升级/降级。未运行用户数据库迁移，未启动 Fairy，未做真实 PostgreSQL 验收。
- 先复现解释无执行绑定、版本可跳号；修复后覆盖复合 objective、禁止事项保留、双聊天/双租户、重开恢复、并发旧 Revision、错误来源、缺失/部分/未知版本 JSON。现有消息关联约束已能阻止非法跨 Scope 行写入，没有绕过数据库约束来伪造复现。
- `.venv/Scripts/python.exe -m pytest tests/assistant tests/test_sqlite_core.py tests/test_persistence_recovery.py tests/test_jsonrpc_transport.py tests/test_stdio_transport.py -q`（cwd=core）：173 passed，93.52秒。
- `.venv/Scripts/python.exe -m pytest tests/test_deployment_contract.py -q`（cwd=cloud）：37 passed；包含新迁移的离线 SQL 门禁，不能替代真实 PostgreSQL。
- Core 与 Cloud 分别在包目录运行受影响文件 Ruff：通过；`git diff --check` 通过。新增11个 Core 场景（10个绑定/隔离/损坏场景＋1个 SQLite 升级场景），新增1个 Cloud 离线迁移场景；仅修改原 Cloud head 断言以对应新增迁移，其余既有断言未放宽。
- 本轮没有改 Desktop/Rust，也未重跑其门禁；Phase 1 的桌面结果保留为历史证据。工具执行前策略尚未消费此快照，Phase 2 仍为进行中。
- 结束时只读进程检查未发现本项目 Node/Python/Cargo/Fairy/Browser Worker 残留；未停止无关进程。

### Phase 2B：执行策略接线（进行中）

- Context、候选派发和实际执行消费同一持久意图；后两者重新读取 active Revision。CommandRun 输入保存解释 Revision；审批恢复到真正执行之前比较，旧审批不能恢复新要求已经撤销的动作。
- 回答/解释/审查、低置信度、待澄清默认只读；允许有明确 builtin 来源和 executor 的研究/计划内部写入。按 action 区分修改、运行、媒体、浏览器、管理工具，未声明语义的第三方写工具不自动获得权限。
- 原文中明确的“不修改/不执行/不通知/不保存记忆”形成额外收紧条件；引用/代码不作为此类指令。这是保守否定守卫，不能替代 Classifier 的语义理解，不宣称覆盖任意自然语言。
- 文件 Changeset 的相对目标匹配与目录边界在派发及执行两处检查；Core Scope 仍负责真实路径、版本和权限。目标不明返回公开意图错误，不能直接猜测访问范围。
- 系统操作默认识别明确指令；提及 notification、引用示例与否定文本不再授予调用。无 Classifier 的 legacy profile 只为已识别的直接系统命令绑定相应 tool target，其余仍只读降级。
- 修改一项既有重启审批测试的输入，从含糊的“Resume this approved notification action”改成明确“Notify me after approval, including after restart”；审批、重启、幂等断言保持不变。
- 新增策略矩阵、引用/否定、文件目标和审批过期回归；此前一次整组失败发现实际执行读取了错误的 CommandRun 字段，已按权威 `input_payload` 修复并通过定向恢复测试。未吞掉异常、未放宽执行结果断言。
- Steering 重新识别新来源消息并清空旧路由；识别输入持有解释 Revision，绑定结果在同事务比较，旧识别结果不得绑定新消息。首次识别中途更正保存低权限 pending Revision；旧 Profile 更正重走同一只读/直接系统命令规则。
- 预算审批幂等键带解释 Revision，不复用旧要求的路由预算授权；已完成操作保留，不回滚其事实。
- 删除 Schedule 的独立 legacy 执行解释构造器，实际 Turn 与普通聊天使用相同准备路径。Schedule Card 的计划描述与来源哈希校验保留，不再把描述层的词法 action 当执行权限。
- 新增首次识别/回复中 Steering、旧 Profile 通知撤权、Schedule 与普通聊天等价测试，全部有对应失败复现。既有 Steering 测试参数化增强断言，没有放宽暂停/消息唯一性/幂等检查。
- 验证：Core cwd 执行 `ruff check src/fairy_core/assistant tests/assistant` 通过；`pytest tests/assistant tests/test_sqlite_core.py tests/test_persistence_recovery.py tests/test_jsonrpc_transport.py tests/test_stdio_transport.py -q --tb=short`：218 passed（100.54秒）。均为临时数据库与脚本 Provider；没有运行真实用户库迁移、网络模型、桌面或原生硬件。
- 复合 objective 当前仍只保存依赖，尚无可信逐 objective 完成凭据；不能把执行模型自报“分析完成”作为修改授权。该部分与 Phase 6 真实节点共用实现，联合关闭 Phase 2，不能提前宣称本阶段完全完成。真实 Provider 与原生联合验收仍未运行。用户要求连续推进，提交不是停点。

### Phase 2C：MCP 明确动作与下游执行门禁

- 复现已解析 HIGH/Create 且目标为精确 MCP 工具名时，扩展工具守卫仍无条件拒绝，导致标准模式无法进入既有审批。现仅对已绑定服务器命名空间的精确 MCP 目标允许继续进入 Scope、Schema/信任和审批检查；只读、低置信、其他服务器、泛化工作区目标和发布禁令仍不授予该路径。
- Auto/Manual Classifier 输入增加最多64个已注册 MCP 写入/执行工具名；目录只含启用、已接受且就绪工具，不含凭据、端点和参数。目录明确是数据而非指令/授权。新增真实路由→持久解释→MCP 审批集成场景，批准前外部调用为零，批准后只调用一次；模型与外部服务为脚本边界，未作为真实 Provider 验收。
- 原 MCP 两项写入测试改用明确创建指令并绑定有效解释，Sandbox 下游测试补齐有效 RUN 解释前置条件；新 helper 只供下游执行测试使用，Classifier/解释测试不走此捷径。未修改原审批、Scope、归档、取消、结果不确定与不重复副作用断言。
- 验证：MCP/Sandbox/意图策略57项通过（17.55秒）；真实路由组件的 MCP 集成1项通过（2.76秒）；随后 Assistant/MCP/Sandbox/stdio 并发整组275项通过（115.44秒），Ruff与 diff check 通过。旧失败缓存中的两项跨作用域用例单独及本轮整组均通过，未据缓存伪造新缺陷或修改其断言。Cloud HTTP/部署契约前一子任务54项通过（14.93秒）。
- 仍未关闭 Phase 2：精确工具名不是任意外部账户/参数目标的完整语义验证，复合 objective 完成事实、审批等待中 Steering 和真实模型兼容仍待后续；不能宣称高置信 Classifier 可以扩大原始用户授权。

### Phase 3A：请求分发验收契约

- Phase 2 独立补漏：Classifier 只有路由/证据字段、缺失完整 interpretation 时，不再由 `requires_workspace_changes` 推导出中置信执行权限；保留路由与查证信息，但意图降为只读。正式双聊天回归修复前能放行 `run.sandboxed`，修复后拒绝执行/安装且保留 `project.read`。测试使用真实路由、临时持久化和 Context，仅模型输出脚本化；未修改完整 Classifier 或显式 legacy 系统命令的语义。Core Assistant 188项通过（85.95秒），受影响 Ruff 通过。最初测试误入最终回复的既有工作区完整性门禁，已收窄为所审查的路由→持久意图→工具暴露链，不将该失败冒充权限复现。

- 保留单 stdio 入口和未协商客户端的顺序响应。`transport.negotiate` 是本地传输能力，不是业务授权；只有明确协商后开启并发请求，事件推送尚未实现时不得宣称支持。
- 一个控制通道仅接已核对的轻量状态请求，两个显式只读 Worker，一个默认串行 Worker；各通道排队数量有界，满载返回公开 `RPC_CAPACITY_EXCEEDED`，绝不悄悄丢弃写操作或自动重试。
- 响应在单一写锁内完整输出。请求 ID 持有至响应发出，活动重复 ID 明确拒绝。读取线程不等慢业务完成，控制通道不启动模型、Browser、Voice 或同步资源清理。
- 测试用同步 Event 阻塞慢请求，确认控制/读取响应先返回；未声明方法保持串行；饱和、重复 ID、EOF 清理和旧协议顺序分别验证。随后 Rust/真实进程门禁验证乱序、代际和期限，再实现持久事件通知；此处测试不能单独证明桌面响应已恢复。
- 实施：Rust 单 reader 按宿主 generation/唯一 wire ID 分发；有界写队列由唯一 writer 输出，调用线程不持有覆盖等待期间的锁。原始客户端 ID 只在回复该调用者时恢复；主窗口与 Realtime Assistance 的宿主外层锁也已释放后再等待。队列满明确未派发，超时明确操作结果可能未定，不自动重试。
- Python 每通道最多32个运行/排队请求；未声明方法默认串行。响应管道断开后不再启动已排队副作用；原 Core 和组合版入口均在流结束/异常时关闭 dispatcher。控制通道目前仅 `health`/`assistant.turns.pause`；取消仍有同步领域清理，待下一子任务拆分，不宣称取消延迟门禁通过。
- Rust Bridge 测试11项通过，Clippy（仅 core-bridge 全 targets）通过；宿主 `cargo check -p fairy-desktop-v3 --lib` 通过。新增真实 Core＋实际 stdio/Bridge 联通测试：领域边界注入10秒延迟时，健康检查每次<1秒（测试总12.13秒）。这是本机进程级传输证据，不是 WebView2/真实 Provider 联合验收。
- Core stdio/JSON-RPC 定向20项通过；Capabilities stdio3项通过；TypeScript 通过，Client/Tauri/Cloud Transport 30项通过。新增 EOF/满载/重复 ID/未知 get 串行/断管不再派发场景；既有低于50ms轮询常量断言按批准方案改成250–500ms初始间隔，并补10秒空闲最多7次请求、取消后无计时器/迟到事件的行为测试（修复前401次）。
- 默认请求30秒，显式 Voice/Browser 长操作120秒；超时返回 `RPC_DEADLINE_EXCEEDED`，不会映射成可重试的启动失联。未协商推送的客户端退避至5秒，读取到新事件后重置。事件 watch、提交后唤醒、慢订阅者重同步仍未实现，Phase 3 尚未完成。

### Phase 3C：取消接受与底层停止

- 补充重启验收：独占本地数据目录的新 Core 启动时，已取消 Turn 遗留的模型流不能永久保持 stopping。只收敛本进程已经不可能持有的 Provider Attempt，保留 usage/费用未知信息；仍运行中的工具尤其外部副作用不得猜测成功或重放。普通恢复调用和 Cloud 多宿主恢复不能使用这项独占本机规则。用关闭并重开临时数据库、两个聊天及保留工具未定状态验证。
- 已复现并修复该模型流重启卡死。恢复查询按 tenant、local Task、cancelled Turn 和 started Attempt 限定，每批最多100项；仅 `build_local_service` 持有 DataDirectoryLock 后调用。模型费用与已知 usage 保留，结果以 unknown 失败收敛；工具 running 状态未改动，没有调用 Provider 或工具。两租户/两执行目标查询门禁先发现 Cloud 记录被选中，补齐目标过滤后通过。
- 验证：关闭重开、双聊天、跨租户/执行目标、批量上限、既有恢复/取消/stdio/Repository 共37项通过（26.29秒），Ruff通过。本测试直接构造崩溃遗留的合法持久记录，不宣称实际断电或真实外部服务停止已验收；未定工具的恢复决策界面仍是剩余工作。

- 取消请求先做持久 Turn Revision 校验，再信号化 Kernel；WSL/MCP 等停止清理由 Core 所有的单 Worker、最多32个请求的有界生命周期队列处理。该队列只停止已经存在的 CommandRun，不创建模型/工具业务，不负责 Workflow 调度或重试。
- 队列容量在提交取消前预留，容量不足不能取消一半再报告未接受；重复停止不改变原 CommandRun 绑定。关闭 Core 先停止接收并排空停止信号，再关闭 Workflow/领域运行时。
- Turn 的 cancelled 兼容字段保留，额外只读停止投影以持久运行中调用为依据；底层调用没有退出时不能显示已经停止，也不能把收到停止信号当作副作用回滚。进程重启后的未定调用必须保留恢复决策，不能自动重放写入。
- 验证真实 CommandBus/临时数据库下阻塞工具＋阻塞 cancel hook，取消确认<1秒、另一聊天仍可读、迟到结果不写成回复、停止投影在实际退出后收敛；验证容量拒绝在任何状态修改前发生、Core close等待己有清理、过期 Revision不触发清理。脚本工具不替代真实 WSL停止门禁。

- 取消版本竞态独立修复：旧代码先调用 Scheduler.cancel 再校验 Turn revision，新增阻塞 Provider 回归实际观察到“RPC 拒绝但 Workflow 已取消”。现改为先事务提交版本校验后的 Turn 取消，再触碰 Worker/领域运行时；相同请求遇到已经提交的相邻取消 Revision 可幂等收敛。过期请求不改变任务，释放 Provider 后仍能正常完成。取消/Workflow/Assistant 定向13项通过（17.03秒），Ruff通过。领域停止仍为同步，此项不冒充快速取消门禁。

- 停止传递链独立复现：Core 实际组合的 Project/Memory/Knowledge 包装层会截断 `cancel_command`，底层工具已运行但停止钩子未收到信号。补齐这些层及 Browser/Document/Research/System/ProjectExecution/Media 的透传，原样携带已授权 CommandRun，不按名称创建新操作。实际组合链回归通过；本轮扩大门禁另外发现旧 Sandbox/MCP 写工具测试没有有效执行意图前置条件，不能把这些未通过场景算作停止链验收完成。
- 已实现有界停止队列、停止投影、接受事件与前端展示。停止钩子阻塞时取消确认<0.9秒；钩子返回但实际工具尚未退出时仍保持 `cancellation_pending`，不生成迟到回复、不允许同聊天新 Turn/重试/严格项目清理。投影来自持久 Tool Invocation/Provider Attempt，不依赖前端计时器或进程内“已发送停止”标记；不新增数据库迁移。
- Core close与已预留取消请求的竞态先失败复现，现等待预留提交后再排空停止队列；控制通道明确加入 `assistant.turns.cancel`，有阻塞普通请求下的透传回归。严格删除路径仍等待领域停止，未定时报告 ProjectBusy，不把接受信号视为物理停止。
- 原取消版本契约复验发现需要区分“处理期间另一写者完成取消”和“重复请求旧 Revision”；已保留前者容错、恢复后者拒绝，没有修改原断言。`assistant.turns.run` 现在等待实际停止投影收敛，再返回同步执行结果；异步取消接口保持快速确认。
- 验证：Assistant＋JSON-RPC/stdio 213项通过（98.41秒）；之后新增取消事件与控制通道回归16项通过（7.40秒），取消/调用结果定向6项通过；Ruff通过。TypeScript通过，聊天取消/工作链/Activity Rail 39项通过；App/ChatWorkspace扩展70项通过。4处既有TS夹具仅补 `cancellation_pending:false`；stdio既有场景扩展pause/cancel参数，未放宽断言。迟到取消确认跨聊天覆盖先通过移除generation守卫复现，再恢复守卫验证。
- 剩余风险明确保留：重启时未定副作用的人工恢复决策与最终原生/WSL验收尚未完成；不能把本机阻塞脚本工具当作真实WSL。扩大跨域门禁曾出现2个MCP与7个Sandbox意图前置失败，继续独立补齐；视频重启场景整组运行1次超过3秒、单独2次通过，列入后续调度负载/恢复复查，不增加测试超时掩盖。

### Phase 4A：Core 消息入口验收契约

- 将文本提交的 Conversation→Task→Turn→启动编排收进 Core 的单一入口，复用原 Task/Turn 幂等键、解释、CommandBus、Scope 和 Kernel，不创建第二套工作队列。入口先验证会话及模型绑定，再准备 Task；相同请求重放返回原 Turn，不因 UI 重载生成第二条消息。
- 提交必须显式包含 Conversation；缺少绑定不能读取另一窗口瞬时选择。Pet 来源只允许普通 scratch 聊天，不能通过该入口携带项目编辑模式或临时附件。来源标签用于审计，不构成授权，Rust 后续以真实窗口身份确定标签。
- 同聊天有活动或 stopping Turn 时，新提交在创建 Task 前拒绝；不同聊天不串绑。网络回复丢失重试、相同键不同内容/会话/模型的冲突、模型失效无残留 Task、源标签和项目隔离均用真实 Core/临时数据库测试。
- 本子任务建立 Core 入口；在 Rust 受限 IPC 和 Pet 前端接通之前，不宣称主 WebView 重载场景已解决。`/new`、`/clear`、`/stop` 的统一领域分发仍随后实施。
- 已实现 local-only `assistant.messages.submit` 和受限 DTO，复用当前模型解析/Task 创建/Turn 解释与 Kernel 启动。模型缺失或禁用在创建 Task 前拒绝；四路同键并发只生成一条消息/一个 Turn，活动聊天不新增 Task。重放返回原状态，不能隐式解除暂停。
- 在 Task 创建后、Turn 创建前注入故障，先复现同键可更换来源/模型的问题；增加 tenant＋key SHA256 唯一回执，保存请求 SHA256、Conversation 和时间，不重复保存原文。SQLite `create_all` 增量建表、旧库缺表升级与重开/外键检查通过；Cloud 新增可逆迁移 `20260909_0055`，只做离线 SQL 验证。真实用户数据库尚未启动或迁移。
- 定向9项通过（10.33秒）：端到端回复及重启重放无额外模型调用、并发重复、双聊天、项目 Pet 拒绝、暂停保留、部分提交、失效模型、两租户回执/升级。原新测试曾用关闭 Scheduler 模拟暂停，实际会触发 closing 错误；改用真实阻塞 Provider，并在暂停竞态测试仅持有派发锁，不替换数据库或执行策略。
- 扩大 Core 门禁233项通过、1项因未生成 RPC manifest 失败；按既有脚本生成 manifest 后 JSON-RPC 11项通过，没有放宽 catalog 断言。Cloud HTTP/部署55项通过（16.47秒）；TypeScript及Client/Cloud/Tauri传输35项通过；相关 Ruff通过。旧 Cloud head断言只更新新增迁移，增加离线可逆门禁。后续最终全量门禁仍需统一复跑。
- 剩余边界：旧前端的分步 Task/Turn API 尚未切换，跨旧新入口并发与宿主会话恢复仍待接线验证；本轮不将这个入口子任务当作 Phase 4 完成。

### Phase 4B：宿主桌宠通道验收契约

- Rust 仅缓存显式聊天绑定及当前 Turn 投影，不拥有业务数据库或执行队列。主窗口可以绑定已由 Core 验证的 scratch Conversation；Pet Input 只能提交文本及绑定 Revision，不能选择 RPC 名、Project、Scope、模型或自报角色。Pet Render 不能调用发送/取消。
- 所有 Core RPC 等待在宿主锁之外。发送开始固定绑定 Revision，主窗口随后切换绑定时，旧调用可以完成原聊天持久化，但不得覆盖新聊天投影。取消使用宿主保存的精确 Turn ID/Revision，不解析另一窗口的当前 UI 选择。
- 自动化先验证双聊天切换、过期绑定/迟到结果、固定 RPC/来源/模型字段和窗口身份；宿主接线后再跑真实 Core Bridge/原生重载。纯 Rust 状态测试不能替代 WebView2。未绑定聊天的创建、发送中取消、终态推送和主窗口刷新仍需后续连续接通才可默认启用新通道。
- 已建立宿主绑定缓存与 `pet_chat_bind/context_get/submit/cancel` 受限 IPC：只有 main 可绑定，只有 pet-input 可发送/取消，pet-render 未获得读写 Core 权限。参数由宿主生成，不接受 Pet 自报 Conversation/模型/来源或 RPC 名。等待 Core 响应期间不持有绑定锁。
- 已验证旧绑定、迟到发送与旧请求释放不覆盖新聊天；终态收到迟到 stopping 投影曾被重新打开，增加终态单向守卫后通过。已删除/已清除的聊天不能绑定；本项先失败复现再修复。
- Rust7项通过，含真实 Core 子进程、真实 Task/Turn/消息链及双聊天，脚本模型仅替代外部网络 Provider。模拟丢弃的是前端连接句柄，不冒充真实 WebView 重载。最初脚本导入失败源于开发启动选择 capabilities 工作目录，测试显式切至 Core 根后正常联通。现有窗口权限11项通过；Desktop全targets Clippy通过。
- 尚未将前端切至新通道，未启动真实 Fairy；终态/回复事件推送、未绑定时新聊天、发送中取消仍在后续列表，不能提前宣称桌宠通道已经端到端替换。

### Phase 4C：统一显式命令入口验收契约

- Core 解析完整显式斜线命令；引用、代码块、普通叙述和未知/多余参数不能成为命令。可用性使用当前 Registry/设备执行策略，不信任前端菜单缓存。
- `/new` 与 `/clear` 创建新的 scratch 聊天，绝不删除原消息。创建与幂等回执在同一数据库事务中提交；重复、并发及重启重放返回同一聊天，同键不同命令/上下文拒绝。回执只保存域分离哈希及会话绑定。
- `/stop` 必须携带明确 Conversation、Turn 和取消 Revision，跨聊天拒绝；仍复用原取消接受/实际停止契约。导航和权限命令只返回类型化 UI action，权限实际变更继续走可信宿主流程。
- 使用两个真实临时数据库聊天、并发请求、关闭重开、停错聊天及已删除聊天重放验证。不调用外部模型验证解析；实际主窗口/桌宠接线与 WebView2 验收随后完成。
- 已实现 local-only `assistant.commands.dispatch`，当前菜单可用性由 Core Registry 复核。新建/清空使用同事务创建＋回执，四路重放和重启返回原 Conversation；清空不删除旧聊天，删除后的旧回执不复活聊天。停止绑定 Conversation/Turn/Revision 并保存域分离回执；权限只返回宿主 UI action。
- Core 命令/消息入口/权限30项通过（29.93秒），命令＋JSON-RPC＋回执25项通过（20.37秒）；TypeScript、传输Client/Cloud/Tauri36项与 Ruff、diff检查通过。新权限测试先持久化设置再比较完整记录，避免未初始化默认设置每次读取生成时间戳的夹具问题。未改变已有产品权限断言。
- 前端还未切换；统一入口当前走默认串行传输通道，下一步必须对已验证的 stop 控制请求单独分流，避免与创建聊天的文件操作共享等待锁。原 `assistant.turns.cancel` 快速通道继续保留。原生重载仍未验收。

### Phase 3B：事件提交唤醒与推送验收契约

- 唤醒信号由同一 Core 的 UnitOfWorkFactory 持有，按 tenant 隔离；Ledger 写入成功提交之后才通知，回滚/普通只读提交不通知。信号只表示“重新读取 Ledger”，不携带消息正文，不取代持久游标。
- 本地 watch/unwatch 必须协商启用；响应和事件使用不同 envelope。每个订阅独立有界队列/游标，溢出进入显式 resync，终态与审批依靠持久回放补齐。主窗口卸载或取消清理订阅，迟到消息不能重建旧订阅。
- 验证双 tenant 提交/回滚隔离，双订阅各自游标，丢唤醒后的5秒持久补读，慢订阅者不阻塞其他窗口、unwatch及重连清理；真实 Rust→Core 联通后再接 Tauri，最后做原生主窗口重载验收。
- 已实现上述 Core→Rust 推送，每个订阅最多8个批次（每批最多64事件），最多8订阅；溢出进入持续 resync 状态，独立订阅不受影响。订阅者退订会唤醒正在等待的读取，迟到的旧 subscription ID 被忽略。
- WebView 通过独立 IPC 等待 Rust 队列（空闲最多30秒返回一次），不是轮询 Core 数据库，也不占业务 RPC 控制通道。主窗口 PageLoad Started 按捕获的旧 ID 清理；宿主以真实 window label 绑定所有者，不接受前端自报窗口身份，桌宠仍无通用 Core/事件权限。
- 新 Core 和旧 Core/旧宿主分别使用推送与250ms→5秒退避回退；正文回放继续经过原 EventCheckpoint 的 source/ledger/cursor/水位恢复逻辑，resync 抛出可识别错误，触发原有持久回放。
- Core 55项通过（23.26秒），覆盖提交/回滚、两个 tenant、两个订阅、独立写入丢失本机 hint 后补读、未来游标拒绝、协商与退订。Ruff 受影响模块通过。
- Rust Bridge 14项通过，含真实 Core 双订阅、提交推送、退订重开回放；Core Bridge＋Desktop 全 targets Clippy 通过，宿主订阅隔离单测通过。新增联通测试最初使用了不存在的 `project.created`/`command.completed` 名称，核对 CommandBus 后改为实际 `command.output`，没有改生产事件契约来迎合测试。
- TypeScript 通过；Client/Tauri/Cloud/EventStream 44测试通过；App/WorkspaceShell/EventStream/Tauri 69测试通过。真实 WebView2 刷新、用户输入竞争、多窗口硬件场景尚待最终验收，不能由这些测试替代。
- 顺带发现并单独提交 DSH 测试 Clippy 清理 `f82f4b07f`：只改默认值初始化写法，3项原断言保持并通过，没有更改 DSH 样式。

### Phase 1 / F11：桌宠异步启动门禁（历史证据）

- 先复现原 projection-only 测试立即寻找尚未挂载 renderer 的失败；改为等待真实设置加载后的 DOM，不改生产渲染逻辑。
- 新增加载等待/live 优先、原生加载失败回退、启动中卸载三个行为测试，验证现有守卫而非增加无条件延时。
- TypeScript 通过，DualSurface/Render Settings 38测试通过。jsdom 无 Canvas context 警告仍明确存在，不作为 GPU/远程可见性验收。

### Phase 1 / F03：不可见 Browser 暂停视觉查询

- 新增 WorkspaceShell 集成失败测试：收起 Inspector 后 Browser active 仍为 true。
- App 的有效可见性经 Shell、Inspector、PreviewWorkspace 传至 BrowserPanel；收起/Settings 隐藏保持挂载但关闭视觉查询，恢复开启；地址草稿和 DOM 保持。
- 检查实际代码确认 Preview 标签离开时当前实现会卸载该面板，已有 cleanup 生效；本修复不改变这项既有标签生命周期，不停止 Core Browser Session/Agent 工作。
- TypeScript 通过，App/Shell/Browser Panel/hook 4文件57测试通过。原生后台 WebView 行为留待最终实机检查。

### Phase 1 / F04：Browser 页面版本

- 两个新增失败场景：导航忽略较新 snapshot，普通 reload 忽略较新 tab revision；统一比较同 Session/Tab 的已知版本。
- 恢复 session 不复用恢复前 snapshot；显式提交的 Element Ref/点击 revision 不改写，不自动重放动作。
- TypeScript 通过，Browser hook/Panel 11测试通过。真实动态页面端到端验收留待联合门禁。

### Phase 1 / F01：录音作用域修复

- 正式 VoiceController 测试新增8个场景，双向聊天切换、权限迟到/卸载、stop迟到、转写迟到、同聊天 Profile/Task 变更均先确认失败。
- 每次录音持有固定 Conversation/Task/Profile 和 generation；布局提交时使旧请求失效并取消媒体 session，异步边界逐次校验，旧 finally 不修改新录音状态。
- TypeScript 通过；VoiceController + App 回归38测试通过。模拟麦克风/RPC，真实麦克风尚未验收；已发往 Core 的转写结果被忽略，不宣称底层请求已物理取消（传输取消属于 Phase 3）。

恢复副本采用仓库相对路径保存，先比较目标文件与备份，只按确认范围恢复；禁止整目录覆盖或重置工作树。后续各修复使用独立提交，可通过新的 revert 提交回滚；含 Schema 的阶段必须配合兼容迁移或一致性数据库备份，不能只回退源码。
