from __future__ import annotations

from dataclasses import dataclass

from fairy_core.commanding.registry import ApprovalPolicy, ToolRegistry
from fairy_core.commanding.types import PermissionProfile

__all__ = ["PermissionProfile", "PolicyDecision", "PolicyEngine"]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool = False
    error_code: str | None = None
    reason: str = ""


class PolicyEngine:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def evaluate(
        self,
        *,
        tool_name: str,
        profile: PermissionProfile,
        capability_overrides: dict[str, bool],
        approval_granted: bool,
        sandbox_healthy: bool,
    ) -> PolicyDecision:
        definition = self._registry.get(tool_name)
        if definition is None or profile not in definition.profiles:
            return PolicyDecision(
                False, error_code="CAPABILITY_NOT_AVAILABLE", reason="tool not exposed"
            )
        if capability_overrides.get(tool_name) is False:
            return PolicyDecision(
                False, error_code="CAPABILITY_NOT_AVAILABLE", reason="tool disabled"
            )
        if definition.requires_sandbox and not sandbox_healthy:
            return PolicyDecision(
                False, error_code="SANDBOX_UNAVAILABLE", reason="sandbox is not healthy"
            )

        requires_approval = definition.approval_policy is ApprovalPolicy.ALWAYS or (
            definition.approval_policy is ApprovalPolicy.PROFILE
            and profile is PermissionProfile.STANDARD
        )
        if requires_approval and not approval_granted:
            return PolicyDecision(
                False,
                requires_approval=True,
                error_code="APPROVAL_REQUIRED",
                reason="explicit approval required",
            )
        return PolicyDecision(True)
