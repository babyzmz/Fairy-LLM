from __future__ import annotations

import pytest

from fairy_core.commanding.policy import PermissionProfile, PolicyEngine
from fairy_core.commanding.registry import (
    ApprovalPolicy,
    RiskLevel,
    SideEffect,
    ToolDefinition,
    ToolRegistry,
    build_default_registry,
)


def test_tool_registry_is_single_source_for_capability_manifest() -> None:
    registry = build_default_registry()

    manifest = registry.capability_manifest(
        profile=PermissionProfile.OBSERVE,
        sandbox_healthy=False,
    )

    assert manifest["project.read"] is True
    assert manifest["web.search"] is True
    assert manifest["info.weather"] is True
    assert manifest["info.time"] is True
    assert manifest["info.crypto"] is True
    assert manifest["edit.apply_changeset"] is False
    assert manifest["run.sandboxed"] is False
    assert "run.host" not in manifest


def test_registry_rejects_duplicate_tool_names() -> None:
    registry = ToolRegistry()
    tool = ToolDefinition(
        name="project.read",
        side_effect=SideEffect.READ,
        risk_level=RiskLevel.LOW,
        approval_policy=ApprovalPolicy.NEVER,
        profiles=frozenset({PermissionProfile.OBSERVE}),
        executor="project_reader",
    )
    registry.register(tool)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(tool)


def test_tool_definition_rejects_permissive_argument_schemas() -> None:
    with pytest.raises(ValueError, match="reject additional properties"):
        ToolDefinition(
            name="unsafe.open_schema",
            side_effect=SideEffect.READ,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=frozenset({PermissionProfile.OBSERVE}),
            executor="unsafe",
            input_schema={"type": "object", "additionalProperties": True},
        )


def test_standard_profile_requires_approval_for_changeset_apply() -> None:
    registry = build_default_registry()
    policy = PolicyEngine(registry)

    pending = policy.evaluate(
        tool_name="edit.apply_changeset",
        profile=PermissionProfile.STANDARD,
        capability_overrides={},
        approval_granted=False,
        sandbox_healthy=False,
    )
    approved = policy.evaluate(
        tool_name="edit.apply_changeset",
        profile=PermissionProfile.STANDARD,
        capability_overrides={},
        approval_granted=True,
        sandbox_healthy=False,
    )

    assert pending.allowed is False
    assert pending.requires_approval is True
    assert pending.error_code == "APPROVAL_REQUIRED"
    assert approved.allowed is True


def test_autonomous_profile_exposes_shell_only_with_healthy_sandbox() -> None:
    registry = build_default_registry()
    policy = PolicyEngine(registry)

    unavailable = policy.evaluate(
        tool_name="run.sandboxed",
        profile=PermissionProfile.AUTONOMOUS,
        capability_overrides={"run.sandboxed": True},
        approval_granted=False,
        sandbox_healthy=False,
    )
    available = policy.evaluate(
        tool_name="run.sandboxed",
        profile=PermissionProfile.AUTONOMOUS,
        capability_overrides={"run.sandboxed": True},
        approval_granted=False,
        sandbox_healthy=True,
    )

    assert unavailable.allowed is False
    assert unavailable.error_code == "SANDBOX_UNAVAILABLE"
    assert available.allowed is True
    assert available.requires_approval is False


def test_static_preview_approval_is_independent_from_wsl_health() -> None:
    registry = build_default_registry()
    policy = PolicyEngine(registry)

    pending = policy.evaluate(
        tool_name="preview.start",
        profile=PermissionProfile.STANDARD,
        capability_overrides={},
        approval_granted=False,
        sandbox_healthy=False,
    )
    approved = policy.evaluate(
        tool_name="preview.start",
        profile=PermissionProfile.STANDARD,
        capability_overrides={},
        approval_granted=True,
        sandbox_healthy=False,
    )
    manifest = registry.capability_manifest(
        profile=PermissionProfile.STANDARD,
        sandbox_healthy=False,
    )
    metadata = {item["name"]: item for item in registry.frontend_metadata()}

    assert pending.error_code == "APPROVAL_REQUIRED"
    assert approved.allowed is True
    assert manifest["preview.start"] is True
    assert manifest["run.sandboxed"] is False
    assert metadata["preview.start"]["requires_sandbox"] is False
    assert metadata["preview.start"]["idempotent"] is True


