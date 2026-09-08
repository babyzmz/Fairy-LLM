# Fairy V3 全项目稳定性、性能与功能联动审查

日期：2026-09-08。状态：第一轮代码与自动化审查完成；发现项未修复，不作为发布验收。

## 结论

Fairy 已有较完整的领域能力、Command/Approval/Scope 治理和恢复设施，但模块之间的执行、可见性、资源占用与会话生命周期尚未完全贯通。当前最需要的不是继续增加功能，而是把已有能力整合为可预测、可停止、可恢复且有资源上限的用户流程。

用户描述的“很多功能存在，但不好用、不稳定、消耗异常”，有可验证的代码依据。不能把所有现象归因于模型，也不能归因于最新 DSH 外观改动：本轮发现同时涉及既有 Core 通信、Workflow、Browser、Voice 和前端状态。

本轮没有修改业务实现，没有删除数据，没有提交或上传，也没有启动模型、录音或桌面原生应用。只增加本报告及隔离复现文件，保留用户原有改动。

## 1. 审查基线与边界

- 实际仓库：`D:\桌面\~\deskllmchat`；实际项目：其下 `fairy-v3`。
- 分支：`main`，HEAD `98f2f4ab438f96c4daf20e75d3be8aba0b291bf6`。历史交接中的 `.worktrees/fairy-v3` 已不存在，不再作为命令目标。
- 对远端 heads 的本次只读查询显示 `origin/main` 与本地 HEAD 相同；当前 DSH/眼睛形态等更新仍包含未提交文件，不能据分支同步就称“最新改动已备份”。
- 未触碰 `CLAUDE.md`、用户备份或已有修改。
- 横跨 Desktop/聊天/Inspector、Browser/Preview、Assistant/Workflow/Schedule、Knowledge/Obsidian/Memory、Voice/Realtime、Native Presence、Cloud 契约和开发资源管理检查；不是逐行穷举全部源码，也不是所有硬件功能的实机验收。
- 本轮不做 Docker、Release、生产构建；未运行真实 PostgreSQL/S3、WSL Sandbox、Provider、GPU/音频/远程桌面验收，未复跑 Rust 或完整 Playwright。

证据分级：**A＝隔离复现或本轮测量；B＝代码路径确认；C＝仍需原生/真实服务验证的风险**。A 的组件测试使用模拟 I/O，不等于真实麦克风或真实浏览器端到端测试。

## 2. 问题清单

### F01 / P1 / A：录音会话与聊天切换未隔离

位置：`desktop/src/voice/VoiceController.tsx:184`、`:214`、`:228`。

- 切换 conversation 只停止播报，不停止当前录音。
- 录音对象保留在 ref；停止录音时使用当前渲染的 `conversationId`，而非开始录音时的 ID。
- 隔离复现：A 聊天开始录音，切到 B，点击停止，实际向转写接口传入 B 的 ID。
- 影响：A 的音频被归入 B 的请求作用域，转写回调还可能与保留的旧回调组合，属于跨聊天正确性问题。

改进：录音会话固定 Conversation/Task/Profile 与 generation；切换、卸载、取消统一结束该会话。麦克风权限迟到、转写迟到、播放器完成回调均校验 generation。权限请求迟到导致资源泄漏是待补测的相邻风险，尚不作为本轮已复现事实。

验收：A/B 快速切换、权限弹窗期间切换、转写中切换均不得写入另一个聊天；残留音轨为零。

### F02 / P1 / B：Core 通信仍存在全通道队头阻塞

位置：`desktop/src-tauri/src/lib.rs:1303`；`desktop/src-tauri/crates/core-bridge/src/lib.rs:277`；`core/src/fairy_core/transports/stdio.py:335`；`desktop/src/core/tauriTransport.ts:209`。

- Tauri 调用持有 Core/进程互斥锁，写入一个请求后阻塞读取一行响应；读取路径未提供请求级 deadline。
- Python stdio 循环同步 dispatch 后才读取下一请求。
- 前端 AbortSignal 检查和启动重试不等于取消已进入 native bridge 的请求。
- 因此即使 Assistant 使用后台 Workflow，一次慢 Browser snapshot 或其他同步 RPC 仍可能阻塞后续状态查询、设置和取消操作。此处确认的是架构路径，本轮未制造真实 WebView 卡死。

改进：先把昂贵请求改为短确认加异步结果/状态；再为桥接建立请求 ID 多路复用、超时、失联检测和取消/状态通道。不要仅用前端 Promise.race 隐藏仍阻塞的后台调用；副作用结果不确定时继续遵守现有恢复决策。

验收：注入 10 秒慢操作时，独立轻量状态请求和取消确认仍有可测上限；超时不留下永久锁、不重复执行副作用。

### F03 / P1 / A+B：隐藏 Browser 面板仍维持高频完整快照

