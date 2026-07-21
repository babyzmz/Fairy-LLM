from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from types import MappingProxyType

from fairy_core.commanding import registry_projection
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


_TOOL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_EXTENSION_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}:[a-z0-9][a-z0-9_-]{0,63}$")
_CLOSED_INPUT_SCHEMA = MappingProxyType({"type": "object", "additionalProperties": False})


@dataclass(frozen=True, slots=True)
class SlashCommandDefinition:
    name: str
    description: str
    argument_hint: str | None = None
    required_operation: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.isascii() or not self.name.islower():
            raise ValueError("slash command names must be lowercase ASCII")
        if not self.description.strip():
            raise ValueError("slash command descriptions cannot be blank")


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
    source: str = "builtin"
    origin_id: str | None = None
    required_operations: frozenset[str] = frozenset()
    required_extensions: frozenset[str] = frozenset()
    input_schema: Mapping[str, object] = field(default_factory=lambda: _CLOSED_INPUT_SCHEMA)
    definition_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if _TOOL_NAME.fullmatch(self.name) is None:
            raise ValueError("tool name must be lowercase ASCII and namespace-safe")
        description = registry_projection.bounded_public_description(
            self.description.strip() or self.name.replace(".", " ")
        )
        try:
            encoded_schema = json.dumps(
                dict(self.input_schema),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            schema = json.loads(encoded_schema)
        except (TypeError, ValueError) as error:
            raise ValueError("tool input_schema must contain canonical JSON") from error
        if schema.get("type") != "object":
            raise ValueError("tool input_schema must describe an object")
        if schema.get("additionalProperties") is not False:
            raise ValueError("tool input_schema must reject additional properties")
        source = self.source.strip().lower()
        if source not in {"builtin", "skill", "mcp"}:
            raise ValueError("tool source must be builtin, skill, or mcp")
        if source == "builtin" and self.origin_id is not None:
            raise ValueError("builtin tools cannot declare an extension origin")
        if source != "builtin" and not (self.origin_id or "").strip():
            raise ValueError("extension tools require an origin_id")
        if self.name in self.required_operations:
            raise ValueError("tool cannot depend on itself")
        for dependency in self.required_operations:
            if _TOOL_NAME.fullmatch(dependency) is None:
                raise ValueError("required operation name is invalid")
        for extension in self.required_extensions:
            if _EXTENSION_KEY.fullmatch(extension) is None:
                raise ValueError("required extension key is invalid")
        digest_payload = {
            "name": self.name,
            "side_effect": self.side_effect.value,
            "risk_level": self.risk_level.value,
            "approval_policy": self.approval_policy.value,
            "profiles": sorted(profile.value for profile in self.profiles),
            "executor": self.executor,
            "requires_sandbox": self.requires_sandbox,
            "idempotent": self.idempotent,
            "model_visible": self.model_visible,
            "description": description,
            "source": source,
            "origin_id": self.origin_id,
            "required_operations": sorted(self.required_operations),
            "required_extensions": sorted(self.required_extensions),
            "input_schema": schema,
        }
        digest = hashlib.sha256(
            json.dumps(
                digest_payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        ).hexdigest()
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "origin_id", self.origin_id.strip() if self.origin_id else None)
        object.__setattr__(self, "input_schema", MappingProxyType(schema))
        object.__setattr__(self, "definition_digest", digest)


class ToolRegistry:
    def __init__(
        self,
        definitions: Iterable[ToolDefinition] = (),
        *,
        slash_commands: Iterable[SlashCommandDefinition] = (),
    ) -> None:
        self._lock = RLock()
        self._definitions: dict[str, ToolDefinition] = {}
        self._slash_commands: dict[str, SlashCommandDefinition] = {}
        self._ready_extensions: set[str] = set()
        self._generation = 0
        for definition in definitions:
            self.register(definition)
        for command in slash_commands:
            if command.name in self._slash_commands:
                raise ValueError(f"slash command already registered: {command.name}")
            if (
                command.required_operation is not None
                and command.required_operation not in self._definitions
            ):
                raise ValueError(
                    f"slash command requires an unknown operation: {command.required_operation}"
                )
            self._slash_commands[command.name] = command

    def register(self, definition: ToolDefinition) -> None:
        with self._lock:
            if definition.name in self._definitions:
                raise ValueError(f"tool already registered: {definition.name}")
            self._definitions[definition.name] = definition
            self._generation += 1

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def replace_namespace(
        self,
        prefix: str,
        definitions: Iterable[ToolDefinition],
    ) -> None:
        if not prefix.endswith(".") or _TOOL_NAME.fullmatch(f"{prefix}x") is None:
            raise ValueError("tool namespace prefix is invalid")
        replacements = tuple(definitions)
        if any(not definition.name.startswith(prefix) for definition in replacements):
            raise ValueError("replacement tool is outside its namespace")
        names = [definition.name for definition in replacements]
        if len(names) != len(set(names)):
            raise ValueError("replacement namespace contains duplicate tools")
        with self._lock:
            replacement_map = {definition.name: definition for definition in replacements}
            current_namespace = {
                name: definition
                for name, definition in self._definitions.items()
                if name.startswith(prefix)
            }
            if current_namespace == replacement_map:
                return
            retained = {
                name: definition
                for name, definition in self._definitions.items()
                if not name.startswith(prefix)
            }
            collisions = set(retained).intersection(names)
            if collisions:
                raise ValueError(f"tool already registered: {sorted(collisions)[0]}")
            retained.update(replacement_map)
            self._definitions = retained
            self._generation += 1

    def set_extension_ready(self, kind: str, extension_id: str, *, ready: bool) -> None:
        key = f"{kind.strip().lower()}:{extension_id.strip().lower()}"
        if _EXTENSION_KEY.fullmatch(key) is None:
            raise ValueError("extension readiness key is invalid")
        with self._lock:
            before = key in self._ready_extensions
            if ready:
                self._ready_extensions.add(key)
            else:
                self._ready_extensions.discard(key)
            if before != ready:
                self._generation += 1

    def get(self, name: str) -> ToolDefinition | None:
        with self._lock:
            return self._definitions.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        with self._lock:
            return tuple(self._definitions.values())

    def available_agent_definitions(
        self,
        *,
        profile: PermissionProfile,
        sandbox_healthy: bool,
        overrides: dict[str, bool] | None = None,
    ) -> tuple[ToolDefinition, ...]:
        with self._lock:
            definitions = dict(self._definitions)
            ready_extensions = frozenset(self._ready_extensions)
        return registry_projection.available_agent_definitions(
            definitions,
            ready_extensions=ready_extensions,
            profile=profile,
            sandbox_healthy=sandbox_healthy,
            overrides=overrides,
        )

    def frontend_metadata(self) -> tuple[dict[str, object], ...]:
        return registry_projection.frontend_metadata(self.definitions())

    def slash_command_metadata(
        self,
        operations: Mapping[str, bool],
    ) -> tuple[dict[str, object], ...]:
        with self._lock:
            commands = tuple(self._slash_commands.values())
        return registry_projection.slash_command_metadata(commands, operations)

    def capability_manifest(
        self,
        *,
        profile: PermissionProfile,
        sandbox_healthy: bool,
        overrides: dict[str, bool] | None = None,
    ) -> dict[str, bool]:
        with self._lock:
            definitions = dict(self._definitions)
            ready_extensions = frozenset(self._ready_extensions)
        return registry_projection.capability_manifest(
            definitions,
            ready_extensions=ready_extensions,
            profile=profile,
            sandbox_healthy=sandbox_healthy,
            overrides=overrides,
        )


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
            else {"type": "object", "additionalProperties": False}
        ),
    )