def test_dependency_and_executable_reviews_require_current_sandbox_health() -> None:
    registry = build_default_registry()
    unavailable = registry.capability_manifest(
        profile=PermissionProfile.STANDARD,
        sandbox_healthy=False,
    )
    available = registry.capability_manifest(
        profile=PermissionProfile.STANDARD,
        sandbox_healthy=True,
    )
    metadata = {item["name"]: item for item in registry.frontend_metadata()}

    for name in (
        "deps.install",
        "review.typecheck",
        "review.lint",
        "review.test",
        "review.build",
    ):
        assert unavailable[name] is False
        assert available[name] is True
        assert metadata[name]["requires_sandbox"] is True
    assert unavailable["preview.start"] is True
    assert unavailable["review.health"] is True


@pytest.mark.parametrize("profile", list(PermissionProfile))
def test_active_version_promotion_always_requires_approval(profile: PermissionProfile) -> None:
    policy = PolicyEngine(build_default_registry())

    decision = policy.evaluate(
        tool_name="project.accept_version",
        profile=profile,
        capability_overrides={"project.accept_version": True},
        approval_granted=False,
        sandbox_healthy=True,
    )

    assert decision.allowed is False
    assert decision.requires_approval is True
    assert decision.error_code == "APPROVAL_REQUIRED"


def test_explicit_disable_override_removes_capability() -> None:
    policy = PolicyEngine(build_default_registry())

    decision = policy.evaluate(
        tool_name="web.search",
        profile=PermissionProfile.AUTONOMOUS,
        capability_overrides={"web.search": False},
        approval_granted=False,
        sandbox_healthy=True,
    )

    assert decision.allowed is False
    assert decision.error_code == "CAPABILITY_NOT_AVAILABLE"


def test_default_registry_does_not_keep_pre_v3_research_alias() -> None:
    assert build_default_registry().get("public_research.search") is None


def test_internal_workspace_commands_share_registry_but_are_not_model_tools() -> None:
    registry = build_default_registry()

    standard = registry.capability_manifest(
        profile=PermissionProfile.STANDARD,
        sandbox_healthy=False,
    )
    observe = registry.capability_manifest(
        profile=PermissionProfile.OBSERVE,
        sandbox_healthy=False,
    )
    agent_names = {
        definition.name for definition in registry.definitions() if definition.model_visible
    }
    metadata = {item["name"]: item for item in registry.frontend_metadata()}

    assert standard["workspace.fork"] is True
    assert observe["workspace.fork"] is False
    assert "workspace.fork" not in agent_names
    assert metadata["workspace.fork"]["side_effect"] == "write"
    assert metadata["workspace.fork"]["model_visible"] is False


def test_project_execution_tools_use_closed_argument_schemas() -> None:
    registry = build_default_registry()

    for name in (
        "deps.install",
        "review.typecheck",
        "review.lint",
        "review.test",
        "review.build",
    ):
        definition = registry.get(name)
        assert definition is not None
        assert dict(definition.input_schema) == {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }


def test_every_registered_tool_has_a_closed_root_schema() -> None:
    registry = build_default_registry()

    assert all(
        definition.input_schema.get("additionalProperties") is False
        for definition in registry.definitions()
    )


def test_core_and_user_only_execution_commands_are_not_agent_tools() -> None:
    registry = build_default_registry()
    agent_names = {
        definition.name for definition in registry.definitions() if definition.model_visible
    }

    assert {
        "deps.install",
        "review.typecheck",
        "review.lint",
        "review.test",
        "review.build",
        "run.sandboxed",
    } <= agent_names
    assert {
        "edit.apply_changeset",
        "preview.start",
        "preview.stop",
        "review.health",
        "review.browser",
        "project.accept_version",
    }.isdisjoint(agent_names)