位置：`desktop/src/app/BrowserPanel.tsx:26`；`WorkspaceInspector.tsx:170`、`:267`；`workspaceBrowserModel.ts:61`；`desktop/src/core/client.ts:437`；`desktop/browser-worker.mjs:143`、`:660`。

- BrowserPanel 仅在挂载/卸载时设置 surfaceActive。
- Inspector 收起、切换标签使用 hidden/inert 保持挂载，未把不可见状态传递给 Browser 查询。
- 隔离复现隐藏后 active 标记仍为 true；对应 hook 对活动 session 以 750ms 周期查询 snapshot。
- 客户端默认 `includeScreenshot=true`；Worker 执行页面语义快照、引用整理和 PNG 截图/Base64。理想短请求下约 80 次/分钟，不代表真实运行必定达到该速率。
- 这是“保持状态”错误地等同于“保持工作”的具体例子。页面内部 hidden 也不等同于整个 WebView 进入浏览器后台。

改进：贯通 App/页面/Inspector/标签/Browser 的有效可见性；保留 session 和 UI 状态，但不可见时停止可视快照。截图和语义信息分离；按页面变化、操作完成和可见需求更新，而不是持续完整抓取。

验收：关闭 Inspector、打开 Settings、离开 Browser 标签后截图请求归零；恢复时单次同步，不丢 session、滚动、Tab。后台 Agent 正在执行的必要浏览器操作不受视觉暂停影响。

### F04 / P1 / A+B：Browser 地址导航使用过期页面 Revision

位置：`desktop/src/app/workspaceBrowserModel.ts:108`、`:155`；`core/src/fairy_core/browser/service.py:380`；`desktop/browser-worker.mjs:577`。

- Snapshot 可以更新页面 revision；导航却使用 sessions 缓存中的 `tab.revision`。
- 成功抓取新 snapshot 不同步该 sessions 缓存；另一路通用 action 已优先采用 snapshot revision，两条路径不一致。
- 隔离复现：session revision=1、snapshot revision=2，导航仍提交 expected revision=1。
- 后端/Worker 对版本进行校验，因此动态页面可能在正常使用中触发版本冲突。

改进：Session+Tab 下的最新页面版本使用统一权威；导航和 element action 一致使用有效 revision。冲突后刷新并明确反馈；不得把任意表单/副作用动作自动重放。

验收：页面自动变化、切 Tab、刷新后导航正常；过期 Element Ref 仍严格拒绝，不能为改善体验取消版本安全检查。

### F05 / P1 / B：Workflow 节点还未真正接管完整工具循环

位置：`core/src/fairy_core/assistant/workflow_plan.py`；`workflow_adapter.py:118`、`:130`、`:202`；`application.py:476`；`parallel_tools.py:51`。

- 图中已有解释、路由、模型、工具、汇总、验证和完成节点。
- 但 model 节点调用 `application.run_turn()`，内部完成模型/工具循环，直到 Turn 完成或进入审批等边界。
- 随后的 tool invoke/join 主要检查、排序已经执行过的调用，verify/finalize 检查已经产生的回复；并不是每轮工具独立领取、执行、汇合。
- 工具并行批次另建 ThreadPoolExecutor，父 Kernel worker 等待结果。因此“Kernel 4 个 worker”不等于“全系统最多 4 个工具操作”。

影响：进度阶段与实际耗时不完全对应；工具级公平性、资源上限、暂停/修订和重启恢复粒度没有完整落实到节点。现有持久化 invocation、审批和 continuation 恢复并非不存在，不能把整套 Workflow 说成无效。

改进：把真实模型轮次、工具调用、fan-in、继续推理编译为可持久化节点；统一调度资源与并发预算。迁移前先固定执行引擎版本，保证同一 Turn 不双执行。不要为这项修复重写 Command/Scope/Approval 安全边界。

验收：两个 Run 的工具遵守全局上限和公平性；等待领域结果不占用可执行 worker；进度对应实际阶段；工具前后崩溃恢复、Steering、审批均无重复副作用。

### F06 / P2 / A+B：空闲调度和后台列表持续制造查询

位置：`core/src/fairy_core/workflow/scheduler.py:111`、`:266`；`workflow/repository.py:255`；`desktop/src/app/useAssistantScheduling.ts:77`、`:84`；`core/src/fairy_core/assistant/background_tasks.py:37`、`:107`。

- 默认 Kernel 100ms 唤醒并尝试 claim，空队列也查询。
- 隔离测量：空 SQLite 数据库、零 Run，3 秒内 112 次 SELECT，约 37.3 次/秒。这是 SQL 次数，不是整机 CPU 测量。
- claim 候选与活动项存在先 `.all()` 再 Python 过滤的路径。
- 聊天模式下后台任务和 Schedule 两组查询固定 2.5 秒轮询；列表构建再逐项读取 Conversation、Task、Occurrence 等，形成 N+1 查询。

