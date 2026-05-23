from __future__ import annotations

import unittest

from app.companion.scene import (
    AFK_THRESHOLD_SECONDS,
    GAME_WARMING_SECONDS,
    VICTORY_AFTERGLOW_SECONDS,
    Scene,
)
from app.companion.scene_state_machine import SceneStateMachine, SceneTransition


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SceneStateMachineTests(unittest.TestCase):
    def _build(self) -> tuple[SceneStateMachine, FakeClock, list[SceneTransition]]:
        clock = FakeClock()
        sm = SceneStateMachine(afk_seconds=AFK_THRESHOLD_SECONDS, clock=clock)
        log: list[SceneTransition] = []
        sm.subscribe(log.append)
        return sm, clock, log

    def test_initial_is_idle(self) -> None:
        sm, _, _ = self._build()
        self.assertEqual(sm.scene, Scene.IDLE)

    def test_game_enter_transitions_to_warming(self) -> None:
        sm, _, log = self._build()
        sm.observe_game_enter("原神")
        self.assertEqual(sm.scene, Scene.GAME_WARMING)
        self.assertEqual(log[-1].current, Scene.GAME_WARMING)
        self.assertEqual(sm.current_game, "原神")

    def test_warming_elapses_to_active(self) -> None:
        sm, clock, _ = self._build()
        sm.observe_game_enter("原神")
        clock.advance(GAME_WARMING_SECONDS + 0.1)
        sm.tick()
        self.assertEqual(sm.scene, Scene.GAME_ACTIVE)

    def test_boss_keyword_transitions(self) -> None:
        sm, _, _ = self._build()
        sm.observe_game_enter("原神")
        sm.observe_assistant_text("BOSS 阶段二来了")
        self.assertEqual(sm.scene, Scene.BOSS)

    def test_victory_settles_back(self) -> None:
        sm, clock, _ = self._build()
        sm.observe_game_enter("原神")
        sm.observe_assistant_text("通关，下一个")
        self.assertEqual(sm.scene, Scene.VICTORY_AFTERGLOW)
        clock.advance(VICTORY_AFTERGLOW_SECONDS + 0.1)
        sm.tick()
        self.assertEqual(sm.scene, Scene.GAME_ACTIVE)

    def test_afk_after_idle_timeout(self) -> None:
        sm, clock, _ = self._build()
        clock.advance(AFK_THRESHOLD_SECONDS + 1.0)
        sm.tick()
        self.assertEqual(sm.scene, Scene.AFK)

    def test_wake_from_afk(self) -> None:
        sm, clock, _ = self._build()
        clock.advance(AFK_THRESHOLD_SECONDS + 1.0)
        sm.tick()
        sm.observe_assistant_text("我回来了")
        self.assertEqual(sm.scene, Scene.IDLE)

    def test_game_exit_clears_game(self) -> None:
        sm, _, _ = self._build()
        sm.observe_game_enter("原神")
        sm.observe_game_exit()
        self.assertEqual(sm.scene, Scene.IDLE)
        self.assertIsNone(sm.current_game)

    def test_homecoming_forced(self) -> None:
        sm, _, _ = self._build()
        sm.observe_homecoming(gap_seconds=4 * 3600)
        self.assertEqual(sm.scene, Scene.HOMECOMING)


if __name__ == "__main__":
    unittest.main()
