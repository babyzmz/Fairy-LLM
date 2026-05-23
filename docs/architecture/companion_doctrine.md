# Companion Doctrine

不可破戒的设计原则。这份文档存在的唯一目的，是在未来的某个改动里
*将要* 破戒之前——写代码的人（包括 future self、包括任何 AI assistant）
能先把这份文档读一遍。

> 这些规则不是建议，是 invariant。任何 PR 要破其中一条，必须先改这份
> 文档并说明为什么破。绕过文档直接改代码 = 回滚。

---

## Rule I — Companion 永不触发 LLM

### 规则
Companion 路径上的任何模块（observer / scene_state / bubble / advisor /
quip 选择）**不允许**调用任何形式的语言模型推理：

- ✗ `llm.execute_task(...)`
- ✗ `provider_router.complete(...)`
- ✗ 任何 HTTP 到 model server / Doubao / OpenAI 的调用
- ✗ 任何"让模型润色一下 quip"的"小调用"

### 为什么这条最重要

破戒会同时引爆三种污染，且不可逆：

**A. Latency 污染** —— 当前 quip = O(1) 字典查找。一旦走 LLM:
```
quip = network round-trip + inference
     ≈ 200~2000ms
```
Companion 的本质是 **ambient presence**。延迟 ≥200ms 的反馈
不是"陪伴"，是"等 AI 想一句话"。视觉上是穿帮的。游戏行业几十年验证过
这件事：**存在感不需要智能**。

**B. Token 污染** —— 一旦给 companion 接 LLM，*总会有人*想：
```
"让 companion 更懂上下文" → 喂 chat history
"让 companion 更人格化" → 喂 memory
"让 companion 知道游戏进度" → 喂 screen state
"让 companion 风格统一" → 喂 system prompt
```
每次都看起来无害。然后某天 Doubao 账单跳了 8 倍，主链路 latency 跟着
跌，没人能说清是哪一步。

**C. 人格侵蚀 executor** —— 这是终局问题。如果 companion 也走 LLM：
```
tool layer      ←┐
persona layer   ←┤  迟早会混
emotion layer   ←┘
```
最后 LLM 在思考还是在表演，区分不出来。Tool calling 开始有情绪，
reasoning 开始有节奏，token usage 开始有"心情"。

### 当前结构（必须保持）

```
assistant = cognition           ← LLM 在这里
companion = illusion of presence ← LLM 永远不在这里
```

### 即将出现的诱惑及正确处理

| 诱惑 | 正确做法 |
|---|---|
| "quip 太机械" | 扩大 quip 池 + 加更多 repetition_overlay 变体 |
| "想根据上下文生成" | 加更细分的 scene + keyword 规则 |
| "想要她记得用户名" | 用 persistent_memory 模板字符串插值（仍是 O(1)） |
| "想要她评论 boss 战术" | 在 assistant 的回答里说，不在 bubble 里说 |
| "想要她说话更自然" | 这是产品问题不是工程问题，去找文案，不要去找模型 |

---

## Rule II — System Prompt 永不动态拼接

### 规则
`BUBBLE_ADDENDUM` 是**静态字符串常量**，永远是 43 行。不允许：

- ✗ 把 `current_scene` 拼进 prompt
- ✗ 把 `recent_quips` 拼进 prompt
- ✗ 把 `companion_memory` 拼进 prompt
- ✗ 把 `current_game` 拼进 prompt
- ✗ 任何 `f"...{companion_state}..."` 形式的 prompt 构造

### 为什么

动态 prompt 拼接是 AI 系统后期最常见的 **屎山源点**：

- **不可审计** —— 出 bug 时不知道当时 prompt 长什么样
- **不可预测** —— 同一 query 不同 session 行为不同
- **prompt 漂移** —— 拼接逻辑改一行，半年后没人记得为什么
- **context contamination** —— companion 状态泄漏到 assistant 推理
- **hidden coupling** —— 表面是两个模块，实际通过 prompt 偷偷耦合
- **latent behavior mutation** —— 看不出代码改了，但行为变了

### 当前实现

[app/prompts/fairy_runtime_prompts.py](app/prompts/fairy_runtime_prompts.py)
里 `build_core_system_prompt()` 通过 `\n\n.join((...))` 把 BUBBLE_ADDENDUM
作为一个**整体常量**拼进 system prompt。companion 模块的任何状态变化
**不影响**这段字符串。

### 即将出现的诱惑及正确处理

| 诱惑 | 正确做法 |
|---|---|
| "让 assistant 知道 fairy 现在在 boss 状态" | 不需要知道。Bubble 是 companion 自己的事 |
| "让 assistant 用更连贯的语气" | persona layer 已经有 tone_preference，改那里 |
| "让 assistant 提到用户最近常玩什么" | 走 memory_retriever（assistant 的长期记忆），不走 companion |

---

## Rule III — Scene 是 Presence，不是 Behavior

### 规则
Scene enum 只能表达**用户当前的临场状态**，不能表达**用户当前在干什么**。

