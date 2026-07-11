from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from fairy_core.commanding.types import PermissionProfile


class SideEffect(StrEnum):
    NONE = "none"
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalPolicy(StrEnum):
    NEVER = "never"
    PROFILE = "profile"
    ALWAYS = "always"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    side_effect: SideEffect
    risk_level: RiskLevel
    approval_policy: ApprovalPolicy
    profiles: frozenset[PermissionProfile]
    executor: str
    requires_sandbox: bool = False
    idempotent: bool = False
    model_visible: bool = True
    description: str = ""
    input_schema: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({"type": "object", "additionalProperties": True})
    )

    def __post_init__(self) -> None:
        description = self.description.strip() or self.name.replace(".", " ")
        schema = dict(self.input_schema)
        if schema.get("type") != "object":
            raise ValueError("tool input_schema must describe an object")
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "input_schema", MappingProxyType(schema))


class ToolRegistry:
    def __init__(self, definitions: Iterable[ToolDefinition] = ()) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions.values())

    def agent_definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            definition for definition in self._definitions.values() if definition.model_visible
        )

    def frontend_metadata(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "name": definition.name,
                "side_effect": definition.side_effect.value,
                "risk_level": definition.risk_level.value,
                "approval_policy": definition.approval_policy.value,
                "requires_sandbox": definition.requires_sandbox,
                "idempotent": definition.idempotent,
                "model_visible": definition.model_visible,
                "description": definition.description,
                "input_schema": dict(definition.input_schema),
            }
            for definition in self._definitions.values()
        )

    def capability_manifest(
        self,
        *,
        profile: PermissionProfile,
        sandbox_healthy: bool,
        overrides: dict[str, bool] | None = None,
    ) -> dict[str, bool]:
        effective_overrides = overrides or {}
        return {
            definition.name: (
                profile in definition.profiles
                and effective_overrides.get(definition.name, True)
                and (not definition.requires_sandbox or sandbox_healthy)
            )
            for definition in self._definitions.values()
        }


def _tool(
    name: str,
    effect: SideEffect,
    risk: RiskLevel,
    approval: ApprovalPolicy,
    profiles: frozenset[PermissionProfile],
    executor: str,
    *,
    sandbox: bool = False,
    idempotent: bool = False,
    model_visible: bool = True,
    description: str = "",
    input_schema: Mapping[str, object] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        side_effect=effect,
        risk_level=risk,
        approval_policy=approval,
        profiles=profiles,
        executor=executor,
        requires_sandbox=sandbox,
        idempotent=idempotent,
        model_visible=model_visible,
        description=description,
        input_schema=(
            input_schema
            if input_schema is not None
            else {"type": "object", "additionalProperties": True}
        ),
    )


