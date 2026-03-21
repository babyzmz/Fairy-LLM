#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for legacy skill decommissioning system.

Tests the controlled decommissioning of legacy skills:
1. Legacy skills are excluded from routing
2. Legacy code is kept for fallback
3. Legacy behavioral prompts are disabled
4. Strict deterministic mode is applied to realtime skills
"""

import logging
import unittest

from app.lazy_runtime.legacy_decommissioning import (
    DecommissioningPhase,
    LegacyPromptDisabler,
    LegacySkillConfig,
    LegacySkillDecommissioner,
    StrictDeterministicMode,
)

logger = logging.getLogger(__name__)


class TestDecommissioningPhase(unittest.TestCase):
    """Test DecommissioningPhase enum."""

    def test_phases_exist(self):
        """Verify all decommissioning phases are defined."""
        self.assertEqual(DecommissioningPhase.ACTIVE.value, "active")
        self.assertEqual(DecommissioningPhase.DEPRECATED.value, "deprecated")
        self.assertEqual(DecommissioningPhase.ARCHIVED.value, "archived")
        self.assertEqual(DecommissioningPhase.REMOVED.value, "removed")

    def test_phase_ordering(self):
        """Verify phase progression."""
        phases = [
            DecommissioningPhase.ACTIVE,
            DecommissioningPhase.DEPRECATED,
            DecommissioningPhase.ARCHIVED,
            DecommissioningPhase.REMOVED,
        ]
        self.assertEqual(len(phases), 4)


class TestLegacySkillConfig(unittest.TestCase):
    """Test LegacySkillConfig dataclass."""

    def test_config_creation(self):
        """Verify config can be created with all fields."""
        config = LegacySkillConfig(
            name="test_skill",
            phase=DecommissioningPhase.DEPRECATED,
            reason="Replaced by new skill",
            replacement_skill="new_skill",
            fallback_enabled=True,
            debug_enabled=True,
            notes="Keep for fallback",
        )
        self.assertEqual(config.name, "test_skill")
        self.assertEqual(config.phase, DecommissioningPhase.DEPRECATED)
        self.assertEqual(config.replacement_skill, "new_skill")
        self.assertTrue(config.fallback_enabled)
        self.assertTrue(config.debug_enabled)

    def test_config_defaults(self):
        """Verify config defaults."""
        config = LegacySkillConfig(
            name="test_skill",
            phase=DecommissioningPhase.ACTIVE,
        )
        self.assertEqual(config.reason, "")
        self.assertIsNone(config.replacement_skill)
        self.assertTrue(config.fallback_enabled)
        self.assertTrue(config.debug_enabled)
        self.assertEqual(config.notes, "")


class TestLegacySkillDecommissioner(unittest.TestCase):
    """Test LegacySkillDecommissioner class."""

    def test_legacy_skills_defined(self):
        """Verify legacy skills are defined."""
        legacy_skills = LegacySkillDecommissioner.LEGACY_SKILLS_TO_DECOMMISSION
        self.assertGreater(len(legacy_skills), 0)
        self.assertIn("web_research_skill", legacy_skills)
        self.assertIn("screen_understanding_skill", legacy_skills)
        self.assertIn("agent_shell_skill", legacy_skills)
        self.assertIn("news_intelligence_skill", legacy_skills)
        self.assertIn("document_editor_skill", legacy_skills)

    def test_is_legacy_skill(self):
        """Test is_legacy_skill detection."""
        self.assertTrue(LegacySkillDecommissioner.is_legacy_skill("web_research_skill"))
        self.assertTrue(LegacySkillDecommissioner.is_legacy_skill("screen_understanding_skill"))
        self.assertFalse(LegacySkillDecommissioner.is_legacy_skill("realtime-lookup"))
        self.assertFalse(LegacySkillDecommissioner.is_legacy_skill("unknown_skill"))

    def test_get_config(self):
        """Test getting decommissioning config."""
        config = LegacySkillDecommissioner.get_config("web_research_skill")
        self.assertIsNotNone(config)
        self.assertEqual(config.name, "web_research_skill")
        self.assertEqual(config.phase, DecommissioningPhase.DEPRECATED)
        self.assertEqual(config.replacement_skill, "web-research")

        # Non-legacy skill returns None
        config = LegacySkillDecommissioner.get_config("unknown_skill")
        self.assertIsNone(config)

    def test_should_exclude_from_routing(self):
        """Test routing exclusion logic."""
        # Deprecated skills should be excluded
        self.assertTrue(LegacySkillDecommissioner.should_exclude_from_routing("web_research_skill"))
        self.assertTrue(LegacySkillDecommissioner.should_exclude_from_routing("screen_understanding_skill"))

        # Non-legacy skills should not be excluded
        self.assertFalse(LegacySkillDecommissioner.should_exclude_from_routing("realtime-lookup"))
        self.assertFalse(LegacySkillDecommissioner.should_exclude_from_routing("unknown_skill"))

    def test_get_replacement_skill(self):
        """Test getting replacement skill."""
        replacement = LegacySkillDecommissioner.get_replacement_skill("web_research_skill")
        self.assertEqual(replacement, "web-research")

        replacement = LegacySkillDecommissioner.get_replacement_skill("screen_understanding_skill")
        self.assertEqual(replacement, "screen-understanding")

        # Non-legacy skill returns None
        replacement = LegacySkillDecommissioner.get_replacement_skill("unknown_skill")
        self.assertIsNone(replacement)

    def test_log_decommissioning_status(self):
        """Test logging decommissioning status."""
        # Should not raise exception
        LegacySkillDecommissioner.log_decommissioning_status()


class TestLegacyPromptDisabler(unittest.TestCase):
    """Test LegacyPromptDisabler class."""

    def test_patterns_defined(self):
        """Verify patterns are defined."""
        patterns = LegacyPromptDisabler.PATTERNS_TO_DISABLE
        self.assertGreater(len(patterns), 0)
        # Check for key pattern categories
        pattern_str = " ".join(patterns)
        self.assertIn("主动", pattern_str)  # Suggestion patterns
        self.assertIn("讲述", pattern_str)  # Narrative patterns
        self.assertIn("解释", pattern_str)  # Educational patterns

    def test_create_minimal_system_prompt(self):
        """Test minimal system prompt creation."""
        prompt = LegacyPromptDisabler.create_minimal_system_prompt()
        self.assertIsNotNone(prompt)
        self.assertIn("Fairy", prompt)
        self.assertIn("direct", prompt)
        self.assertIn("factual", prompt)
        # Should NOT contain behavioral patterns
        self.assertNotIn("主动推进", prompt)
        self.assertNotIn("建议", prompt)

    def test_should_disable_prompt_expansion(self):
        """Test prompt expansion disabling logic."""
        # Realtime skills should have expansion disabled
        self.assertTrue(LegacyPromptDisabler.should_disable_prompt_expansion("realtime-lookup"))

        # Other skills should not have expansion disabled
        self.assertFalse(LegacyPromptDisabler.should_disable_prompt_expansion("web-research"))
        self.assertFalse(LegacyPromptDisabler.should_disable_prompt_expansion("unknown_skill"))

    def test_filter_prompt_removes_patterns(self):
        """Test that filter_prompt removes disabled patterns."""
        original_prompt = """
