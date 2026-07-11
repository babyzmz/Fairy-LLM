from __future__ import annotations

from pathlib import Path

from fairy_core.commanding.policy import PermissionProfile
from fairy_core.commanding.registry import build_default_registry
from fairy_core.skills.loader import SkillPackageLoader
from fairy_core.skills.registry import SkillRegistry
from tests.skills.support import write_skill


def test_skill_registry_materializes_non_executing_tool_with_capability_dependencies(
    tmp_path: Path,
) -> None:
    registry = build_default_registry()
    skills = SkillRegistry(registry)
    package = SkillPackageLoader().load(
        write_skill(
            tmp_path,
            required_capabilities=("project.read", "web.search"),
        )
    )

    skills.install(package)

    definition = registry.get("skill.release-notes")
    assert definition is not None
    assert definition.executor == "skill_instructions"
    assert definition.idempotent is True
    assert definition.source == "skill"
    assert definition.required_operations == frozenset({"project.read", "web.search"})
    enabled = registry.capability_manifest(
        profile=PermissionProfile.STANDARD,
        sandbox_healthy=True,
    )
    disabled = registry.capability_manifest(
        profile=PermissionProfile.STANDARD,
        sandbox_healthy=True,
        overrides={"web.search": False},
    )
    assert enabled[definition.name] is True
    assert disabled[definition.name] is False


def test_skill_requiring_untrusted_mcp_server_remains_unavailable(tmp_path: Path) -> None:
    registry = build_default_registry()
    skills = SkillRegistry(registry)
    package = SkillPackageLoader().load(
        write_skill(tmp_path, compatible_mcp_servers=("issue-tracker",))
    )
    skills.install(package)

    unavailable = registry.capability_manifest(
        profile=PermissionProfile.STANDARD,
        sandbox_healthy=True,
    )
    registry.set_extension_ready("mcp", "issue-tracker", ready=True)
    available = registry.capability_manifest(
        profile=PermissionProfile.STANDARD,
        sandbox_healthy=True,
    )

    assert unavailable["skill.release-notes"] is False
    assert available["skill.release-notes"] is True
