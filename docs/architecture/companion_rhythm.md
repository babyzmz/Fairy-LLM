# Companion Rhythm Architecture

桌宠节奏控制模块的设计与边界约束。本文件记录的是**为什么**这样划分，避免后续把 companion 越界塞进 assistant pipeline。

## 不变量（最重要）

```
assistant = executor       (FairyRuntimeV2 + tool loop)
companion = atmosphere     (observer + scene + bubble)
```

允许的耦合方向只有三条，全是单向：

1. `chat → notify_assistant_text(text)` — fire-and-forget，主链路完全不等
2. `screen watcher → observer.observe_game_*` — 单向事件流
3. `system prompt → COMPANION_BUBBLE_ADDENDUM` — 静态字符串常量，永不动态拼接

明确禁止：

- ✗ Companion 反向写入 assistant 的 system prompt
- ✗ Companion 触发任何 LLM 调用（quip 一律靠规则 + 预写池）
- ✗ `scene` 被 tool loop 或 reasoning 路径读取
- ✗ Companion 阻塞 chat 的任何一步（subscribers 走异步队列）
- ✗ Companion 与 `app/memory/*` 共用存储（assistant 长期记忆 ≠ companion 个性化记忆）

## 模块层级

```
                  ┌────────────────────────────────────────────┐
                  │       CompanionObserver                    │
                  │   (gating + dispatch only)                 │
                  └──┬──────────────────────┬──────────────────┘
                     │                      │
         ┌───────────▼────────┐     ┌───────▼──────────┐
         │ DensityGovernor    │     │ SceneStateMachine│
         │ (sliding window)   │     │ (transition rules)│
         └────────────────────┘     └───────┬──────────┘
                                            │ on transition
                                            ▼
                                  ┌──────────────────────┐
                                  │ RepetitionAdvisor    │
                                  │ ├── ShortMemory      │  (RAM ring, current session)
                                  │ └── PersistentMemory │  (data/companion_memory.json)
                                  └──────────────────────┘
```

## 各组件职责

### DensityGovernor
3 分钟滑动窗口。`soft_cap=5` 后 emission 概率开始线性衰减；`hard_cap=9` 完全静默。
独立于 cooldown：cooldown 控制最小间隔，governor 控制密度。

### SceneStateMachine
9 个场景：
- `IDLE` — 桌面空闲
- `GAME_WARMING` — 进游戏 60s 内（缓冲，避免一秒内连续 quip）
- `GAME_ACTIVE` — 游戏中
- `COMBAT` / `BOSS` — 战斗 / 头目战（由 assistant 文本关键词触发）
- `VICTORY_AFTERGLOW` / `DEFEAT_REGROUP` — 通关 / 失败 30s 余韵
- `AFK` — 10 分钟无事件
- `HOMECOMING` — 启动时检测到 ≥2h 间隔（"刚回来"）

转移触发：
- 游戏窗口 enter/exit（watcher）
- assistant 文本关键词（chat hook）
- 时间到期（tick，5s 一次，由 watcher 顺带调）

### ShortMemory + PersistentMemory（分层）

| 维度 | ShortMemory | PersistentMemory |
|---|---|---|
| 存储 | in-RAM 环形缓冲（50 条） | `data/companion_memory.json` |
| 时间尺度 | 30 分钟窗口 | 跨 session |
| 用途 | "刚才同一 boss 又死了" | "今天玩第 12 次原神" / "上次离开 4 小时" |
| 与 assistant 记忆隔离 | ✓ 物理隔离 | ✓ 物理隔离 |

`RepetitionAdvisor` 根据这两层联合判断要不要切到 `REPETITION_QUIPS` 池：
- `DEFEAT_REGROUP`: 短期重复 OR `consecutive_defeats ≥ 2`
- `VICTORY_AFTERGLOW`: `consecutive_victories ≥ 3`
- `BOSS`: 短期同一 boss 重复
- `GAME_WARMING`: `games_played ≥ 5` → "这游戏你最近玩得很勤"

## Quip 分层

```
scene_quips.py
├── BASE_QUIPS[Scene]              # 每场景的默认池
└── REPETITION_QUIPS[Scene]        # 命中重复时切换的副池
```

旧的 `quip_pool.py` 按 `QuipCategory` 分类的池子保留，作为 `force_emit` 的兜底（命令式触发场合）。新场景驱动路径走 `scene_quips`。

## UI 联动（前端）

```
SSE: /companion/quip-stream
  event: hello   → 初始快照 + scene
  event: quip    → 含 scene + repetition 字段
  event: scene   → 场景切换
  event: ping    → 心跳
```

前端 `useCompanionStream` 统一订阅，向 `PetSurface` 暴露 `{bubble, scene, petBurst, triggerPet}`。

`sceneToAvatarMode.ts` 把 scene 映射到 `FairyAvatarSignal`：
- `boss` → `alert` (urgency 0.95)
- `combat` → `thinking` (urgency 0.7)
- `defeat_regroup` → `uncertain`
- `victory_afterglow` → `focused` certainty 0.95
- `afk` → `standby` urgency 0.04
- 等等

优先级顺序（PetSurface presence 解析）：
1. chatError / systemError → `alert`
2. streamSignal (assistant 主动报告的状态) → 沿用
3. chat phase = thinking / replying → 沿用现有逻辑
4. **scene → 落地态** ← 新增层级，只在前三项都空时生效
5. systemState fallback

**Listening glow**：`phase === "thinking"` 时给 `pet-orb-button` 加蓝色 drop-shadow + className `pet-orb-button--listening`，不动核心动画参数。

## 启动 / 关闭顺序

```
initialize_runtime_service() {
   FairyRuntimeService()                          ← 主链路 OK
   start_companion()
     ├── persistent_memory.load()
     ├── observer.set_repetition_advisor(advisor)
     ├── scene.subscribe(advisor.record_scene_entry)
     ├── if homecoming_gap >= 2h: emit homecoming quip
     └── passive_screen_watcher.start()
}

shutdown_runtime_service() {
   stop_companion()
     ├── passive_screen_watcher.stop()
     └── persistent_memory.note_session_end()  ← 写盘
   FairyRuntimeService.shutdown()
}
```

## 加新场景的步骤

如果以后要加 `LOOTING` / `EXPLORATION` 等新场景：

1. `scene.py` 加 enum 值
2. `scene_quips.py` 给 `BASE_QUIPS` / `REPETITION_QUIPS` 配两套池
3. `scene_state_machine.py` 加转移规则（关键词 / 时长 / game event）
4. `sceneToAvatarMode.ts` 加 avatar mode 映射
5. 加测试到 `tests/test_scene_state_machine.py`

**不需要**改 observer 本体、不需要改 chat 路径、不需要改 prompt。这是分层设计的好处。

## 已知边界 / 取舍

- `scene_state_machine` 单例，全局唯一。多窗口场景下行为一致（这是想要的）。
- `homecoming` 检测只在启动时做一次。session 内回来不会再触发（避免和 AFK wake 重叠）。
- AFK 阈值 10 分钟硬编码。如果想做用户偏好，改 `scene.AFK_THRESHOLD_SECONDS` 或加 `companion_settings.json`。
- Persistent memory 不做迁移 schema。字段加是兼容的，删字段是不兼容的，删字段时手动迁移。