You are an assistant.
主动推进用户的需求。
建议用户尝试新功能。
讲述相关的背景故事。
"""
        filtered = LegacyPromptDisabler.filter_prompt(original_prompt, "realtime-lookup")
        # Filtered prompt should not contain the disabled patterns
        self.assertNotIn("主动推进", filtered)
        self.assertNotIn("建议", filtered)
        self.assertNotIn("讲述", filtered)
        self.assertNotIn("背景", filtered)

    def test_filter_prompt_preserves_core(self):
        """Test that filter_prompt preserves core content."""
        original_prompt = """
You are an assistant.
主动推进用户的需求。
Respond directly to user requests.
"""
        filtered = LegacyPromptDisabler.filter_prompt(original_prompt, "realtime-lookup")
        # Core content should be preserved
        self.assertIn("You are an assistant", filtered)
        self.assertIn("Respond directly", filtered)

    def test_filter_prompt_non_realtime_skill(self):
        """Test that filter_prompt doesn't filter non-realtime skills."""
        original_prompt = """
You are an assistant.
主动推进用户的需求。
建议用户尝试新功能。
"""
        filtered = LegacyPromptDisabler.filter_prompt(original_prompt, "web-research")
        # Should not filter for non-realtime skills
        self.assertEqual(filtered, original_prompt)


