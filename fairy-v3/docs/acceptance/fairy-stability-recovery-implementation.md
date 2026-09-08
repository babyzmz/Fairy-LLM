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
| 4 | 宿主 broker、桌宠脱离主 UI 生命周期、统一领域命令 | 未开始 |
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