def _information_definitions(
    profiles: frozenset[PermissionProfile],
) -> list[ToolDefinition]:
    from fairy_core.commanding.information_definitions import (
        build_information_definitions,
    )

    return build_information_definitions(profiles)


def build_default_registry() -> ToolRegistry:
    from fairy_core.commanding.browser_definitions import browser_definitions
    from fairy_core.commanding.media_definitions import media_generation_definitions
    from fairy_core.commanding.model_definitions import model_generation_definitions
    from fairy_core.commanding.project_definitions import project_definitions
    from fairy_core.commanding.slash_commands import default_slash_commands
    from fairy_core.commanding.system_action_definitions import system_action_definitions

    all_profiles = frozenset(PermissionProfile)
    active_profiles = frozenset({PermissionProfile.STANDARD, PermissionProfile.AUTONOMOUS})
    autonomous = frozenset({PermissionProfile.AUTONOMOUS})
    definitions = [
        *model_generation_definitions(all_profiles),
        *media_generation_definitions(active_profiles),
        *browser_definitions(active_profiles),
        _tool(
            "extensions.mcp.discover",
            SideEffect.READ,
            RiskLevel.MEDIUM,
            ApprovalPolicy.NEVER,
            active_profiles,
            "mcp_control",
            idempotent=True,
            model_visible=False,
            description="Discover bounded tool metadata from an explicitly configured MCP server.",
            input_schema={
                "type": "object",
                "properties": {
                    "server_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "connection_fingerprint": {
                        "type": "string",
                        "minLength": 64,
                        "maxLength": 64,
                    },
                },
                "required": ["server_id", "connection_fingerprint"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "workspace.create_empty",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
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
            all_profiles,
            "rust_workspace_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "workspace.create_scratch",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
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
        *project_definitions(all_profiles, active_profiles),
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
        *system_action_definitions(active_profiles),
        *_information_definitions(all_profiles),
        _tool(
            "memory.observe",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "memory_application",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "memory.search",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "memory_application",
            idempotent=True,
            description="Search the immutable Memory Snapshot bound to the current Task.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 10_000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "knowledge.search",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "knowledge_application",
            idempotent=True,
            description="Search the immutable Knowledge Snapshot bound to the current Task.",
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
        _tool(
            "knowledge.read",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "knowledge_application",
            idempotent=True,
            description=(
                "Read one immutable Knowledge Revision returned by task-bound knowledge search."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "revision_id": {"type": "string", "format": "uuid"},
                },
                "required": ["revision_id"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "knowledge.links",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "knowledge_application",
            idempotent=True,
            description="List links captured in one task-bound immutable Knowledge Revision.",
            input_schema={
                "type": "object",
                "properties": {
                    "revision_id": {"type": "string", "format": "uuid"},
                },
                "required": ["revision_id"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "memory.suggest",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "memory_application",
            idempotent=True,
            description=(
                "Suggest a bounded memory for explicit user review. This never creates a Claim."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "minLength": 1, "maxLength": 10_000},
                    "proposed_namespace": {
                        "type": "string",
                        "enum": [
                            "project_canonical",
                            "conversation_draft",
                            "task_episode",
                        ],
                    },
                },
                "required": ["content", "proposed_namespace"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "memory.proposal.accept",
            SideEffect.WRITE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.NEVER,
            active_profiles,
            "memory_application",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "memory.proposal.reject",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "memory_application",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "memory.claim.promote",
            SideEffect.WRITE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.ALWAYS,
            active_profiles,
            "memory_application",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "memory.claim.supersede",
            SideEffect.WRITE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.ALWAYS,
            active_profiles,
            "memory_application",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "memory.claim.resolve_conflict",
            SideEffect.WRITE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.ALWAYS,
            active_profiles,
            "memory_application",
            idempotent=True,
            model_visible=False,
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
            all_profiles,
            "memory_snapshot_builder",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "edit.apply_changeset",
            SideEffect.WRITE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.PROFILE,
            active_profiles,
            "changeset_worker",
            model_visible=False,
        ),
        _tool(
            "deps.install",
            SideEffect.EXECUTE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.PROFILE,
            active_profiles,
            "dependency_worker",
            sandbox=True,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        _tool(
            "preview.start",
            SideEffect.EXECUTE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.PROFILE,
            active_profiles,
            "preview_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "preview.stop",
            SideEffect.EXECUTE,
            RiskLevel.MEDIUM,
            ApprovalPolicy.PROFILE,
            active_profiles,
            "preview_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "review.typecheck",
            SideEffect.EXECUTE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            sandbox=True,
            idempotent=True,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        _tool(
            "review.lint",
            SideEffect.EXECUTE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            sandbox=True,
            idempotent=True,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        _tool(
            "review.test",
            SideEffect.EXECUTE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            sandbox=True,
            idempotent=True,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        _tool(
            "review.build",
            SideEffect.EXECUTE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            sandbox=True,
            idempotent=True,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        _tool(
            "review.health",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "review.browser",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "review_worker",
            idempotent=True,
            model_visible=False,
        ),
        _tool(
            "run.sandboxed",
            SideEffect.EXECUTE,
            RiskLevel.HIGH,
            ApprovalPolicy.NEVER,
            autonomous,
            "sandbox_worker",
            sandbox=True,
            description=(
                "Run a terminating validation or build argv inside the attested Task-bound "
                "Sandbox Workspace. Never start servers, watchers, or Preview runtimes."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 64,
                        "items": {"type": "string", "minLength": 1, "maxLength": 4_096},
                    },
                    "cwd": {"type": "string", "minLength": 1, "maxLength": 1_024},
                    "environment": {
                        "type": "array",
                        "maxItems": 32,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 64,
                                },
                                "value": {"type": "string", "maxLength": 8_192},
                            },
                            "required": ["name", "value"],
                            "additionalProperties": False,
                        },
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 900,
                    },
                    "output_limit_bytes": {
                        "type": "integer",
                        "minimum": 1_024,
                        "maximum": 1_048_576,
                    },
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "project.accept_version",
            SideEffect.WRITE,
            RiskLevel.HIGH,
            ApprovalPolicy.ALWAYS,
            all_profiles,
            "version_manager",
            model_visible=False,
        ),
    ]
    return ToolRegistry(definitions, slash_commands=default_slash_commands())
