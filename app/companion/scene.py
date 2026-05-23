from __future__ import annotations

from enum import Enum


class Scene(str, Enum):
    IDLE = "idle"
    GAME_WARMING = "game_warming"
    GAME_ACTIVE = "game_active"
    COMBAT = "combat"
    BOSS = "boss"
    VICTORY_AFTERGLOW = "victory_afterglow"
    DEFEAT_REGROUP = "defeat_regroup"
    AFK = "afk"
    HOMECOMING = "homecoming"


GAME_WARMING_SECONDS = 60.0
VICTORY_AFTERGLOW_SECONDS = 30.0
DEFEAT_REGROUP_SECONDS = 30.0
AFK_THRESHOLD_SECONDS = 600.0
HOMECOMING_GAP_SECONDS = 2 * 60 * 60


GAME_SCENES = frozenset(
    {
        Scene.GAME_WARMING,
        Scene.GAME_ACTIVE,
        Scene.COMBAT,
        Scene.BOSS,
        Scene.VICTORY_AFTERGLOW,
        Scene.DEFEAT_REGROUP,
    }
)