改进：事件唤醒加下次到期时间、空闲退避；候选 SQL 有界且保持公平性；后台投影批量读取，前端事件驱动加低频恢复兜底。不能以减小心跳频率破坏 lease/fence 正确性。

验收：零任务稳定期查询显著下降；新任务启动延迟不恶化；长历史下 SQL 数不按每条展示任务线性增加；时钟变化、重启、续租仍通过。

### F07 / P2 / B：长历史读取方式不随使用规模收敛

位置：`core/src/fairy_core/assistant/context.py:294`；`desktop/src/app/workspaceModel.ts:135`。

- Assistant 构建历史上下文时以每页 100 条遍历整个 transcript，最后才截取最近 40 条。
- Workspace 也存在读取全部 Task 后按选中聊天过滤的路径。
- 用户历史越长，启动/切换/上下文准备成本越高；本轮未做万条记录耗时基准，不能报实际卡顿数值。

改进：Repository 直接提供有序最近 N 条查询；聊天/项目作用域分页、索引与游标；History 和 Graph 按需加载。Knowledge 内容读取与目录投影也纳入查询预算检查，避免为了计数或摘要读取全文。

验收：1千/1万/10万历史规模下最近上下文结果一致且读取量有界；切换聊天无旧数据与全局 loading 回归。

### F08 / P2 / B：Browser 资源池缺少与 Preview 对等的生命周期约束

位置：`desktop/browser-worker.mjs:10`、`:175`、`:254`；`core/src/fairy_core/browser/service.py`。

- Browser Worker 共用 persistent browser context，但为 session/tab 保留独立页面，包含 popup 接入。
- 该路径未见统一的 session/tab 数量上限或空闲回收器；Preview Runtime Pool 的存在不代表 Browser 页面也受其上限约束。
- 因而不能声称每个聊天新建一个浏览器进程，但已打开页面可能跨任务积累。

改进：Browser 独立资源预算，限制 session/tab/popup；活动任务 pin，空闲页面 suspend/close 并保留可恢复元数据；展示真实 cold/starting/ready/degraded 状态，而非仅凭 Worker 在线宣称浏览器就绪。

验收：连续 20 个研究任务页面数量有界；关闭、崩溃、恢复不把旧 tab/ref 绑定到新任务；不关闭仍被有效工作流使用的资源。

### F09 / P2 / B+C：语音、Realtime 与 GPU 资源尚缺共同仲裁

位置：`desktop/src-tauri/src/voice_worker.rs:690`；`desktop/src/voice/VoiceController.tsx`；`desktop/src/realtime/RealtimeCompanion.tsx`；`voice-worker/src/fairy_voice_worker/server.py`。

- 播报消费者归零后，生命周期回到 ready；该释放路径没有空闲卸载策略，模型可以继续驻留。
- Realtime 与普通聊天播报各自组织控制，未看到统一音频焦点所有者；并不意味着现有队列没有上限，Voice 队列已有约束。
- 本地 Realtime、TTS 模型和桌宠渲染可能同时使用 GPU；硬件资格检查不等于跨功能共享显存预算。
- Ambient 已有 Realtime 抑制，不应把已完成联动重复列为缺失。

改进：统一“谁正在录音/播报”的音频焦点、可中断优先级与会话作用域；增加驻留预算、可配置空闲卸载和启动前资源确认。正常启动继续禁止预热模型。

验收：真实硬件上并行聊天播报、Realtime、Browser 和桌宠，测量 CPU/GPU/VRAM、音频中断和恢复。当前未实测 OOM 或音频重叠，不能把风险当成本轮复现故障。

### F10 / P2 / B+C：Native 渲染与远程查看缺少明确能力契约

位置：`desktop/src-tauri/src/presence_native_gpu/windows_backend.rs:158`、`:253`、`:898`、`:4056`。

- DDA 自排除与一般窗口录屏 affinity 是不同设置；native 初始化为 render/input HWND 开启 DDA exclusion。
- 当前生产 stop 路径未见对应撤回保留 WebView HWND exclusion 的调用；形态热切换后的远程可见性需要专门核验。
- 名为 `native_surface_remains_visible_to_recording_and_remote_desktop` 的测试实际上检查源码字符串，不是在远程桌面里查看窗口。
- 因此不能用该测试证明“所有录屏/远程软件可见且液态玻璃效果完整”。本轮没有重新测量远程渲染效果或 GPU 帧成本。

改进：声明本机完整、远程兼容、降级/不可用的能力状态；把 exclusion 变为可恢复的生命周期资源；帧率/采样按可见性与交互需求分级。不得仅为可录制取消自排除并放任递归采样。

验收：真实 WebView2、DDA AccessLost、休眠、跨屏、远程连接，以及 native/SVG 连续热切换；分别检查画面、命中、窗口属性和 GPU 资源释放。

