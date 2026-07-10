from __future__ import annotations

import random
from enum import Enum


class QuipCategory(str, Enum):
    LOW_HP = "low_hp"
    BOSS = "boss"
    VICTORY = "victory"
    DEFEAT = "defeat"
    LOOT = "loot"
    QUEST = "quest"
    STRATEGY = "strategy"
    ERROR = "error"
    GENERAL = "general"
    GAME_ENTER = "game_enter"
    GAME_EXIT = "game_exit"


QUIP_POOLS: dict[QuipCategory, tuple[str, ...]] = {
    QuipCategory.LOW_HP: (
        "血量危险，撤一下。",
        "回防回防。",
        "残血了主人。",
        "补给一下，别硬刚。",
    ),
    QuipCategory.BOSS: (
        "正主来了。",
        "深呼吸，稳一点。",
        "这一段我陪你看走位。",
    ),
    QuipCategory.VICTORY: (
        "漂亮。",
        "下一个。",
        "我就知道你行。",
    ),
    QuipCategory.DEFEAT: (
        "没事，再来。",
        "战术调整一下。",
        "我看到问题点了。",
    ),
    QuipCategory.LOOT: (
        "好东西。",
        "捡到了主人。",
        "这个值得拿。",
    ),
    QuipCategory.QUEST: (
        "任务我记上了。",
        "路线给你看一下？",
        "目标我帮你盯着。",
    ),
    QuipCategory.STRATEGY: (
        "攻略我查过了。",
        "这条路稳。",
        "我给你推一条。",
    ),
    QuipCategory.ERROR: (
        "出岔子了。",
        "退一步看看。",
        "我排查一下。",
    ),
    QuipCategory.GENERAL: (
        "在的。",
        "嗯。",
        "记下了。",
        "知道了主人。",
    ),
    QuipCategory.GAME_ENTER: (
        "好，我陪你打。",
        "屏幕我看着了。",
    ),
    QuipCategory.GAME_EXIT: (
        "下班咯。",
        "桌面模式。",
    ),
}


KEYWORD_TO_CATEGORY: tuple[tuple[QuipCategory, tuple[str, ...]], ...] = (
    (QuipCategory.LOW_HP, ("低血量", "残血", "血量危险", "回血", "low hp", "low health")),
    (QuipCategory.BOSS, ("boss", "BOSS", "首领", "头目", "精英怪")),
    (QuipCategory.VICTORY, ("通关", "胜利", "victory", "win", "击败", "击杀完成")),
    (QuipCategory.DEFEAT, ("失败", "败北", "团灭", "defeat", "game over", "you died", "你死了")),
    (QuipCategory.LOOT, ("装备", "掉落", "稀有", "传说", "史诗", "loot", "drop")),
    (QuipCategory.QUEST, ("任务", "支线", "委托", "quest", "mission")),
    (QuipCategory.STRATEGY, ("攻略", "打法", "build", "配装", "强度", "tier")),
    (QuipCategory.ERROR, ("错误", "异常", "失败", "error", "exception", "failed")),
)


def classify_text(text: str) -> QuipCategory | None:
    if not text:
        return None
    lowered = text.lower()
    for category, keywords in KEYWORD_TO_CATEGORY:
        for keyword in keywords:
            if keyword.lower() in lowered:
                return category
    return None


def pick_quip(category: QuipCategory, *, rng: random.Random | None = None) -> str:
    pool = QUIP_POOLS.get(category) or QUIP_POOLS[QuipCategory.GENERAL]
    chooser = rng or random
    return chooser.choice(pool)