def _information_definitions(
    profiles: frozenset[PermissionProfile],
) -> list[ToolDefinition]:
    shared = {
        "effect": SideEffect.READ,
        "risk": RiskLevel.LOW,
        "approval": ApprovalPolicy.NEVER,
        "profiles": profiles,
        "executor": "information_tools",
    }
    return [
        _tool(
            "info.weather",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Get current weather for an explicitly resolved location.",
            input_schema={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "minLength": 2, "maxLength": 500},
                    "country_code": {"type": "string", "minLength": 2, "maxLength": 2},
                    "candidate_index": {"type": "integer", "minimum": 1, "maximum": 5},
                    "units": {"type": "string", "enum": ["metric", "imperial"]},
                },
                "required": ["location"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.news",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Search current news sources with publication provenance.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 2_000},
                    "count": {"type": "integer", "minimum": 1, "maximum": 10},
                    "freshness": {"type": "string", "maxLength": 64},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.time",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Convert the current instant to an IANA time zone.",
            input_schema={
                "type": "object",
                "properties": {"timezone": {"type": "string", "minLength": 3, "maxLength": 255}},
                "required": ["timezone"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.map",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Generate an encoded OpenStreetMap search link.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 1_000}},
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.stock",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Get a delayed or last-close stock quote.",
            input_schema={
                "type": "object",
                "properties": {"symbol": {"type": "string", "minLength": 1, "maxLength": 16}},
                "required": ["symbol"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.fx",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Convert currencies using a dated central-bank reference rate.",
            input_schema={
                "type": "object",
                "properties": {
                    "base": {"type": "string", "minLength": 3, "maxLength": 3},
                    "quote": {"type": "string", "minLength": 3, "maxLength": 3},
                    "amount": {"type": "number", "minimum": 0, "maximum": 1e15},
                },
                "required": ["base", "quote"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "info.crypto",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Get a dated cryptocurrency close in the requested market currency.",
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "minLength": 1, "maxLength": 16},
                    "market_currency": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 3,
                    },
                },
                "required": ["symbol", "market_currency"],
                "additionalProperties": False,
            },
        ),
    ]


def _system_action_definitions(
    profiles: frozenset[PermissionProfile],
) -> list[ToolDefinition]:
    shared = {
        "effect": SideEffect.EXECUTE,
        "risk": RiskLevel.MEDIUM,
        "approval": ApprovalPolicy.PROFILE,
        "profiles": profiles,
        "executor": "rust_system_actions",
    }
    return [
        _tool(
            "system.open_url",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Open one validated HTTPS URL with the operating system.",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "minLength": 1, "maxLength": 2_048}},
                "required": ["url"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "system.reveal_path",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Reveal one existing path inside the Task-bound managed Version.",
            input_schema={
                "type": "object",
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1_024,
                    }
                },
                "required": ["relative_path"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "system.copy_text",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Copy bounded text to the operating-system clipboard.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 32_768}},
                "required": ["text"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "system.notify",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Show a bounded informational or warning notification.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 80},
                    "body": {"type": "string", "minLength": 1, "maxLength": 240},
                    "level": {"type": "string", "enum": ["info", "warning"]},
                },
                "required": ["title", "body"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "system.open_settings",
            shared["effect"],
            shared["risk"],
            shared["approval"],
            shared["profiles"],
            shared["executor"],
            idempotent=True,
            description="Open one fixed operating-system Settings page.",
            input_schema={
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "enum": ["display", "microphone", "notifications", "sound"],
                    }
                },
                "required": ["page"],
                "additionalProperties": False,
            },
        ),
    ]