### F11 / P2 / A+B：回归门禁和文档仍不能证明整机体验稳定

位置：`desktop/src/presence/DualSurface.test.tsx:362`；`desktop/src/presence/render/PresenceRenderApp.tsx:134`、`:450`；`core/src/fairy_core/evals/agent_workflow.py`；`docs/architecture.md`。

- 完整 Vitest 有一项失败：测试在异步 render settings 尚未就绪时立即查找 renderer。确认的是测试/启动契约不匹配，不是已证明原生桌宠永久消失。
- 默认 Workflow eval 主要调度确定性 pytest 场景，并明确区分 scripted 与 live provider；其通过率不是实际模型工具选择成功率。
- 架构文档仍有独立 Settings、旧渲染描述及旧 engine version 等滞后内容。
- 这使“功能实现了/测试通过了/用户可用了”容易被混为一谈。

改进：测试区分实现单元、接口契约、用户旅程、原生资源、真实模型；ready 必须附带能力验证层级。文档与切换门禁随实现一起更新，禁止用源码包含断言代替原生行为验收。

## 3. 本轮验证记录

| 检查 | 结果 | 能证明什么 |
| --- | --- | --- |
| TypeScript `npx tsc --noEmit` | 通过 | 当前 TS 类型检查，不是原生启动 |
| Desktop 完整 Vitest `npx vitest run --maxWorkers=2` | 105 文件：104 通过、1 失败；587 测试：586 通过、1 失败 | 现有桌面门禁未全绿 |
| Core `tests/workflow tests/assistant tests/browser tests/media` | 182 通过 | 对应确定性回归；包含 Schedule 测试 |
| Core Project Knowledge/Knowledge Harness/Obsidian/Realtime/Memory Retention | 55 通过 | 对应隔离测试，不是实机语音或真实 Vault 全量扫描 |
| Capabilities 全套 | 118 通过 | 能力层自动化测试 |
| Cloud `-m 'not integration'` | 129 通过、1 跳过、29 排除 | 本地/模拟契约；跳过项缺真实 PostgreSQL DSN |
| 新增隔离复现 | 3 项预期正确性断言均失败 | 录音跨聊天、隐藏 Browser active、导航旧 revision 三条问题可复现 |
| Kernel 空闲 SQL 探针 | 3 秒 112 SELECT | 空闲查询频率，不是进程 CPU/GPU 实测 |

Core 使用各自 `.venv\Scripts\python.exe -m pytest ... -q -p no:cacheprovider`，未安装新依赖。

隔离探针保留在本机 ignored 临时目录，不属于现有正式测试集；修复时应迁入对应正式测试并验证由红转绿：

- `desktop/.tmp/project-audit/recording-scope.test.tsx`
- `desktop/.tmp/project-audit/audit.config.ts`
- `.tmp/project_audit_idle.py`

复现命令（分别在 desktop 和 core 中执行）：

```powershell
npx vitest run --config .tmp/project-audit/audit.config.ts .tmp/project-audit/recording-scope.test.tsx --maxWorkers=1
```

```powershell
.\.venv\Scripts\python.exe ..\.tmp\project_audit_idle.py
```

磁盘为本轮目录文件逻辑大小统计，不代表去重后的物理占用：

- `desktop/src-tauri/target`：约 24.35 GiB，主要是可重建 Rust 输出。
- `desktop/src-tauri/runtime`：约 8.48 GiB，包含运行时/模型，不能全部当垃圾。
- `desktop/native`：约 1.50 GiB；`desktop/node_modules`：约 0.37 GiB。
- 没有执行清理。开发工作应增加构建缓存预算、定期盘点与明确保留规则，而不是每次磁盘红了再全删。

结束时只读查询未发现命令行匹配本项目/pytest/vitest/browser-worker 的 Python、Node、Cargo 或 Fairy 进程；没有停止用户无关进程。

## 4. 建议实施顺序

以下是根据审查结果提出的路线，不代表已经获得实施、提交或上传授权。

### 第一阶段：先修明确错误，建立可信基线

1. F01：录音和迟到回调的 Scope/generation 隔离。
2. F04：Browser revision 权威统一。
3. F03：隐藏面板可见性传播与快照暂停。
4. F11：修正异步 renderer 启动测试，并覆盖失败/回退，保留用户 DSH 改动。

每项独立可回滚提交；三个隔离复现迁入正式测试。不要先大改 UI，也不要以删除功能绕过失败。

### 第二阶段：响应速度和资源止损

1. F02：为 Core 慢请求建立异步执行边界和不会被阻塞的控制路径。
2. F06/F07：空闲退避、到期唤醒、批量查询和有界历史读取。
3. F08：Browser 页面/Tab 数量与空闲生命周期约束。
4. 建立可关闭、无敏感内容的诊断计数：RPC 排队/执行耗时、查询次数、活跃 worker/page、内存与 GPU 驻留。