### 好 Scene（presence-oriented）

```
IDLE          ← 桌面闲置
GAME_ACTIVE   ← 在游戏里
COMBAT        ← 紧张
BOSS          ← 高度紧张
VICTORY       ← 胜利情绪
DEFEAT        ← 失败情绪
AFK           ← 不在场
HOMECOMING    ← 刚回来
```

每一个都是 *氛围维度*。Avatar 视觉、quip 池子、声音节奏都能从这一个
维度推出来。

### 坏 Scene（behavior-oriented）

```
✗ READING_GUIDE         ← 这是行为
✗ BROWSING_WEB          ← 这是行为
✗ OPENED_INVENTORY      ← 这是行为
✗ CHATTING_WITH_FRIEND  ← 这是行为
✗ WATCHING_VIDEO        ← 这是行为
✗ EDITING_DOCUMENT      ← 这是行为
```

为什么不行：

1. **正交性塌陷** —— 行为可以组合（边看视频边游戏边聊天），状态机
   会从 O(N) 爆成 O(N²) 转移
2. **没有视觉映射** —— "browse_web" 到底是 focused 还是 relaxed？
   没法决定 avatar 怎么动
3. **维护成本爆炸** —— 每加一个用户行为就要加 scene + quip 池 +
   转移规则 + 测试
4. **越界 illusion engineering** —— 一旦开始追"行为感知"，迟早要接
   accessibility tree / OCR / window watcher 各种，回到 Rule I 的
   token 污染陷阱

### 判定标准

加新 Scene 前问三个问题：

1. 它能用 1 个形容词描述吗？（focused / tense / relaxed ✓ ；looking_at ✗）
2. avatar 视觉能不能从它直接推出来？（不需要再判断"哪种 browsing"）
3. quip 池能不能写出 5 句通用的？（不需要"在浏览器里"特例）

三个都 yes 才能加。

---

## Rule IV — Companion Memory 是"近期体验感"，不是"人生档案"

### 规则
[short_memory.py](../../app/companion/short_memory.py) 是 50 条环形缓冲、
30 分钟窗口。[persistent_memory.py](../../app/companion/persistent_memory.py)
只持久化 5 个计数器，不持久化任何事件序列。

### 不允许

- ✗ 把 quip 历史完整持久化
- ✗ 把 scene transition 历史完整持久化
- ✗ "学习用户的情绪偏好"
- ✗ "适应用户语气"
- ✗ 任何想把 companion 数据塞进 [app/memory/](../../app/memory) 的尝试

### 为什么

游戏行业的 companion（Persona navigator / Hades narrator / VTuber
reactive systems / Left 4 Dead AI Director）全是 **有限状态 + illusion**。
没有一个真的"理解"用户。illusion engineering 的关键就是：

```
有限范围内的 contextual response
+
用户自己脑补连续性
=
"她记得我"
```

一旦 companion memory 变成"档案"，错觉会立刻穿帮：用户会期待真的连贯性，
然后发现是假的，整个 presence 崩塌。

### 当前实现的边界

- ShortMemory ring buffer 满了就丢，**不归并**
- PersistentMemory 只存 6 个 scalar（games_played / 连胜 / 连败 /
  last_session / last_known_game / total_quips）
- **物理隔离**：companion 数据在 `data/companion_memory.json`，assistant
  长期记忆在 `data/fairy_memory.db`，两者不共享 schema 不共享访问层

---

## 当前不变量在代码里的表现

| Rule | 代码层 | 验证 |
|---|---|---|
| I — No LLM | `app/companion/` 任何 .py grep 不到 `LLMClient` / `provider_router` / `execute_task` | `grep -r "execute_task\|LLMClient\|provider_router" app/companion/ → 0` |
| II — Static prompt | `BUBBLE_ADDENDUM` 是模块级常量 | `app/companion/prompt_addendum.py` 无函数定义 |
| III — Scene as presence | `Scene` enum 限定 9 个值 | [scene.py](../../app/companion/scene.py) |
| IV — Memory bounds | ShortMemory capacity 硬编 50 | [short_memory.py](../../app/companion/short_memory.py:25) |

---

## Doctrine 校验脚本（建议加到 CI）

```bash
# 不允许 LLM 调用进入 companion
grep -r "execute_task\|LLMClient\|provider_router\|provider_complete" app/companion/ && exit 1

# 不允许 prompt addendum 变成函数
grep -E "^def |^class " app/companion/prompt_addendum.py && exit 1

# 不允许 companion 进 app/memory
grep -r "from app.memory" app/companion/ && exit 1
```

把这三行写进 `tools/check_companion_doctrine.sh`（暂未实现，留待 CI 接入时一并做）。

---

## 修改这份文档的流程

1. 写改动说明（为什么破戒）
2. 影响范围分析（破戒后哪些地方会变质）
3. 找到 doctrine 之外的替代方案再回来
4. 如果确实没替代方案，PR 标题加 `[doctrine-break]` 前缀

绝大多数情况，第 3 步会让你想出别的办法。
