from __future__ import annotations

import random

from app.companion.scene import Scene


BASE_QUIPS: dict[Scene, tuple[str, ...]] = {
    Scene.IDLE: (
        "在线。",
        "桌面待命。",
        "随叫随到。",
    ),
    Scene.GAME_WARMING: (
        "陪玩通道开启。",
        "屏幕识别已激活。",
        "节奏调整中。",
    ),
    Scene.GAME_ACTIVE: (
        "在你身边。",
        "持续观察中。",
        "屏幕已纳入视野。",
    ),
    Scene.COMBAT: (
        "进入战斗。",
        "节奏拉起来。",
        "小心走位。",
    ),
    Scene.BOSS: (
        "BOSS 在前。",
        "硬骨头，稳。",
        "上技能。",
        "高强度阶段。",
    ),
    Scene.VICTORY_AFTERGLOW: (
        "干净利落。",
        "下一个。",
        "效率不错。",
    ),
    Scene.DEFEAT_REGROUP: (
        "再来一次。",
        "策略需要调整。",
        "整顿一下重开。",
    ),
    Scene.AFK: (
        "在线待命。",
        "等你回来。",
    ),
    Scene.HOMECOMING: (
        "欢迎回来，主人。",
        "已恢复在线。",
        "继续陪你。",
    ),
}


REPETITION_QUIPS: dict[Scene, tuple[str, ...]] = {
    Scene.DEFEAT_REGROUP: (
        "又是这招。",
        "第几次了。",
        "建议换个打法。",
        "这 boss 你有点执念。",
    ),
    Scene.VICTORY_AFTERGLOW: (
        "手感上来了。",
        "连胜确认。",
        "这局节奏完全在你手上。",
    ),
    Scene.BOSS: (
        "熟悉的画面。",
        "又见你了，老朋友。",
    ),
    Scene.GAME_WARMING: (
        "这游戏你最近玩得很勤。",
        "回到熟悉的地图。",
    ),
}


GAME_ENTRY_TRANSITION_QUIPS: tuple[str, ...] = (
    "陪玩通道开启。",
    "进入游戏模式。",
    "屏幕分析已就位。",
)

GAME_EXIT_TRANSITION_QUIPS: tuple[str, ...] = (
    "退出战斗。",
    "回到桌面模式。",
)


def pick_scene_quip(
    scene: Scene,
    *,
    use_repetition: bool = False,
    rng: random.Random | None = None,
) -> str:
    chooser = rng or random
    if use_repetition:
        pool = REPETITION_QUIPS.get(scene) or BASE_QUIPS.get(scene)
    else:
        pool = BASE_QUIPS.get(scene)
    if not pool:
        pool = BASE_QUIPS[Scene.IDLE]
    return chooser.choice(pool)


def pick_transition_quip(*, entering_game: bool, rng: random.Random | None = None) -> str:
    chooser = rng or random
    pool = GAME_ENTRY_TRANSITION_QUIPS if entering_game else GAME_EXIT_TRANSITION_QUIPS
    return chooser.choice(pool)