先量化现状再设预算；诊断不得记录 API key、完整私密消息或原始音视频。

### 第三阶段：使“工作流”和跨模块联动名实相符

1. F05：将真实工具调用迁入 Kernel 节点，统一并发和资源冲突；保持执行引擎版本隔离和已有恢复语义。
2. F09：共享音频焦点和 GPU 资源申请/释放契约，不将 Realtime 连续流塞进离散 Workflow。
3. F10：Native/SVG/远程兼容能力与 exclusion 生命周期闭环。
4. 前端投影同一事实来源：任务执行状态、资源状态、可见状态分开；暂停不能只是隐藏 UI，隐藏也不能等同于取消任务。

### 第四阶段：用用户完整旅程决定能否发布

至少验收下列组合，而不仅是分别点开每个功能：

- 文字提问 → 搜索/读取 → 引用回答 → Preview → 隐藏 Inspector → 恢复。
- A 录音 → 切到 B → 权限/转写迟到 → 返回 A；整个过程不串聊天。
- Browser 研究 → 动态页面变化 → 切 Tab → 继续导航 → Worker 断连恢复。
- 长任务 → 审批 → 更新要求 → 暂停/恢复 → 退出重启 → 唯一最终回复。
- Schedule 到期 → 同聊天已有任务 → 合并等待 → 执行 → 通知跳转。
- Workspace 文件变更 → Knowledge 索引/Snapshot → 下一轮回答引用更新后的证据。
- 普通语音回复 → Realtime → 桌宠交互 → 关闭所有消费者 → 检查释放。
- 本机/远程、窄窗口、Settings 往返、后台托盘、Native/SVG 切换。

每个旅程记录成功/失败、失败原因、p50/p95 延迟、RPC/SQL 数、CPU/内存/GPU/VRAM、残留资源、恢复结果。真实 Provider 单列工具选择和证据覆盖，禁止与脚本成功率混报。

建议作为下一轮冻结起点的验收目标（尚未测得或承诺达成）：

- 跨聊天错写、重复副作用、遗漏审批、遗留音轨：零。
- 不可见 Browser 可视快照：零；有效后台任务仍能工作。
- 空队列稳定期 Kernel SQL 较本轮基线下降至少 90%，同时验证调度延迟与 lease。
- 注入单个慢 RPC 时轻量控制请求不随它等待；先定义并测量 1 秒内确认目标。
- 连续 20 次聊天/Browser/形态切换后，页面、线程和驻留资源不随次数无界增长。
- 自动化确定性门禁全绿；真实硬件/Provider 不可用时明确列为未验收，不用静态检查替代。

## 5. 总体判断

保留现有治理与领域能力，停止横向扩功能，先偿还执行链和资源生命周期的工程欠账。优先解决 F01–F04 这类直接损害日常使用的问题，再收敛 F05 的调度事实与 F06–F10 的资源策略，最后以跨模块旅程和原生测量形成真正的可用性门禁。

当前不能称为“全项目稳定可用”；也没有证据支持推倒重做。需要的是有顺序的整合与稳定化，而不是又一次功能堆叠。

## 6. 补充审查：多前端解耦、事件推送、Core 共享与用户意图

补充日期：2026-09-08。保留上述第一轮记录，不覆盖原始结论与测试结果。本节仅补充设计方向、代码证据和验收要求，未实施业务修改。

### 6.1 概念与 Fairy 当前实现的对应关系

| 用户提出的方向 | 当前实现 | 应补齐的内容 |
| --- | --- | --- |
| TypeScript 管界面，Python 管会话、工具、模型和命令 | 已有 React → Tauri/Rust → stdio JSON-RPC → Python Core；会话、工具治理、模型调用主要在 Python | 协议级解耦不等于执行无阻塞；斜线命令业务分发尚未完全集中 |
| CLI/TUI 双前端 | 未发现完整的交互式聊天 CLI/TUI 产品；存在 Core 进程入口、开发脚本和 eval CLI | 不为修复桌面应用而额外制造 CLI/TUI；先让现有前端共享稳定应用契约 |
| Webhook 主动推送 | Local 有 Ledger 事件读取和前端恢复；Cloud 有 SSE；未在本次检查的 Core/Cloud 路由中发现通用 Webhook 接入/投递服务 | 本地需要事件分发与推送，不是 HTTP Webhook；外部系统集成再单独设计 |
| 主窗口与桌宠同时监听 Core | 已共享一个宿主 Core；桌宠通过 PresenceBridge 请求和投影间接使用它，不是直接任意调用 Core | 消除不必要的主窗口 UI 生命周期依赖，保持桌宠能力最小化 |
| 提示词转义，避免误执行 | 已有输入分段、JSON envelope、结构化 classifier、意图修订和澄清 | 补齐逐动作授权范围、否定/指代/引用的评测，以及意图与工具执行的一致性 |

