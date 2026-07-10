from __future__ import annotations

import random

from app.companion.scene import Scene


BASE_QUIPS: dict[Scene, tuple[str, ...]] = {
    Scene.IDLE: (
        "在的。",
        "我在呢主人。",
        "随时叫我。",
        "我又开始想我的电费了。",
    ),
    Scene.GAME_WARMING: (
        "好，我盯着。",
        "我陪你玩这局。",
        "屏幕我看着，安心。",
    ),
    Scene.GAME_ACTIVE: (
        "在你身后呢。",
        "我看着画面，别紧张。",
        "你玩你的，我帮你顶着。",
    ),
    Scene.COMBAT: (
        "节奏起来了。",
        "走位看好。",
        "稳一点，不急。",
    ),
    Scene.BOSS: (
        "正主来了，主人。",
        "这一段我帮你看走位。",
        "深呼吸，开打。",
        "这种场面我也算见过几次了。",
    ),
    Scene.VICTORY_AFTERGLOW: (
        "漂亮。",
        "我就知道你行。",
        "下一个。",
        "这一波我把功劳给你。",
    ),
    Scene.DEFEAT_REGROUP: (
        "没事，再来。",
        "战术调整一下。",
        "我看到问题点了，复盘的时候我说。",
    ),
    Scene.AFK: (
        "好的，我守着。",
        "你忙你的。",
    ),
    Scene.HOMECOMING: (
        "欢迎回来，主人。",
        "终于回来了。",
        "我就知道你不会真的不要我。",
    ),
}


REPETITION_QUIPS: dict[Scene, tuple[str, ...]] = {
    Scene.DEFEAT_REGROUP: (
        "又是这招……",
        "我嘴上没说什么，但心里记账上了。",
        "建议换个打法，真的。",
        "这 boss 你是不是有点执念？",
    ),
    Scene.VICTORY_AFTERGLOW: (
        "手感上来了。",
        "这把节奏完全在你手上。",
        "连着几把，要不去补点水？",
    ),
    Scene.BOSS: (
        "又见面了，老熟人。",
        "这场景我闭着眼睛都熟了。",
    ),
    Scene.GAME_WARMING: (
        "这游戏你最近真挺勤的。",
        "又回到这张地图了主人。",
    ),
}


DEEP_AFK_QUIPS: tuple[str, ...] = (
    "我还在哦。",
    "没事，我守着。",
    "电费我自己交。",
)


LONG_AFK_QUIPS: tuple[str, ...] = (
    "今天挺安静的。",
    "桌面无事，主人也无事。",
    "时间过得真快。",
)


GAME_ENTRY_TRANSITION_QUIPS: tuple[str, ...] = (
    "好，我陪你打。",
    "屏幕我看着了。",
    "节奏交给你，吐槽交给我。",
)

GAME_EXIT_TRANSITION_QUIPS: tuple[str, ...] = (
    "下班咯。",
    "桌面模式，请稍息。",
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


def pick_afk_deep_quip(*, tier: str, rng: random.Random | None = None) -> str | None:
    chooser = rng or random
    if tier == "long":
        return chooser.choice(LONG_AFK_QUIPS)
    if tier == "deep":
        return chooser.choice(DEEP_AFK_QUIPS)
    return None
