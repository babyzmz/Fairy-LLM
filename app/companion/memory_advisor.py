from __future__ import annotations

from app.companion.persistent_memory import PersistentMemory, get_persistent_memory
from app.companion.scene import Scene
from app.companion.short_memory import ShortMemory, get_short_memory


REPEAT_VICTORY_STREAK = 3
REPEAT_DEFEAT_STREAK = 2
HEAVY_GAME_THRESHOLD = 5


class RepetitionAdvisor:
    def __init__(
        self,
        *,
        short_memory: ShortMemory | None = None,
        persistent_memory: PersistentMemory | None = None,
    ) -> None:
        self._short = short_memory or get_short_memory()
        self._persistent = persistent_memory or get_persistent_memory()

    def __call__(self, scene: Scene, game_name: str | None) -> bool:
        if scene == Scene.DEFEAT_REGROUP:
            if self._short.is_repeating(scene, game_name):
                return True
            if self._persistent.snapshot().consecutive_defeats >= REPEAT_DEFEAT_STREAK:
                return True
            return False
        if scene == Scene.VICTORY_AFTERGLOW:
            return self._persistent.snapshot().consecutive_victories >= REPEAT_VICTORY_STREAK
        if scene == Scene.BOSS:
            return self._short.is_repeating(scene, game_name)
        if scene == Scene.GAME_WARMING and game_name:
            return self._persistent.games_played(game_name) >= HEAVY_GAME_THRESHOLD
        return False

    def record_scene_entry(self, scene: Scene, game_name: str | None) -> None:
        self._short.record(scene, game_name)
        if scene == Scene.GAME_WARMING and game_name:
            self._persistent.record_game_played(game_name)
        elif scene == Scene.VICTORY_AFTERGLOW:
            self._persistent.record_victory()
        elif scene == Scene.DEFEAT_REGROUP:
            self._persistent.record_defeat()


_singleton: RepetitionAdvisor | None = None


def get_repetition_advisor() -> RepetitionAdvisor:
    global _singleton
    if _singleton is None:
        _singleton = RepetitionAdvisor()
    return _singleton
