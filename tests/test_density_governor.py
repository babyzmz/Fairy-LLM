from __future__ import annotations

import unittest

from app.companion.density_governor import DensityGovernor


class DensityGovernorTests(unittest.TestCase):
    def test_below_soft_cap_no_decay(self) -> None:
        gov = DensityGovernor(window_seconds=180.0, soft_cap=5, hard_cap=9)
        for _ in range(4):
            gov.record_emission()
        self.assertAlmostEqual(gov.gate(0.4), 0.4, places=3)

    def test_at_soft_cap_starts_decay(self) -> None:
        gov = DensityGovernor(window_seconds=180.0, soft_cap=5, hard_cap=9)
        for _ in range(5):
            gov.record_emission()
        gated = gov.gate(0.4)
        self.assertLess(gated, 0.4)
        self.assertGreater(gated, 0.0)

    def test_at_hard_cap_zero(self) -> None:
        gov = DensityGovernor(window_seconds=180.0, soft_cap=5, hard_cap=9)
        for _ in range(9):
            gov.record_emission()
        self.assertEqual(gov.gate(0.4), 0.0)

    def test_window_prunes(self) -> None:
        gov = DensityGovernor(window_seconds=0.05, soft_cap=2, hard_cap=4)
        gov.record_emission()
        gov.record_emission()
        import time

        time.sleep(0.15)
        self.assertEqual(gov.recent_count(), 0)

    def test_reset_clears(self) -> None:
        gov = DensityGovernor()
        for _ in range(3):
            gov.record_emission()
        gov.reset()
        self.assertEqual(gov.recent_count(), 0)


if __name__ == "__main__":
    unittest.main()
