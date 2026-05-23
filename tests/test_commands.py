from __future__ import annotations

import unittest

from app.commands.builtins import (
    MUTE_SPEC,
    PET_SPEC,
    SCREENSHOT_SPEC,
    STRATEGY_SPEC,
    register_builtins,
)
from app.commands.registry import CommandKind, CommandRegistry


class CommandRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = CommandRegistry()
        register_builtins(self.registry)

    def test_all_builtins_registered(self) -> None:
        names = {spec.name for spec in self.registry.specs}
        self.assertEqual(names, {SCREENSHOT_SPEC.name, STRATEGY_SPEC.name, PET_SPEC.name, MUTE_SPEC.name})

    def test_find_by_alias(self) -> None:
        spec = self.registry.find("/screenshot")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.name, SCREENSHOT_SPEC.name)

    def test_find_returns_none_for_unknown(self) -> None:
        self.assertIsNone(self.registry.find("/不存在"))

    def test_kind_distribution(self) -> None:
        kinds = {spec.kind for spec in self.registry.specs}
        self.assertIn(CommandKind.CLIENT_ACTION, kinds)
        self.assertIn(CommandKind.REWRITE_MESSAGE, kinds)
        self.assertIn(CommandKind.SERVER_ACTION, kinds)

    def test_rewrite_template_present(self) -> None:
        self.assertIn("{arg}", STRATEGY_SPEC.rewrite_template or "")

    def test_server_action_has_handler(self) -> None:
        result = self.registry.execute_server(PET_SPEC.server_handler_name or "", {})
        self.assertIn("pet_started_at", result)

    def test_to_dict_contains_kind_string(self) -> None:
        payload = STRATEGY_SPEC.to_dict()
        self.assertEqual(payload["kind"], "rewrite_message")


if __name__ == "__main__":
    unittest.main()
