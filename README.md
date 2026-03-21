## Windows 桌宠（Python + 本地大模型预留）

一个基于 Python 的 Windows 桌宠原型：

- **透明置顶小窗口**，圆形区域。
- **双环蓝色辉光动画** 作为初始形象。
- **本地大模型对话接口预留**（通过 HTTP / 本地进程均可实现）。
- **记忆库与个性化性格系统骨架**。
- **Live2D 接入预留**（以后可切换为 Live2D 形象）。

### 运行方式

```bash
pip install -r requirements.txt
python main.py
```

### 目录结构（计划）

- `main.py`：程序入口。
- `app/ui/desktop_pet.py`：桌宠 UI 和动画。
- `app/ai/llm_client.py`：本地大模型客户端接口（可对接任意本地大模型服务）。
- `app/ai/memory.py`：简单记忆管理（JSON 文件）与检索逻辑。
- `app/ai/personality.py`：个性化性格配置、系统提示词构造。
- `app/config.py`：基础配置（模型端口、窗口位置等）。

> 当前版本重点是 **UI + 架构**，方便后续逐步接入具体的大模型和 Live2D。







