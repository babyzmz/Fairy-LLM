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
| 1 | 录音 Scope；Browser revision；隐藏面板；桌宠启动 | 进行中 |
| 2 | 持久执行意图、目标、副作用约束、多入口一致性 | 未开始 |
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

## 恢复方法

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
