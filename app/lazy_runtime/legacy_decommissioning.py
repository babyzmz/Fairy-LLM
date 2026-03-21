#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Controlled decommissioning of legacy skills.

This module manages the gradual transition from legacy skills to the new lazy runtime
without breaking existing dependencies or removing code.

Key principles:
1. Legacy code is kept physically for fallback and debugging
2. Legacy skills are removed from active routing
3. Legacy behavioral prompts are disabled
4. New lazy runtime has strict deterministic mode for realtime skills
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class DecommissioningPhase(Enum):
    """Phases of skill decommissioning."""

    ACTIVE = "active"  # Skill is actively used in routing
    DEPRECATED = "deprecated"  # Skill is deprecated but still available
    ARCHIVED = "archived"  # Skill is archived, not in routing
    REMOVED = "removed"  # Skill code is removed


@dataclass
class LegacySkillConfig:
    """Configuration for a legacy skill during decommissioning."""

    name: str
    phase: DecommissioningPhase
    reason: str = ""
    replacement_skill: str | None = None
    fallback_enabled: bool = True  # Keep for fallback
    debug_enabled: bool = True  # Keep for debugging
    notes: str = ""


class LegacySkillDecommissioner:
    """Manages controlled decommissioning of legacy skills."""

    # Legacy skills that should be removed from active routing
    LEGACY_SKILLS_TO_DECOMMISSION = {
        "web_research_skill": LegacySkillConfig(
            name="web_research_skill",
            phase=DecommissioningPhase.DEPRECATED,
            reason="Replaced by web-research bundle in lazy runtime",
            replacement_skill="web-research",
            fallback_enabled=True,
            debug_enabled=True,
            notes="Keep for fallback if lazy runtime fails",
        ),
        "screen_understanding_skill": LegacySkillConfig(
            name="screen_understanding_skill",
            phase=DecommissioningPhase.DEPRECATED,
            reason="Replaced by screen-understanding bundle in lazy runtime",
            replacement_skill="screen-understanding",
            fallback_enabled=True,
            debug_enabled=True,
            notes="Keep for fallback if lazy runtime fails",
        ),
        "agent_shell_skill": LegacySkillConfig(
            name="agent_shell_skill",
            phase=DecommissioningPhase.DEPRECATED,
            reason="Replaced by terminal-agent bundle in lazy runtime",
            replacement_skill="terminal-agent",
            fallback_enabled=True,
            debug_enabled=True,
            notes="Keep for fallback if lazy runtime fails",
        ),
        "news_intelligence_skill": LegacySkillConfig(
            name="news_intelligence_skill",
            phase=DecommissioningPhase.DEPRECATED,
            reason="Replaced by news-intelligence bundle in lazy runtime",
            replacement_skill="news-intelligence",
            fallback_enabled=True,
            debug_enabled=True,
            notes="Keep for fallback if lazy runtime fails",
        ),
        "document_editor_skill": LegacySkillConfig(
            name="document_editor_skill",
            phase=DecommissioningPhase.DEPRECATED,
            reason="Replaced by document-editing bundle in lazy runtime",
            replacement_skill="document-editing",
            fallback_enabled=True,
            debug_enabled=True,
            notes="Keep for fallback if lazy runtime fails",
        ),
    }

    @staticmethod
    def is_legacy_skill(skill_name: str) -> bool:
        """Check if a skill is a legacy skill being decommissioned."""
        return skill_name in LegacySkillDecommissioner.LEGACY_SKILLS_TO_DECOMMISSION

    @staticmethod
    def get_config(skill_name: str) -> LegacySkillConfig | None:
        """Get decommissioning config for a skill."""
        return LegacySkillDecommissioner.LEGACY_SKILLS_TO_DECOMMISSION.get(skill_name)

    @staticmethod
    def should_exclude_from_routing(skill_name: str) -> bool:
        """Check if a skill should be excluded from active routing."""
        config = LegacySkillDecommissioner.get_config(skill_name)
        if not config:
            return False

        # Exclude deprecated and archived skills from routing
        return config.phase in {DecommissioningPhase.DEPRECATED, DecommissioningPhase.ARCHIVED}

    @staticmethod
    def get_replacement_skill(skill_name: str) -> str | None:
        """Get the replacement skill for a legacy skill."""
        config = LegacySkillDecommissioner.get_config(skill_name)
        if config:
            return config.replacement_skill
        return None

    @staticmethod
    def log_decommissioning_status() -> None:
        """Log the status of all legacy skills."""
        logger.info("\n" + "=" * 70)
        logger.info("LEGACY SKILL DECOMMISSIONING STATUS")
        logger.info("=" * 70)

        for skill_name, config in LegacySkillDecommissioner.LEGACY_SKILLS_TO_DECOMMISSION.items():
            logger.info(f"\n{skill_name}:")
            logger.info(f"  Phase: {config.phase.value}")
            logger.info(f"  Reason: {config.reason}")
            if config.replacement_skill:
                logger.info(f"  Replacement: {config.replacement_skill}")
            logger.info(f"  Fallback enabled: {config.fallback_enabled}")
            logger.info(f"  Debug enabled: {config.debug_enabled}")
            if config.notes:
                logger.info(f"  Notes: {config.notes}")

        logger.info("\n" + "=" * 70 + "\n")


