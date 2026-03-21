"""Tests for legacy skill isolation."""

from __future__ import annotations

import pytest

from app.skills.legacy_isolation import (
    LegacySkillRegistry,
    get_legacy_registry,
    set_legacy_enabled,
)


class TestLegacySkillRegistry:
    """Test legacy skill registry isolation."""

    def test_registry_disabled_by_default(self) -> None:
        """Test that registry is disabled by default."""
        registry = LegacySkillRegistry()
        assert registry.enabled is False

    def test_registry_enable_disable(self) -> None:
        """Test enabling and disabling registry."""
        registry = LegacySkillRegistry()
        assert registry.enabled is False

        registry.enable()
        assert registry.enabled is True

        registry.disable()
        assert registry.enabled is False

    def test_load_skills_when_disabled(self) -> None:
        """Test that skills are not loaded when disabled."""
        registry = LegacySkillRegistry(enabled=False)
        skills = registry.load_skills()
        assert skills == {}

    def test_get_skill_when_disabled(self) -> None:
        """Test that get_skill returns None when disabled."""
        registry = LegacySkillRegistry(enabled=False)
        skill = registry.get_skill("web_search_skill")
        assert skill is None

    def test_is_available_when_disabled(self) -> None:
        """Test that is_available returns False when disabled."""
        registry = LegacySkillRegistry(enabled=False)
        assert registry.is_available("web_search_skill") is False

    def test_registry_initialization_with_enabled(self) -> None:
        """Test registry initialization with enabled flag."""
        registry = LegacySkillRegistry(enabled=True)
        assert registry.enabled is True

    def test_load_skills_idempotent(self) -> None:
        """Test that load_skills is idempotent."""
        registry = LegacySkillRegistry(enabled=False)
        skills1 = registry.load_skills()
        skills2 = registry.load_skills()
        assert skills1 == skills2 == {}

    def test_global_registry_singleton(self) -> None:
        """Test that global registry is a singleton."""
        registry1 = get_legacy_registry()
        registry2 = get_legacy_registry()
        assert registry1 is registry2

    def test_set_legacy_enabled_global(self) -> None:
        """Test setting legacy enabled globally."""
        registry = get_legacy_registry()
        original_state = registry.enabled

        try:
            set_legacy_enabled(True)
            assert get_legacy_registry().enabled is True

            set_legacy_enabled(False)
            assert get_legacy_registry().enabled is False
        finally:
            # Restore original state
            if original_state:
                set_legacy_enabled(True)
            else:
                set_legacy_enabled(False)

    def test_registry_isolation_prevents_auto_load(self) -> None:
        """Test that registry isolation prevents auto-loading."""
        registry = LegacySkillRegistry(enabled=False)
        # Even if we try to get a skill, it shouldn't load
        skill = registry.get_skill("web_search_skill")
        assert skill is None
        assert registry._loaded is False


class TestLegacySkillIsolationBehavior:
    """Test isolation behavior of legacy skills."""

    def test_disabled_registry_no_side_effects(self) -> None:
        """Test that disabled registry has no side effects."""
        registry = LegacySkillRegistry(enabled=False)
        # Multiple operations should not cause any imports
        registry.load_skills()
        registry.get_skill("web_search_skill")
        registry.is_available("web_search_skill")
        # If we got here without import errors, isolation is working
        assert registry._skills == {}

    def test_registry_state_isolation(self) -> None:
        """Test that registry state is properly isolated."""
        registry1 = LegacySkillRegistry(enabled=False)
        registry2 = LegacySkillRegistry(enabled=True)

        # They should have independent state
        assert registry1.enabled is False
        assert registry2.enabled is True

    def test_feature_flag_controls_loading(self) -> None:
        """Test that feature flag controls skill loading."""
        registry = LegacySkillRegistry(enabled=False)
        assert registry.load_skills() == {}

        registry.enable()
        # Even after enabling, if imports fail, should handle gracefully
        skills = registry.load_skills()
        # Should either have skills or empty dict (if imports fail)
        assert isinstance(skills, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
