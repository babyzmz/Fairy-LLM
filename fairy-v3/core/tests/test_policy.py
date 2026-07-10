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
    assert manifest["public_research.search"] is True
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
        tool_name="public_research.search",
        profile=PermissionProfile.AUTONOMOUS,
        capability_overrides={"public_research.search": False},
        approval_granted=False,
        sandbox_healthy=True,
    )

    assert decision.allowed is False
    assert decision.error_code == "CAPABILITY_NOT_AVAILABLE"
