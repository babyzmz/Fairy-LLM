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
        "血量危险。",
        "回防。",
        "残血，撤。",
        "补给一下，主人。",
        "再硬刚一下要死的。",
    ),
    QuipCategory.BOSS: (
        "BOSS 在前。",
        "硬骨头，稳。",
        "进入高强度阶段。",
        "上技能。",
        "节奏拉满。",
    ),
    QuipCategory.VICTORY: (
        "下一个。",
        "解决。",
        "干净利落。",
        "通关确认。",
        "效率不错。",
    ),
    QuipCategory.DEFEAT: (
        "再来一次。",
        "策略需要调整。",
        "整顿一下重开。",
        "失败已记录。",
    ),
    QuipCategory.LOOT: (
        "好东西。",
        "装备到手。",
        "捡漏成功。",
        "值得收下。",
    ),
    QuipCategory.QUEST: (
        "任务在身。",
        "目标已锁定。",
        "路线规划中。",
    ),
    QuipCategory.STRATEGY: (
        "攻略已查。",
        "走这条线。",
        "推荐路径已就位。",
    ),
    QuipCategory.ERROR: (
        "异常。",
        "回退一步。",
        "排查中。",
    ),
    QuipCategory.GENERAL: (
        "在。",
        "在线。",
        "记下了。",
        "知道了。",
        "明白。",
    ),
    QuipCategory.GAME_ENTER: (
        "游戏模式启动。",
        "陪玩通道开启。",
        "屏幕分析进入待命。",
    ),
    QuipCategory.GAME_EXIT: (
        "退出战斗。",
        "恢复桌面模式。",
        "陪玩通道关闭。",
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