def build_default_registry() -> ToolRegistry:
    all_profiles = frozenset(PermissionProfile)
    active_profiles = frozenset({PermissionProfile.STANDARD, PermissionProfile.AUTONOMOUS})
    autonomous = frozenset({PermissionProfile.AUTONOMOUS})
    definitions = [
        _tool(
            "model.generate",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "model_provider",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "workspace.create_empty",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "rust_workspace_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "workspace.import",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "rust_workspace_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "workspace.fork",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "rust_workspace_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "workspace.create_scratch",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "rust_workspace_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "workspace.write_text",
            SideEffect.WRITE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.NEVER,
            active_profiles,
            "rust_workspace_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "workspace.diff",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "rust_workspace_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "workspace.checkpoint",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "rust_workspace_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "workspace.discard",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "rust_workspace_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "project.read",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "project_reader",
            idempotent=True,
        ),
        _tool(
            "artifact.list",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "artifact_store",
            idempotent=True,
        ),
        _tool(
            "artifact.read",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "artifact_store",
            idempotent=True,
        ),
        _tool(
            "web.search",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "web_search",
            idempotent=True,
            description="Search public web or news sources.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 2_000},
                    "kind": {"type": "string", "enum": ["web", "news"]},
                    "count": {"type": "integer", "minimum": 1, "maximum": 20},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 200},
                    "freshness": {"type": "string", "maxLength": 64},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "web.fetch",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "web_fetch",
            idempotent=True,
            description="Fetch bounded text from one authorized public URL.",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "minLength": 1, "maxLength": 2_048}},
                "required": ["url"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "research.build",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "research_application",
            idempotent=True,
            description="Build a cited Evidence Artifact from authorized public URLs.",
            input_schema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["web_brief", "specs", "compare", "release"],
                    },
                    "question": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 10_000,
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 2_048},
                        "minItems": 1,
                        "maxItems": 10,
                    },
                },
                "required": ["kind", "question", "sources"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "documents.import",
            SideEffect.WRITE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.ALWAYS,
            active_profiles,
            "document_application",
            idempotent=True,
            model_visible=False,
            description="Import an explicitly approved file into managed document storage.",
        ),
        _tool(
            "documents.delete",
            SideEffect.WRITE,
            RiskLevel.HIGH,
            ApprovalPolicy.ALWAYS,
            active_profiles,
            "document_application",
            idempotent=True,
            model_visible=False,
            description="Delete an explicitly approved managed document projection.",
        ),
        _tool(
            "documents.list",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "document_application",
            idempotent=True,
            description="List managed documents visible to the current Task Scope.",
            input_schema={"type": "object", "additionalProperties": False},
        ),
        _tool(
            "documents.get",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "document_application",
            idempotent=True,
            description="Read immutable metadata for one managed document.",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "format": "uuid",
                    }
                },
                "required": ["document_id"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "documents.search",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "document_application",
            idempotent=True,
            description="Search managed document chunks with immutable source provenance.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 10_000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        *_system_action_definitions(active_profiles),
        *_information_definitions(all_profiles),
        _tool(
            "memory.observe",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "memory_application",
            idempotent=True,
        ),
        _tool(
            "memory.claim.promote",
            SideEffect.WRITE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.ALWAYS,
            active_profiles,
            "memory_application",
            idempotent=True,
        ),
        _tool(
            "memory.claim.supersede",
            SideEffect.WRITE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.ALWAYS,
            active_profiles,
            "memory_application",
            idempotent=True,
        ),
        _tool(
            "memory.claim.resolve_conflict",
            SideEffect.WRITE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.ALWAYS,
            active_profiles,
            "memory_application",
            idempotent=True,
        ),
        _tool(
            "memory.forget",
            SideEffect.WRITE,
            RiskLevel.HIGH,
            ApprovalPolicy.ALWAYS,
            active_profiles,
            "memory_application",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "memory.projection.refresh",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "memory_projection_refresher",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "memory.snapshot.build",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "memory_snapshot_builder",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "preview.status",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "preview_worker",
            idempotent=True,
        ),
        _tool(
            "edit.propose_changeset",
            SideEffect.NONE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "changeset_orchestrator",
            idempotent=True,
        ),
        _tool(
            "edit.apply_changeset",
            SideEffect.WRITE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.PROFILE,
            active_profiles,
            "changeset_worker",
        ),
        _tool(
            "deps.install",
            SideEffect.EXECUTE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.PROFILE,
            active_profiles,
            "dependency_worker",
        ),
        _tool(
            "preview.start",
            SideEffect.EXECUTE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.PROFILE,
            active_profiles,
            "preview_worker",
            idempotent=True,
        ),
        _tool(
            "preview.stop",
            SideEffect.EXECUTE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.PROFILE,
            active_profiles,
            "preview_worker",
            idempotent=True,
        ),
        _tool(
            "review.typecheck",
            SideEffect.EXECUTE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            idempotent=True,
        ),
        _tool(
            "review.lint",
            SideEffect.EXECUTE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            idempotent=True,
        ),
        _tool(
            "review.test",
            SideEffect.EXECUTE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            idempotent=True,
        ),
        _tool(
            "review.build",
            SideEffect.EXECUTE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            idempotent=True,
        ),
        _tool(
            "review.health",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            idempotent=True,
        ),
        _tool(
            "review.browser",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            idempotent=True,
        ),
        _tool(
            "run.sandboxed",
            SideEffect.EXECUTE,
            RiskLevel.HIGH,
            ApprovalPolicy.NEVER,
            autonomous,
            "sandbox_worker",
            sandbox=True,
        ),
        _tool(
            "project.accept_version",
            SideEffect.WRITE,
            RiskLevel.HIGH,
            ApprovalPolicy.ALWAYS,
            all_profiles,
            "version_manager",
        ),
    ]
    return ToolRegistry(definitions)
