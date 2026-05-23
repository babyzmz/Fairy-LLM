from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.companion.persistent_memory import PersistentMemory


class PersistentMemoryTests(unittest.TestCase):
    def test_load_missing_returns_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pm = PersistentMemory(path=Path(tmp) / "absent.json")
            state = pm.load()
            self.assertEqual(state.games_played, {})
            self.assertEqual(state.last_session_ended_at, 0.0)

    def test_record_and_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "companion.json"
            pm = PersistentMemory(path=path)
            pm.load()
            pm.record_game_played("原神")
            pm.record_game_played("原神")
            pm.record_victory()
            pm.note_session_end()
            self.assertTrue(path.exists())
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["games_played"]["原神"], 2)
            self.assertEqual(raw["consecutive_victories"], 1)
            self.assertGreater(raw["last_session_ended_at"], 0)

    def test_consecutive_streaks_reset_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pm = PersistentMemory(path=Path(tmp) / "c.json")
            pm.load()
            pm.record_victory()
            pm.record_victory()
            self.assertEqual(pm.snapshot().consecutive_victories, 2)
            pm.record_defeat()
            self.assertEqual(pm.snapshot().consecutive_victories, 0)
            self.assertEqual(pm.snapshot().consecutive_defeats, 1)

    def test_homecoming_gap(self) -> None:
        import time

        with tempfile.TemporaryDirectory() as tmp:
            pm = PersistentMemory(path=Path(tmp) / "h.json")
            pm.load()
            pm._state.last_session_ended_at = time.time() - 3 * 3600  # type: ignore[attr-defined]
            should, gap = pm.should_emit_homecoming()
            self.assertTrue(should)
            self.assertGreater(gap, 2 * 3600)


if __name__ == "__main__":
    unittest.main()
