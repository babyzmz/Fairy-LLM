from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from fairy_core.commanding.registry import ApprovalPolicy, RiskLevel, SideEffect
from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.common import ContractModel
from fairy_core.mcp.models import McpServerStatus, McpTransport


class SkillProvenanceModel(ContractModel):
    publisher: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=200)


class SkillModel(ContractModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    version: str = Field(min_length=5, max_length=128)
    description: str = Field(min_length=1, max_length=1_024)
    tool_name: str = Field(pattern=r"^skill\.[a-z0-9][a-z0-9-]{0,63}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_capabilities: tuple[str, ...]
    compatible_mcp_servers: tuple[str, ...]
    provenance: SkillProvenanceModel
    enabled: bool
    available: bool


class SkillPageModel(ContractModel):
    items: tuple[SkillModel, ...]


class ExtensionCatalogEntryModel(ContractModel):
    extension_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    kind: str = Field(pattern=r"^(skill|mcp_preset)$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1_024)
    publisher: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=200)
    experimental: bool
    installed: bool
    source_kind: str = Field(pattern=r"^(curated|git|archive|local|created)$")
    trust: str = Field(pattern=r"^(verified_publisher|curated|external|local)$")
    tags: tuple[str, ...] = Field(default=(), max_length=16)
    requirements: tuple[str, ...] = Field(default=(), max_length=16)


class ExtensionCatalogPageModel(ContractModel):
    items: tuple[ExtensionCatalogEntryModel, ...]


class SkillInstallInput(ContractModel):
    catalog_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    idempotency_key: str = Field(min_length=1, max_length=512)


class SkillImportInspectInput(ContractModel):
    source_kind: str = Field(pattern=r"^(folder|zip|github)$")
    source: str = Field(min_length=1, max_length=4_096)

    @field_validator("source")
    @classmethod
    def _safe_source(cls, value: str) -> str:
        if "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError("Skill import source is invalid")
        return value


class SkillImportInspectionModel(ContractModel):
    inspection_token: str = Field(pattern=r"^[A-Za-z0-9_-]{24,128}$")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    description: str = Field(min_length=1, max_length=1_024)
    version: str | None = Field(default=None, min_length=5, max_length=128)
    publisher: str | None = Field(default=None, min_length=1, max_length=200)
    license: str | None = Field(default=None, min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=500)
    has_manifest: bool
    file_count: int = Field(ge=1, le=64)
    content_bytes: int = Field(ge=1, le=524_288)


class SkillImportInstallInput(ContractModel):
    inspection_token: str = Field(pattern=r"^[A-Za-z0-9_-]{24,128}$")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    version: str = Field(min_length=5, max_length=128)
    description: str = Field(min_length=1, max_length=1_024)
    publisher: str = Field(min_length=1, max_length=200)
    license: str = Field(min_length=1, max_length=200)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: tuple[str, ...] = Field(default=(), max_length=32)
    compatible_mcp_servers: tuple[str, ...] = Field(default=(), max_length=32)
    idempotency_key: str = Field(min_length=1, max_length=512)


class SkillCreateInput(ContractModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    version: str = Field(min_length=5, max_length=128)
    description: str = Field(min_length=1, max_length=1_024)
    instructions: str = Field(min_length=1, max_length=131_072)
    publisher: str = Field(min_length=1, max_length=200)
    license: str = Field(min_length=1, max_length=200)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: tuple[str, ...] = Field(default=(), max_length=32)
    compatible_mcp_servers: tuple[str, ...] = Field(default=(), max_length=32)
    idempotency_key: str = Field(min_length=1, max_length=512)


class SkillUpdateInput(ContractModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    expected_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=512)


class SkillSetEnabledInput(ContractModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    expected_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    enabled: bool
    idempotency_key: str = Field(min_length=1, max_length=512)


class SkillRemoveInput(ContractModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    expected_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=512)


class SkillRemoveResult(ContractModel):
    name: str
    removed: bool


class McpToolDescriptorModel(ContractModel):
    name: str = Field(min_length=1, max_length=128)
    imported_name: str | None = Field(default=None, min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4_096)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class McpToolPolicyInput(ContractModel):
    name: str = Field(min_length=1, max_length=128)
    enabled: bool
    side_effect: SideEffect
    risk_level: RiskLevel
    approval_policy: ApprovalPolicy
    profiles: tuple[PermissionProfile, ...] = Field(min_length=1, max_length=3)
    idempotent: bool

    @field_validator("profiles")
    @classmethod
    def _unique_profiles(
        cls,
        value: tuple[PermissionProfile, ...],
    ) -> tuple[PermissionProfile, ...]:
        if len(value) != len(set(value)):
            raise ValueError("MCP policy profiles must be unique")
        return value


class McpServerModel(ContractModel):
    server_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=200)
    transport: McpTransport
    command: str | None = Field(default=None, min_length=1, max_length=4_096)
    arguments: tuple[str, ...] = Field(default=(), max_length=64)
    endpoint: str | None = Field(default=None, min_length=1, max_length=4_096)
    credential_configured: bool
    environment_names: tuple[str, ...] = Field(default=(), max_length=32)
    enabled: bool
    status: McpServerStatus
    accepted_schema_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pending_schema_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    accepted_tools: tuple[McpToolDescriptorModel, ...]
    pending_tools: tuple[McpToolDescriptorModel, ...]
    policies: tuple[McpToolPolicyInput, ...]
    revision: int = Field(ge=1)
    last_error_code: str | None = Field(default=None, min_length=1, max_length=128)
    created_at: str
    updated_at: str


class McpServerPageModel(ContractModel):
    items: tuple[McpServerModel, ...]


class McpServerConfigureInput(ContractModel):
    server_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=200)
    transport: McpTransport
    command: str | None = Field(default=None, min_length=1, max_length=4_096)
    arguments: tuple[str, ...] = Field(default=(), max_length=64)
    endpoint: str | None = Field(default=None, min_length=1, max_length=4_096)
    credential_ref: str | None = Field(default=None, min_length=3, max_length=288)
    environment_refs: dict[str, str] = Field(default_factory=dict, max_length=32)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def _transport_fields(self) -> McpServerConfigureInput:
        if self.transport is McpTransport.STDIO:
            if self.command is None or self.endpoint is not None:
                raise ValueError("stdio requires command and forbids endpoint")
        elif self.command is not None or self.arguments or self.environment_refs:
            raise ValueError("Streamable HTTP forbids stdio process fields")
        elif self.endpoint is None:
            raise ValueError("Streamable HTTP requires endpoint")
        return self


class McpServerIdInput(ContractModel):
    server_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")


class McpServerDiscoverInput(McpServerIdInput):
    task_id: UUID
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=512)


class McpServerAcceptInput(McpServerIdInput):
    expected_revision: int = Field(ge=1)
    schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    enabled: bool
    tools: tuple[McpToolPolicyInput, ...] = Field(max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=512)


class McpServerSetEnabledInput(McpServerIdInput):
    expected_revision: int = Field(ge=1)
    enabled: bool
    idempotency_key: str = Field(min_length=1, max_length=512)


class McpServerDeleteInput(McpServerIdInput):
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=512)


class McpServerDeleteResult(ContractModel):
    server_id: str
    deleted: bool


class McpPresetInstallInput(ContractModel):
    catalog_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    credential_ref: str | None = Field(default=None, min_length=3, max_length=288)
    expected_revision: int = Field(default=0, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=512)


__all__ = [
    "ExtensionCatalogEntryModel",
    "ExtensionCatalogPageModel",
    "McpPresetInstallInput",
    "McpServerAcceptInput",
    "McpServerConfigureInput",
    "McpServerDeleteInput",
    "McpServerDeleteResult",
    "McpServerDiscoverInput",
    "McpServerModel",
    "McpServerPageModel",
    "McpServerSetEnabledInput",
    "McpToolDescriptorModel",
    "McpToolPolicyInput",
    "SkillCreateInput",
    "SkillImportInspectInput",
    "SkillImportInspectionModel",
    "SkillImportInstallInput",
    "SkillInstallInput",
    "SkillModel",
    "SkillPageModel",
    "SkillProvenanceModel",
    "SkillRemoveInput",
    "SkillRemoveResult",
    "SkillSetEnabledInput",
    "SkillUpdateInput",
]