class TestStrictDeterministicMode(unittest.TestCase):
    """Test StrictDeterministicMode class."""

    def test_strict_mode_skills_defined(self):
        """Verify strict mode skills are defined."""
        skills = StrictDeterministicMode.STRICT_MODE_SKILLS
        self.assertGreater(len(skills), 0)
        self.assertIn("realtime-lookup", skills)

    def test_strict_mode_card_types_defined(self):
        """Verify strict mode card types are defined."""
        card_types = StrictDeterministicMode.STRICT_MODE_CARD_TYPES
        self.assertGreater(len(card_types), 0)
        self.assertIn("time", card_types)
        self.assertIn("crypto", card_types)
        self.assertIn("stock", card_types)
        self.assertIn("fx_rate", card_types)
        self.assertIn("fuel_price", card_types)
        self.assertIn("weather", card_types)

    def test_should_use_strict_mode_for_realtime_skills(self):
        """Test strict mode detection for realtime skills."""
        # Realtime skills should use strict mode
        self.assertTrue(StrictDeterministicMode.should_use_strict_mode("realtime-lookup"))

        # Other skills should not use strict mode
        self.assertFalse(StrictDeterministicMode.should_use_strict_mode("web-research"))
        self.assertFalse(StrictDeterministicMode.should_use_strict_mode("unknown_skill"))

    def test_should_use_strict_mode_with_card_type(self):
        """Test strict mode detection with card type."""
        # Realtime skills with strict card types should use strict mode
        self.assertTrue(StrictDeterministicMode.should_use_strict_mode("realtime-lookup", "time"))
        self.assertTrue(StrictDeterministicMode.should_use_strict_mode("realtime-lookup", "crypto"))
        self.assertTrue(StrictDeterministicMode.should_use_strict_mode("realtime-lookup", "stock"))

        # Realtime skills with non-strict card types should not use strict mode
        self.assertFalse(StrictDeterministicMode.should_use_strict_mode("realtime-lookup", "unknown_type"))

        # Non-realtime skills should not use strict mode regardless of card type
        self.assertFalse(StrictDeterministicMode.should_use_strict_mode("web-research", "time"))

    def test_apply_strict_mode(self):
        """Test applying strict mode to response."""
        response = {
            "skill_name": "realtime-lookup",
            "card_type": "crypto",
            "answer": "BTC: $42350.0 USD",
        }
        result = StrictDeterministicMode.apply_strict_mode(response)
        self.assertTrue(result.get("strict_mode"))
        self.assertTrue(result.get("tool_lock"))
        self.assertTrue(result.get("no_expansion"))
        self.assertTrue(result.get("deterministic"))

    def test_apply_strict_mode_empty_response(self):
        """Test applying strict mode to empty response."""
        response = {}
        result = StrictDeterministicMode.apply_strict_mode(response)
        self.assertTrue(result.get("strict_mode"))
        self.assertTrue(result.get("tool_lock"))
        self.assertTrue(result.get("no_expansion"))
        self.assertTrue(result.get("deterministic"))


class TestIntegration(unittest.TestCase):
    """Integration tests for legacy decommissioning system."""

    def test_legacy_skill_workflow(self):
        """Test complete legacy skill workflow."""
        # Check if skill is legacy
        self.assertTrue(LegacySkillDecommissioner.is_legacy_skill("web_research_skill"))

        # Get config
        config = LegacySkillDecommissioner.get_config("web_research_skill")
        self.assertIsNotNone(config)

        # Check if should be excluded from routing
        self.assertTrue(LegacySkillDecommissioner.should_exclude_from_routing("web_research_skill"))

        # Get replacement skill
        replacement = LegacySkillDecommissioner.get_replacement_skill("web_research_skill")
        self.assertEqual(replacement, "web-research")

    def test_realtime_skill_workflow(self):
        """Test realtime skill with strict deterministic mode."""
        # Check if should use strict mode
        self.assertTrue(StrictDeterministicMode.should_use_strict_mode("realtime-lookup", "time"))

        # Apply strict mode
        response = {
            "skill_name": "realtime-lookup",
            "card_type": "time",
            "answer": "Tokyo: 2026-03-19 23:30 (night)",
        }
        result = StrictDeterministicMode.apply_strict_mode(response)
        self.assertTrue(result.get("strict_mode"))
        self.assertTrue(result.get("deterministic"))

    def test_prompt_filtering_workflow(self):
        """Test prompt filtering workflow."""
        # Create a prompt with behavioral patterns
        prompt = """
You are Fairy, a personal AI assistant.
主动推进用户的需求。
建议用户尝试新功能。
讲述相关的背景故事。
Respond directly to user requests.
"""
        # Filter for realtime skill
        filtered = LegacyPromptDisabler.filter_prompt(prompt, "realtime-lookup")

        # Verify patterns are removed
        self.assertNotIn("主动推进", filtered)
        self.assertNotIn("建议", filtered)
        self.assertNotIn("讲述", filtered)

        # Verify core content is preserved
        self.assertIn("Fairy", filtered)
        self.assertIn("Respond directly", filtered)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    unittest.main()
