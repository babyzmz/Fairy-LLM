# Fairy Desktop

桌宠陪伴型桌面助手：右下角浮动 Fairy + 实时游戏屏幕识别 + 联网查询 + 攻略助手。

## 运行架构

```
Tauri (React/Vite) → FastAPI (app.api.main:app) → FairyRuntimeV2 → 结构化响应卡片
                                       ↓
                          CompanionObserver / PassiveScreenWatcher
                                       ↓
                            SSE quip-stream → SpeechBubble
```

- **桌面壳**：`fairy-desktop/`（Tauri 2，两个窗口：主聊天 + `fairy-pet` 浮窗）
- **后端**：`app/api/main.py` (FastAPI / uvicorn)
- **LLM**：本地 Qwen via `local_server` 模式，或云端 Doubao via `app/providers/`
- **桌宠陪伴**：`app/companion/`（quip 观察者 + 气泡状态机 + 被动屏幕监控）
- **斜杠命令**：`app/commands/`（`/截图` `/攻略` `/喂` `/静音`）

## 启动方式

```powershell
powershell -ExecutionPolicy Bypass -File tools/start_fairy_desktop.ps1
```

脚本会拉起 headless Chrome（CDP 9778）+ uvicorn (8000) + Tauri (1420)，三者绑到同一个 job object，关闭终端就一并停。

## 首次安装

```bash
pip install -r requirements.txt
cd fairy-desktop && npm install
```

`faster-whisper` 是新加的依赖（流式 STT），缺失时会优雅降级到 DummyStreamSTT。

## 目录结构

- `app/` — Python 后端
  - `api/` — FastAPI 路由
  - `runtime/` — FairyRuntimeV2 主链路
  - `companion/` — 桌宠 quip / 气泡 / 游戏窗口监控
  - `commands/` — 斜杠命令注册
  - `capabilities/` — 屏幕/浏览器/文档/命令能力
  - `skills/bundles/` — web_research / screen_understanding / news_intelligence / document_editing / terminal_agent
  - `providers/` — Doubao + OpenAI 兼容客户端
  - `memory/` — 结构化 + 向量记忆 + 相关性/陈旧度评分
- `fairy-desktop/` — Tauri 桌面壳
- `config/game_window_whitelist.json` — 游戏窗口白名单（被动监控用）
- `docs/history/` — 历史迁移文档归档

## 遗留入口

`main.py` (Qt) 仅用于 migration debugging，不再是默认路径。架构现状见 [LEGACY_DECOMMISSIONING.md](LEGACY_DECOMMISSIONING.md)。
