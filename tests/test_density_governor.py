from __future__ import annotations

import unittest

from app.companion.density_governor import DensityGovernor


class DensityGovernorTests(unittest.TestCase):
    def test_below_soft_cap_no_density_decay(self) -> None:
        gov = DensityGovernor(window_seconds=180.0, soft_cap=5, hard_cap=9, energy_drain_per_emit=0.0)
        for _ in range(4):
            gov.record_emission()
        self.assertAlmostEqual(gov.gate(0.4), 0.4, places=3)

    def test_at_soft_cap_starts_decay(self) -> None:
        gov = DensityGovernor(window_seconds=180.0, soft_cap=5, hard_cap=9, energy_drain_per_emit=0.0)
        for _ in range(5):
            gov.record_emission()
        gated = gov.gate(0.4)
        self.assertLess(gated, 0.4)
        self.assertGreater(gated, 0.0)

    def test_at_hard_cap_zero(self) -> None:
        gov = DensityGovernor(window_seconds=180.0, soft_cap=5, hard_cap=9, energy_drain_per_emit=0.0)
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


class MoodEnergyTests(unittest.TestCase):
    def test_starts_at_full(self) -> None:
        gov = DensityGovernor()
        self.assertAlmostEqual(gov.current_energy(), 1.0, places=3)

    def test_emit_drains_energy(self) -> None:
        gov = DensityGovernor(energy_drain_per_emit=0.2, energy_recovery_per_second=0.0)
        gov.record_emission()
        self.assertAlmostEqual(gov.current_energy(), 0.8, places=2)
        gov.record_emission()
        self.assertAlmostEqual(gov.current_energy(), 0.6, places=2)

    def test_energy_recovers_over_time(self) -> None:
        gov = DensityGovernor(energy_drain_per_emit=0.5, energy_recovery_per_second=0.5)
        gov.record_emission()
        import time

        time.sleep(0.4)
        self.assertGreater(gov.current_energy(), 0.6)

    def test_energy_floor(self) -> None:
        gov = DensityGovernor(energy_drain_per_emit=1.0, energy_recovery_per_second=0.0, energy_floor=0.05)
        for _ in range(5):
            gov.record_emission()
        self.assertAlmostEqual(gov.current_energy(), 0.05, places=2)

    def test_gate_multiplies_density_and_energy(self) -> None:
        gov = DensityGovernor(
            soft_cap=2,
            hard_cap=10,
            energy_drain_per_emit=0.4,
            energy_recovery_per_second=0.0,
        )
        gov.record_emission()
        gov.record_emission()
        baseline_density = gov._density_factor()  # type: ignore[attr-defined]
        energy = gov.current_energy()
        self.assertAlmostEqual(gov.gate(0.4), 0.4 * baseline_density * energy, places=3)


if __name__ == "__main__":
    unittest.main()