class LegacyPromptDisabler:
    """Disables legacy behavioral system prompts."""

    # Patterns in legacy prompts that should be disabled
    PATTERNS_TO_DISABLE = [
        # Suggestion-style patterns
        r"主动推进",
        r"主动提出",
        r"主动建议",
        r"可以尝试",
        r"建议",
        r"不妨",
        # Narrative expansion patterns
        r"讲述",
        r"叙述",
        r"故事",
        r"背景",
        r"上下文",
        # Educational expansion patterns
        r"解释",
        r"说明",
        r"教学",
        r"学习",
        # Cross-task contextual patterns
        r"之前",
        r"上次",
        r"历史",
        r"记得",
        r"还记得",
    ]

    @staticmethod
    def create_minimal_system_prompt() -> str:
        """Create a minimal system prompt with only core identity.

        This replaces the legacy behavioral prompt with a strict,
        deterministic prompt that prevents expansion and narrative.
        """
        return """\
You are Fairy, a personal AI assistant running on the user's computer.

Your role:
- Understand what the user wants to accomplish
- Provide direct, factual answers
- Use tools when necessary
- Do not add narrative, suggestions, or educational content

For realtime data (time, prices, weather, etc.):
- Return only the factual data from the tool
- Do not add explanations or context
- Do not rewrite or interpret the data
- Preserve all numeric and datetime values exactly

Respond concisely and directly.
"""

    @staticmethod
    def should_disable_prompt_expansion(skill_name: str) -> bool:
        """Check if prompt expansion should be disabled for a skill."""
        # Disable expansion for realtime skills
        realtime_skills = {"realtime-lookup"}
        return skill_name in realtime_skills

    @staticmethod
    def filter_prompt(prompt: str, skill_name: str) -> str:
        """Filter a prompt to remove disabled patterns.

        Args:
            prompt: Original prompt text
            skill_name: Name of the skill

        Returns:
            Filtered prompt with disabled patterns removed
        """
        if not LegacyPromptDisabler.should_disable_prompt_expansion(skill_name):
            return prompt

        filtered = prompt
        for pattern in LegacyPromptDisabler.PATTERNS_TO_DISABLE:
            # Remove lines containing disabled patterns
            lines = filtered.split("\n")
            filtered_lines = [
                line for line in lines
                if not any(p in line for p in LegacyPromptDisabler.PATTERNS_TO_DISABLE)
            ]
            filtered = "\n".join(filtered_lines)

        return filtered


class StrictDeterministicMode:
    """Strict deterministic response mode for realtime skills."""

    # Skills that should use strict deterministic mode
    STRICT_MODE_SKILLS = {
        "realtime-lookup",
    }

    # Card types that require strict mode
    STRICT_MODE_CARD_TYPES = {
        "time",
        "crypto",
        "stock",
        "fx_rate",
        "fuel_price",
        "weather",
    }

    @staticmethod
    def should_use_strict_mode(skill_name: str, card_type: str | None = None) -> bool:
        """Check if strict deterministic mode should be used.

        Args:
            skill_name: Name of the skill
            card_type: Type of card (optional)

        Returns:
            True if strict mode should be used
        """
        if skill_name not in StrictDeterministicMode.STRICT_MODE_SKILLS:
            return False

        if card_type is None:
            return True

        return card_type in StrictDeterministicMode.STRICT_MODE_CARD_TYPES

    @staticmethod
    def apply_strict_mode(response: dict[str, Any]) -> dict[str, Any]:
        """Apply strict deterministic mode to a response.

        Args:
            response: Response dict from skill

        Returns:
            Response with strict mode applied
        """
        response["strict_mode"] = True
        response["tool_lock"] = True
        response["no_expansion"] = True
        response["deterministic"] = True

        logger.info(
            "strict_deterministic_mode_applied skill=%s card_type=%s",
            response.get("skill_name", "unknown"),
            response.get("card_type", "unknown"),
        )

        return response


def initialize_decommissioning() -> None:
    """Initialize legacy skill decommissioning system."""
    logger.info("Initializing legacy skill decommissioning system...")

    # Log decommissioning status
    LegacySkillDecommissioner.log_decommissioning_status()

    # Log strict mode configuration
    logger.info("Strict deterministic mode enabled for: %s", LegacySkillDecommissioner.LEGACY_SKILLS_TO_DECOMMISSION.keys())

    logger.info("Legacy skill decommissioning system initialized.")
