from __future__ import annotations

import random
import time
import unittest

from app.companion.bubble_state import BUBBLE_SHOW_SECONDS, BubbleState, PET_BURST_SECONDS
from app.companion.observer import CompanionObserver
from app.companion.quip_pool import QuipCategory


class BubbleStateTests(unittest.TestCase):
    def _build(self) -> tuple[CompanionObserver, BubbleState]:
        observer = CompanionObserver(emit_probability=1.0, cooldown_seconds=0.0, rng=random.Random(42))
        bubble = BubbleState(observer=observer)
        return observer, bubble

    def test_empty_snapshot_returns_none(self) -> None:
        _, bubble = self._build()
        self.assertIsNone(bubble.snapshot())

    def test_quip_populates_snapshot(self) -> None:
        observer, bubble = self._build()
        observer.force_emit(QuipCategory.BOSS)
        snap = bubble.snapshot()
        self.assertIsNotNone(snap)
        self.assertEqual(snap.category, QuipCategory.BOSS.value)
        self.assertGreater(snap.expires_at, snap.quip_started_at)

    def test_quip_expires_after_show_seconds(self) -> None:
        observer, bubble = self._build()
        observer.force_emit(QuipCategory.BOSS)
        bubble._quip_started_at = bubble._quip_started_at - BUBBLE_SHOW_SECONDS - 1
        self.assertIsNone(bubble.snapshot())

    def test_pet_trigger_sets_pet_burst(self) -> None:
        _, bubble = self._build()
        started = bubble.trigger_pet()
        self.assertGreater(started, 0)
        snap = bubble.snapshot()
        self.assertIsNotNone(snap)
        self.assertIsNotNone(snap.pet_started_at)
        self.assertIsNotNone(snap.pet_expires_at)
        self.assertAlmostEqual(snap.pet_expires_at - snap.pet_started_at, PET_BURST_SECONDS, places=1)

    def test_mute_clears_current_quip(self) -> None:
        observer, bubble = self._build()
        observer.force_emit(QuipCategory.BOSS)
        bubble.set_muted(True)
        snap = bubble.snapshot()
        self.assertTrue(observer.muted)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.quip, "")
        self.assertTrue(snap.muted)


if __name__ == "__main__":
    unittest.main()
