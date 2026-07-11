from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from fairy_core.contracts.common import ContractModel, PermissionProfileModel


class ToolDefinitionMetadataModel(ContractModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    side_effect: Literal["none", "read", "write", "execute"]
    risk_level: Literal["low", "medium", "high"]
    approval_policy: Literal["never", "profile", "always"]
    profiles: tuple[PermissionProfileModel, ...] = Field(min_length=1, max_length=3)
    requires_sandbox: bool
    idempotent: bool
    model_visible: bool
    description: str = Field(min_length=1, max_length=1_000)
    source: Literal["builtin", "skill", "mcp"] = "builtin"
    origin_id: str | None = Field(default=None, min_length=1, max_length=64)
    required_operations: tuple[str, ...] = ()
    required_extensions: tuple[str, ...] = ()
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_schema: dict[str, Any]


class SlashCommandMetadataModel(ContractModel):
    name: Literal["new", "project", "permission", "stop", "clear", "help"]
    description: str = Field(min_length=1, max_length=500)
    argument_hint: str | None = Field(default=None, min_length=1, max_length=100)
    required_operation: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    available: bool


class CapabilityManifestModel(ContractModel):
    profile: PermissionProfileModel
    operations: dict[str, bool]
    sandbox_healthy: bool
    command_metadata: tuple[ToolDefinitionMetadataModel, ...] = ()
    slash_commands: tuple[SlashCommandMetadataModel, ...] = ()
    schema_version: Literal[3] = 3


__all__ = [
    "CapabilityManifestModel",
    "SlashCommandMetadataModel",
    "ToolDefinitionMetadataModel",
]
