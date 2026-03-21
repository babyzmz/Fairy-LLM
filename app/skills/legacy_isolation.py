"""Legacy skill isolation layer.

This module provides a controlled interface to legacy Python skills.
Legacy skills are only loaded if explicitly enabled via feature flags.
This prevents auto-registration and ensures clean separation from the new lazy runtime.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class LegacySkillRegistry:
    """Registry for legacy skills with feature flag control."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._skills: dict[str, Any] = {}
        self._loaded = False

    def enable(self) -> None:
        """Enable legacy skill loading."""
        self.enabled = True

    def disable(self) -> None:
        """Disable legacy skill loading."""
        self.enabled = False

    def load_skills(self) -> dict[str, Any]:
        """Load legacy skills if enabled.

        Returns:
            Dictionary of loaded legacy skills, or empty dict if disabled.
        """
        if not self.enabled or self._loaded:
            return self._skills

        if not self.enabled:
            logger.info("Legacy skills are disabled. Skipping load.")
            return {}

        try:
            # Import legacy skills only when explicitly enabled
            from skills.web_research_skill import web_search_skill
            from skills.web_research import run_web_research

            self._skills = {
                "web_search_skill": web_search_skill,
                "run_web_research": run_web_research,
            }
            self._loaded = True
            logger.info("Legacy skills loaded successfully. Count=%s", len(self._skills))
        except ImportError as e:
            logger.warning("Failed to load legacy skills: %s", e)
            self._skills = {}

        return self._skills

    def get_skill(self, name: str) -> Any | None:
        """Get a specific legacy skill by name.

        Args:
            name: Skill name

        Returns:
            Skill callable or None if not found/disabled
        """
        if not self.enabled:
            return None
        if not self._loaded:
            self.load_skills()
        return self._skills.get(name)

    def is_available(self, name: str) -> bool:
        """Check if a legacy skill is available.

        Args:
            name: Skill name

        Returns:
            True if skill is available and enabled
        """
        if not self.enabled:
            return False
        if not self._loaded:
            self.load_skills()
        return name in self._skills


# Global registry instance
_legacy_registry: LegacySkillRegistry | None = None


def get_legacy_registry() -> LegacySkillRegistry:
    """Get or create the global legacy skill registry."""
    global _legacy_registry
    if _legacy_registry is None:
        _legacy_registry = LegacySkillRegistry(enabled=False)
    return _legacy_registry


def set_legacy_enabled(enabled: bool) -> None:
    """Set whether legacy skills are enabled.

    Args:
        enabled: True to enable legacy skills
    """
    registry = get_legacy_registry()
    if enabled:
        registry.enable()
    else:
        registry.disable()
