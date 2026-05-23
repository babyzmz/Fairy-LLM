from __future__ import annotations

import unittest

from app.companion.scene import Scene
from app.companion.short_memory import ShortMemory


class ShortMemoryTests(unittest.TestCase):
    def test_repetition_threshold(self) -> None:
        mem = ShortMemory(repeat_threshold=3)
        mem.record(Scene.DEFEAT_REGROUP, "原神")
        mem.record(Scene.DEFEAT_REGROUP, "原神")
        self.assertFalse(mem.is_repeating(Scene.DEFEAT_REGROUP, "原神"))
        mem.record(Scene.DEFEAT_REGROUP, "原神")
        self.assertTrue(mem.is_repeating(Scene.DEFEAT_REGROUP, "原神"))

    def test_different_games_isolated(self) -> None:
        mem = ShortMemory(repeat_threshold=2)
        mem.record(Scene.DEFEAT_REGROUP, "原神")
        mem.record(Scene.DEFEAT_REGROUP, "原神")
        self.assertTrue(mem.is_repeating(Scene.DEFEAT_REGROUP, "原神"))
        self.assertFalse(mem.is_repeating(Scene.DEFEAT_REGROUP, "黑神话"))

    def test_capacity_bounded(self) -> None:
        mem = ShortMemory(capacity=5)
        for _ in range(20):
            mem.record(Scene.GAME_ACTIVE, "原神")
        self.assertLessEqual(sum(mem.snapshot().values()), 5)

    def test_clear(self) -> None:
        mem = ShortMemory()
        mem.record(Scene.BOSS, "黑神话")
        mem.clear()
        self.assertEqual(mem.repetition_count(Scene.BOSS, "黑神话"), 0)


if __name__ == "__main__":
    unittest.main()
