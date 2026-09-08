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
| 2 | 持久执行意图、目标、副作用约束、多入口一致性 | 进行中：先建立不可变绑定及恢复契约，执行拦截尚未接入 |
| 3 | 单 reader RPC、控制通道、期限、协商及事件推送 | 未开始 |
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

### Phase 1 / F11：桌宠异步启动门禁

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
