from __future__ import annotations

import unittest

from app.app_preferences import AppPreferences
from app.persona.persona_engine import PersonaEngine
from app.persona.persona_runtime import get_effective_persona_mode


class PersonaRuntimeTests(unittest.TestCase):
    def test_persona_mode_resolution(self) -> None:
        self.assertEqual(
            get_effective_persona_mode(AppPreferences(persona_enabled=False), "normal_mode"),
            "off",
        )
        self.assertEqual(
            get_effective_persona_mode(
                AppPreferences(persona_enabled=True, persona_mode="full", game_mode_force_disable_persona=True),
                "game_mode",
            ),
            "off",
        )
        self.assertEqual(
            get_effective_persona_mode(
                AppPreferences(persona_enabled=True, persona_mode="lightweight", game_mode_force_disable_persona=False),
                "normal_mode",
            ),
            "lightweight",
        )

    def test_lightweight_prompt_is_short(self) -> None:
        engine = PersonaEngine()
        prompt = engine.build_lightweight_prompt("coding")
        self.assertIn("[Persona hint]", prompt)
        self.assertLess(len(prompt), 220)


if __name__ == "__main__":
    unittest.main()