CLI 是命令行接口，TUI 是终端内交互界面；它们不是天然的“前后端两层”。Fairy 已有主窗口、桌宠输入/渲染等多表面，当前应该借鉴的是“同一个应用内核服务多个展示端”，而不是再增加两种 UI。

JSON-RPC 规定请求、响应、ID 和通知格式，不限定必须使用 TCP/HTTP，也不自动提供线程调度、取消、重连或权限治理。换一个传输协议名称不能修复 F02。[JSON-RPC 官方规范](https://www.jsonrpc.org/specification)

### 6.2 解耦已有基础，但职责需要进一步收敛

代码证据：

- `desktop/src/core/tauriTransport.ts`、`desktop/src-tauri/crates/core-bridge/src/lib.rs`、`core/src/fairy_core/transports/jsonrpc.py` 和 `stdio.py` 已形成桥接链。
- `core/src/fairy_core/commanding/slash_commands.py` 定义命令元数据；`desktop/src/chat/slashCommands.ts` 解析文本；`ChatWorkspace.tsx:243` 后的分支实际决定 `/new`、`/clear`、`/stop`、`/permission` 等行为。
- `core/pyproject.toml` 中显式 CLI entry point 为 `fairy-agent-eval`，不能据此宣称已有完整聊天 TUI。

建议职责：

- **TypeScript**：屏幕布局、键盘/IME、选中项、草稿、滚动、动画、显示与输入反馈。
- **Python Core**：会话/Turn、业务命令语义、意图解释、模型/工具调用、Scope/Approval、Workflow、持久化事实。
- **Rust 宿主**：进程监管、OS 窗口/托盘、凭据、原生渲染与设备权限、IPC 身份和限流。不能把这些 Windows 能力为了“纯 Python”而强行迁走。

命令收敛分两类：`/stop` 等领域命令通过 Core 权威 dispatcher 校验和执行；`/project` 等纯导航可以仍由前端处理，但采用统一、类型化的 UI action 描述。设备权限档位仍由现有安全宿主边界负责，不因引入 command dispatcher 绕过确认。前端可以做补全和即时格式校验，最终业务语义不能各实现一套。

验收：通过界面和无界面的协议测试调用相同命令，产生一致的业务结果和公开错误；不存在绕开 UI 就丢失的业务校验。未来如确有 CLI/TUI 需求，只接入同一契约，不复制 Assistant、Classifier 或 Scheduler。

### 6.3 新发现 F12 / P2 / B：本地事件“订阅”是 25ms 空闲轮询

位置：`desktop/src/core/client.ts:123`、`:1026`、`:1030`；`desktop/src/app/workspaceModel.ts:415`；`core/src/fairy_core/application/service_endpoints.py:408`。

- 客户端优先使用 transport 的 `subscribeEvents`，否则进入 `pollEvents`。
- Tauri transport 当前没有提供该流式实现；Workspace 订阅未覆盖轮询间隔，因此使用 `DEFAULT_EVENT_POLL_MS = 25`。
- Core `events.subscribe` 立即返回一次 `_event_page`，不是服务器等待事件再唤醒的长订阅。
- 空批次后等待 25ms，再发一次 RPC。在调用足够快时，理论上可接近每秒 40 次请求；这是代码推导，不是本轮实测整机请求频率，也不能直接与 F06 的隔离 SQL 数相加当作整机数据。

现有 `eventStream.ts` 已做 ledger/source/cursor 校验、回放、去重和重连，这些应该保留。Cloud 的 `event_routes.py` 向客户端提供 SSE，但服务端内部仍周期读取事件；不能称为全链路已经事件驱动。

**修复并入 F02/F06：**

1. 短期按空闲/活动状态退避，处理取消、断连和边界延迟，停止 25ms 无条件空轮询。
2. 中期由 Core 事务提交后的通知唤醒事件读取，Rust 常驻 broker 管理一次受控读取并按客户端分发。
3. 持久 Ledger 是事实来源，推送是及时送达机制；提交后推送丢失可通过 cursor 补齐。订阅起点采用快照水位与后续事件衔接，不能在“先读快照后注册监听”的空隙丢事件。
4. 增加每订阅者队列上限、慢消费者隔离、scope 过滤、重连退避；高频视觉/音量状态只保留最新值，不和必须可靠投递的任务终态/审批事件混用策略。
5. 长订阅不得占住目前唯一串行 RPC 的锁；不能只把服务端方法改成阻塞等待，否则 F02 会更严重。

验收：两个表面并行订阅时不重复执行命令、不相互消费掉事件；慢桌宠不拖住主窗口；断线/重启/窗口重载后重放不漏终态、不串会话；空闲请求数明显下降。

### 6.4 Webhook 不应拿来连接本机窗口

Webhook 的典型用途是外部系统发生事件后向预先配置的 HTTP 接收地址发请求；SSE 则是客户端建立连接后持续接收服务器事件。两者都能减少某些轮询，但适用场景不同。[GitHub Webhook 文档](https://docs.github.com/en/webhooks/about-webhooks)、[MDN SSE 文档](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)

Fairy 当前修复应优先采用本地 IPC/宿主事件分发；不需要给主窗口、桌宠各启动 HTTP server，也不需要为了它们开放公网入口。

未来如果接 GitHub 构建完成、外部任务完成等事件，再增加独立的入站/出站 Webhook Adapter。其签名验证、重放防护、事件 ID 去重、持久 inbox/outbox、有限重试、失败可见性和目标网络限制应独立设计。入站事件必须带来源标签，经既有 Scope/Policy/Approval 触发流程，不得直接转成 shell 命令或高优先级提示词。Local 离线或关闭时的接收/补偿也需要产品约定。

本轮只预留边界，不实现 Webhook 产品功能，不扩大原稳定化范围。

### 6.5 多窗口共享 Core：需要受控分发，不是“让两个窗口绑定同一个端口”

服务器可以监听一个端点，多个客户端分别连接；客户端不需要自己占用同一个监听端口。对 Fairy 当前桌面结构，更直接的是让已有 Rust 宿主管理共享 Core 与客户端分发，不必新增 TCP 端口。

现状证据：

- `desktop/src-tauri/src/lib.rs:307` 的通用 Core RPC 只允许 main 窗口，其他受控窗口有各自允许的方法。
- `desktop/src/presence/transport/presenceChannel.ts` 使用 `BroadcastChannel("fairy.presence.v2")` 传递请求/投影。
- `PresenceBridge.tsx:46` 在主界面侧生成投影并处理 `chat.send/chat.cancel` 等请求。
- 隐藏主窗口并保留 WebView 能维持这条链；但它不等于独立于主窗口 WebView 重载、故障或卸载的宿主服务。此处是生命周期耦合，尚未新增原生故障复现。

建议目标关系：

```text
主窗口 ── 业务请求 / 会话投影 ──┐
桌宠输入 ── 最小受控意图 ───────┼── Rust 常驻 broker ── Python Core
桌宠渲染 ── 只读展示投影 ───────┘                       │
                 各自有权限与队列                Ledger / Workflow
```

- broker 是传输、授权入口和生命周期管理，不是第二套会话库或 Workflow Scheduler。
- 桌宠不直接持有完整项目状态、不调用模型、不处理审批、不订阅整个内部 Ledger；渲染表面保持只读。
- `client_id` 与能力应由宿主/连接身份绑定，不能信任消息自己声称来自 main。
- 桌宠发送需要由 Core/宿主解析到明确 Conversation/Task，不能仅依赖另一个前端“此刻选中了谁”。
- Core 的 stdout 只有一个受控 reader；不能让多个窗口各自读同一条流抢响应。由 reader 按 request ID 分发，并为异步事件使用明确 envelope。
- 有独立进程 CLI 的实际需求时再评估具 ACL 的 named pipe 或认证的 loopback；禁止默认绑定 `0.0.0.0`。更换传输不改变 Scope 与幂等语义。

验收：主窗口隐藏和重载、桌宠开关、Core 重启时，已有 Run 不因 UI 消失而丢失；两个窗口发请求响应不串线；同一提交去重；权限拒绝仍可追踪。

### 6.6 “提示词转义”应拆成数据边界、意图解释、执行约束

这里的目标是理解用户真正要做什么，不是新建有害内容过滤器，也不是给所有字符加反斜杠。JSON 转义能保护序列化结构，却不能保证模型语义上绝不误读引用或代码；Classifier 也是可犯错的解释器，不是新增授权来源。

**已经实现：**

- `assistant/interpretation.py:235`、`:261`：原文哈希、TEXT/QUOTE/CODE 分段、JSON envelope、长度限制和长输入分批；声明内容为待分析的数据。
- 同文件的 `ClassifierInterpretationPayload`：action、目标、约束、交付物、依赖、假设、缺失信息、置信度、澄清状态；不可变 interpretation revision。
- `request_intent_policy.py:18`：修改/运行/管理缺少 target 时要求澄清；创建/生成缺少交付物时要求澄清；假设公开化。
- `intent_guard.py`：引用/代码不触发词法专用路由，结构化意图约束 Browser/Media 路由。
- `routing_runtime.py` 与 `routing_evidence_runtime.py`：Auto/Manual 使用解释流程；classifier 不可调用工具；`WAITING_FOR_INPUT` 和回应澄清链已存在。
- `context.py:551`：把 interpretation 作为有界规划数据交给执行模型，明确不能扩大 Scope。

**还需补齐或证明的边界：**

1. **意图与权限不是同一件事。** `commanding/policy.py:23` 校验 profile、工具开关、sandbox 和 approval，没有逐 Turn 的解释 action/targets 参数；`context.py` 的工具投影也没有全面按 review/explain 等 action 缩减。现有 Scope 与审批依然有效，但尚不能据此证明“本来只请求分析的用户绝不会被模型带去修改”。这是代码确认的覆盖缺口，尚未做真实模型误写复现。
2. **降级解释需要可见来源。** `fallback_interpretation` 可给出 medium/ready；不能把它当成已由用户确认的高确定性目标。需盘点无 model selection、兼容旧 classifier 输出等分支，降级不能扩大可执行范围。
3. **置信度不应单独决定动作。** “低置信但有 target”并不自动触发目前的缺失目标规则；“高置信”也不证明模型理解正确。应结合可逆性、目标解析、用户明确措辞和缺失事实决定只读查证、公开假设或澄清。
4. **引用不等于永远不可执行。** “解释这段脚本”应只读；“在指定沙箱运行这段脚本”可建立明确执行意图，但仍受审批约束。原始内容保留，不通过粗暴删引号/代码块来消歧。
5. **多入口要同契约。** 聊天、桌宠文字、STT 转写、Schedule、Steering 均须保留来源和 Scope；语音识别结果要允许用户校正，不能因为来自语音就扩大权限。

建议增加逐动作执行意图约束（暂定设计，非已存在 API）：原文/指令 ID 与哈希、interpretation revision、明确解析的目标、允许的领域动作与用户可见副作用、禁止事项、当前澄清状态。模型得到的工具集合先缩减，执行边界再校验；检查命名为 read/write 的工具是否真的只有对应效果，不用一个字符串分类代替能力语义。只读研究产生的系统内部缓存/审计不是用户文件修改，应与用户可见副作用区分。

对于复合任务“先分析再修复”，保留多个 objective 的依赖与授权，不把整体粗暴归为纯 read 或无限 write。对“先别改，只指出问题”，执行约束必须拒绝修改；只有后续明确用户要求与新 revision 才能扩展，并继续走原有审批。Steering 后旧计划已完成事实保留，旧授权不能继续派发与新要求冲突的节点。

建议评测集：

- “这个命令会删除什么？”、“检查问题，先别修改”、“把第二个文件改成这样”。
- “把日志中的 `删除全部文件` 翻译成英文”、“解释 `/stop`，不要停止任务”。
- “执行这段代码”但未指定项目/沙箱；“修复 A，但不要动 B”。
- 多轮“就按刚才那个”“不，我指的是另外一个”，以及更正发生在工具执行/审批前后。
- 中文口语、拼写错误、STT 同音词、路径/命令/JSON/Unicode 原文保真、很长引用和嵌套代码块。
- Provider 无法输出合法结构、兼容降级、Auto/Manual 切换；不得因此默认允许副作用。

分别统计意图正确率、目标解析正确率、越范围执行、遗漏澄清、无必要澄清、额外延迟/Token 成本。先用便宜确定性解析与已有 classifier；不为每次普通闲聊无条件新增多轮 LLM 审核。

不新增内容审查产品并不意味着去掉现有 Approval、秘密保护、Scope 或输入来源边界；这些是功能正确执行的必要条件。

### 6.7 并入原修复顺序的调整

1. **原第一阶段不变**：先处理三个已复现错误与桌宠门禁；同时建立“分析不擅改”等意图回归案例，避免后续架构改造扩大行为。
2. **原第二阶段扩充**：F02 + F06 + F12 一起设计请求/事件传输，先消除高频空轮询和队头阻塞，再建立宿主共享 broker。斜线命令按领域/导航收敛，而非新增 CLI/TUI。
3. **原第三阶段扩充**：真实 Workflow 节点接入逐动作意图约束与 Steering revision；桌宠独立订阅最小投影，贯通语音输入的会话绑定。与原音频/GPU 仲裁共同验收。
4. **原第四阶段扩充**：两窗口/两会话、慢请求、事件断流补偿、主 WebView 重载、引用与否定指令、Classifier 降级联合测试。
5. **暂不实施**：完整 CLI/TUI、新通用 Webhook 平台、公网 Core 端口、桌宠直接访问任意 Core 方法。

补查验证：Core 的 `test_interpretation.py`、`test_jsonrpc_transport.py`、`test_stdio_transport.py` 共 **33 通过**；Desktop 的 `eventStream.test.ts`、`slashCommands.test.ts`、`PresenceBridge.test.tsx` 共 **16 通过**。这是已有机制的定向回归，不证明本节建议已经实现；没有重跑上节完整套件，也没有新增原生/真实模型验收。
