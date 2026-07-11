from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fairy_core.commanding.registry import ApprovalPolicy, RiskLevel, SideEffect
from fairy_core.commanding.types import PermissionProfile

MCP_PROTOCOL_VERSION = "2025-11-25"
_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
_CREDENTIAL_REF = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9_.:/-]{1,255}$")


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _freeze_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if value is None:
        return None
    return MappingProxyType(json.loads(_canonical_json(dict(value))))


class McpTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class McpServerStatus(StrEnum):
    DISABLED = "disabled"
    UNTRUSTED = "untrusted"
    REVIEW_REQUIRED = "review_required"
    READY = "ready"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class McpConnection:
    server_id: str
    display_name: str
    transport: McpTransport
    command: str | None = None
    arguments: tuple[str, ...] = ()
    endpoint: str | None = None
    credential_ref: str | None = None
    environment_refs: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        server_id = self.server_id.strip().lower()
        if _ID.fullmatch(server_id) is None or "--" in server_id:
            raise ValueError("MCP server_id is invalid")
        display_name = self.display_name.strip()
        if not display_name or len(display_name) > 200:
            raise ValueError("MCP display_name is invalid")
        if len(self.arguments) > 64 or any(
            not argument or len(argument) > 4_096 or "\x00" in argument
            for argument in self.arguments
        ):
            raise ValueError("MCP stdio arguments are invalid")
        credential_ref = self.credential_ref.strip() if self.credential_ref else None
        if credential_ref is not None and _CREDENTIAL_REF.fullmatch(credential_ref) is None:
            raise ValueError("MCP credential_ref is invalid")
        environment_refs = {
            str(name): str(reference).strip()
            for name, reference in dict(self.environment_refs).items()
        }
        if len(environment_refs) > 32:
            raise ValueError("MCP environment_refs has too many entries")
        if any(_ENVIRONMENT_NAME.fullmatch(name) is None for name in environment_refs):
            raise ValueError("MCP environment variable name is invalid")
        if any(_CREDENTIAL_REF.fullmatch(value) is None for value in environment_refs.values()):
            raise ValueError("MCP environment credential reference is invalid")
        command = self.command.strip() if self.command else None
        endpoint = self.endpoint.strip() if self.endpoint else None
        if self.transport is McpTransport.STDIO:
            if (
                command is None
                or len(command) > 4_096
                or any(character in command for character in ("\x00", "\r", "\n"))
            ):
                raise ValueError("stdio MCP transport requires a fixed command")
            if endpoint is not None:
                raise ValueError("stdio MCP transport cannot declare an endpoint")
        else:
            if command is not None or self.arguments or environment_refs:
                raise ValueError("Streamable HTTP MCP cannot declare stdio process fields")
            endpoint = _validate_endpoint(endpoint)
        object.__setattr__(self, "server_id", server_id)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "credential_ref", credential_ref)
        object.__setattr__(self, "environment_refs", MappingProxyType(environment_refs))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            _canonical_json(
                {
                    "server_id": self.server_id,
                    "display_name": self.display_name,
                    "transport": self.transport.value,
                    "command": self.command,
                    "arguments": self.arguments,
                    "endpoint": self.endpoint,
                    "credential_ref": self.credential_ref,
                    "environment_refs": dict(self.environment_refs),
                }
            ).encode("ascii")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    name: str
    title: str | None
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None
    imported_name: str | None
    schema_digest: str

    def __post_init__(self) -> None:
        name = self.name.strip()
        description = self.description.strip()
        if not name or len(name) > 128 or not name.isascii():
            raise ValueError("MCP tool name is invalid")
        if not description or len(description) > 4_096:
            raise ValueError("MCP tool description is invalid")
        title = self.title.strip() if self.title else None
        if title is not None and len(title) > 200:
            raise ValueError("MCP tool title is invalid")
        if _SHA256.fullmatch(self.schema_digest) is None:
            raise ValueError("MCP schema digest is invalid")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "input_schema", _freeze_mapping(self.input_schema))
        object.__setattr__(self, "output_schema", _freeze_mapping(self.output_schema))

    @classmethod
    def create(
        cls,
        *,
        name: str,
        title: str | None,
        description: str,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any] | None,
    ) -> McpToolDescriptor:
        from fairy_core.mcp.schema import sanitize_extension_input_schema

        sanitized_input = sanitize_extension_input_schema(input_schema)
        sanitized_output = (
            sanitize_extension_input_schema(output_schema) if output_schema is not None else None
        )
        payload = {
            "name": name,
            "title": title,
            "description": description,
            "input_schema": sanitized_input,
            "output_schema": sanitized_output,
        }
        digest = hashlib.sha256(_canonical_json(payload).encode("ascii")).hexdigest()
        return cls(
            name=name,
            title=title,
            description=description,
            input_schema=sanitized_input,
            output_schema=sanitized_output,
            imported_name=None,
            schema_digest=digest,
        )

    @classmethod
    def bound(
        cls,
        *,
        name: str,
        title: str | None,
        description: str,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any] | None,
        imported_name: str,
        schema_digest: str,
    ) -> McpToolDescriptor:
        return cls(
            name=name,
            title=title,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            imported_name=imported_name,
            schema_digest=schema_digest,
        )

    def bind(self, server_id: str) -> McpToolDescriptor:
        from fairy_core.mcp.schema import sanitize_mcp_tool

        return sanitize_mcp_tool(
            server_id=server_id,
            name=self.name,
            title=self.title,
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
        )

    def with_description(self, description: str) -> McpToolDescriptor:
        return self.create(
            name=self.name,
            title=self.title,
            description=description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema) if self.output_schema is not None else None,
            "imported_name": self.imported_name,
            "schema_digest": self.schema_digest,
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> McpToolDescriptor:
        return cls(
            name=str(values["name"]),
            title=str(values["title"]) if values.get("title") is not None else None,
            description=str(values["description"]),
            input_schema=dict(values["input_schema"]),
            output_schema=(
                dict(values["output_schema"]) if values.get("output_schema") is not None else None
            ),
            imported_name=(
                str(values["imported_name"]) if values.get("imported_name") is not None else None
            ),
            schema_digest=str(values["schema_digest"]),
        )


@dataclass(frozen=True, slots=True)
class McpDiscovery:
    server_name: str
    protocol_version: str
    tools: tuple[McpToolDescriptor, ...]
    schema_digest: str

    @classmethod
    def create(
        cls,
        *,
        server_name: str,
        protocol_version: str,
        tools: tuple[McpToolDescriptor, ...],
    ) -> McpDiscovery:
        if protocol_version != MCP_PROTOCOL_VERSION:
            raise ValueError("MCP server negotiated an unsupported protocol version")
        bound = tuple(tool.bind(server_name) for tool in tools)
        imported_names = [tool.imported_name for tool in bound]
        if len(imported_names) != len(set(imported_names)):
            raise ValueError("MCP tool names collide after sanitization")
        digest = hashlib.sha256(
            _canonical_json(
                {
                    "protocol_version": protocol_version,
                    "tools": [tool.as_dict() for tool in bound],
                }
            ).encode("ascii")
        ).hexdigest()
        return cls(
            server_name=server_name.strip()[:200],
            protocol_version=protocol_version,
            tools=bound,
            schema_digest=digest,
        )


@dataclass(frozen=True, slots=True)
class McpToolPolicy:
    name: str
    enabled: bool
    side_effect: SideEffect
    risk_level: RiskLevel
    approval_policy: ApprovalPolicy
    profiles: frozenset[PermissionProfile]
    idempotent: bool

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name or len(name) > 128:
            raise ValueError("MCP policy tool name is invalid")
        if not self.profiles:
            raise ValueError("MCP tool policy requires at least one profile")
        if self.side_effect in {SideEffect.WRITE, SideEffect.EXECUTE}:
            if PermissionProfile.OBSERVE in self.profiles:
                raise ValueError("observe cannot expose mutating MCP tools")
            if self.idempotent:
                raise ValueError("mutating MCP tools cannot be trusted as replay-safe")
            if self.approval_policy is ApprovalPolicy.NEVER:
                raise ValueError("mutating MCP tools require approval policy")
        if self.risk_level is RiskLevel.HIGH and self.approval_policy is not ApprovalPolicy.ALWAYS:
            raise ValueError("high-risk MCP tools always require approval")
        object.__setattr__(self, "name", name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "side_effect": self.side_effect.value,
            "risk_level": self.risk_level.value,
            "approval_policy": self.approval_policy.value,
            "profiles": sorted(profile.value for profile in self.profiles),
            "idempotent": self.idempotent,
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> McpToolPolicy:
        return cls(
            name=str(values["name"]),
            enabled=bool(values["enabled"]),
            side_effect=SideEffect(str(values["side_effect"])),
            risk_level=RiskLevel(str(values["risk_level"])),
            approval_policy=ApprovalPolicy(str(values["approval_policy"])),
            profiles=frozenset(PermissionProfile(str(value)) for value in values["profiles"]),
            idempotent=bool(values["idempotent"]),
        )


@dataclass(frozen=True, slots=True)
class McpServerRecord:
    connection: McpConnection
    enabled: bool
    status: McpServerStatus
    accepted_schema_digest: str | None
    pending_schema_digest: str | None
    accepted_tools: tuple[McpToolDescriptor, ...]
    pending_tools: tuple[McpToolDescriptor, ...]
    policies: tuple[McpToolPolicy, ...]
    revision: int
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(cls, connection: McpConnection) -> McpServerRecord:
        now = _now()
        return cls(
            connection=connection,
            enabled=False,
            status=McpServerStatus.UNTRUSTED,
            accepted_schema_digest=None,
            pending_schema_digest=None,
            accepted_tools=(),
            pending_tools=(),
            policies=(),
            revision=1,
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )

    def reconfigure(self, connection: McpConnection) -> McpServerRecord:
        if connection.server_id != self.connection.server_id:
            raise ValueError("MCP server identity cannot change")
        if connection.fingerprint == self.connection.fingerprint:
            return self
        return replace(
            self,
            connection=connection,
            enabled=False,
            status=McpServerStatus.UNTRUSTED,
            accepted_schema_digest=None,
            pending_schema_digest=None,
            accepted_tools=(),
            pending_tools=(),
            policies=(),
            revision=self.revision + 1,
            last_error_code=None,
            updated_at=_now(),
        )

    def record_discovery(self, discovery: McpDiscovery) -> McpServerRecord:
        trusted = discovery.schema_digest == self.accepted_schema_digest and bool(self.policies)
        return replace(
            self,
            status=(
                McpServerStatus.READY
                if trusted and self.enabled
                else McpServerStatus.DISABLED
                if trusted
                else McpServerStatus.REVIEW_REQUIRED
            ),
            pending_schema_digest=discovery.schema_digest,
            pending_tools=discovery.tools,
            accepted_tools=discovery.tools if trusted else self.accepted_tools,
            revision=self.revision + 1,
            last_error_code=None,
            updated_at=_now(),
        )

    def accept(
        self,
        *,
        schema_digest: str,
        policies: tuple[McpToolPolicy, ...],
        enabled: bool,
    ) -> McpServerRecord:
        if schema_digest != self.pending_schema_digest or _SHA256.fullmatch(schema_digest) is None:
            raise ValueError("MCP pending schema digest does not match")
        discovered_names = {tool.name for tool in self.pending_tools}
        policy_names = {policy.name for policy in policies}
        if discovered_names != policy_names or len(policies) != len(policy_names):
            raise ValueError("MCP trust policy must decide every discovered tool exactly once")
        return replace(
            self,
            enabled=enabled,
            status=McpServerStatus.READY if enabled else McpServerStatus.DISABLED,
            accepted_schema_digest=schema_digest,
            accepted_tools=self.pending_tools,
            policies=policies,
            revision=self.revision + 1,
            last_error_code=None,
            updated_at=_now(),
        )

    def set_enabled(self, enabled: bool) -> McpServerRecord:
        if enabled and (self.accepted_schema_digest is None or not self.policies):
            raise ValueError("MCP server cannot be enabled before trust acceptance")
        return replace(
            self,
            enabled=enabled,
            status=McpServerStatus.READY if enabled else McpServerStatus.DISABLED,
            revision=self.revision + 1,
            updated_at=_now(),
        )

    def mark_unavailable(self, error_code: str) -> McpServerRecord:
        return replace(
            self,
            status=McpServerStatus.UNAVAILABLE,
            revision=self.revision + 1,
            last_error_code=error_code.strip()[:128] or "MCP_UNAVAILABLE",
            updated_at=_now(),
        )


@dataclass(frozen=True, slots=True)
class McpCallContext:
    command_run_id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    scope_digest: str
    lease_fence: int

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.scope_digest) is None or self.lease_fence < 1:
            raise ValueError("MCP call context is not bound to a valid Core Scope")


@dataclass(frozen=True, slots=True)
class McpCallResult:
    text: str | None
    structured_content: Mapping[str, Any] | None
    is_error: bool
    response_id: str | None

    @classmethod
    def create(
        cls,
        *,
        text: str | None,
        structured_content: Mapping[str, Any] | None,
        is_error: bool = False,
        response_id: str | None = None,
    ) -> McpCallResult:
        normalized_text = text.strip() if text else None
        if normalized_text is not None and len(normalized_text.encode("utf-8")) > 256 * 1024:
            raise ValueError("MCP text result is too large")
        structured = _freeze_mapping(structured_content)
        if (
            structured is not None
            and len(_canonical_json(dict(structured)).encode("utf-8")) > 256 * 1024
        ):
            raise ValueError("MCP structured result is too large")
        if normalized_text is None and structured is None:
            raise ValueError("MCP result contains no supported content")
        normalized_id = response_id.strip() if response_id else None
        if normalized_id is not None and len(normalized_id) > 255:
            raise ValueError("MCP response_id is too long")
        return cls(
            text=normalized_text,
            structured_content=structured,
            is_error=is_error,
            response_id=normalized_id,
        )


def _validate_endpoint(value: str | None) -> str:
    if value is None or len(value) > 4_096:
        raise ValueError("Streamable HTTP MCP requires an endpoint")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("MCP endpoint is invalid") from error
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("MCP endpoint cannot contain credentials, query, or fragment")
    if parsed.hostname is None or not parsed.path.startswith("/"):
        raise ValueError("MCP endpoint requires a host and absolute path")
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (loopback and parsed.scheme == "http"):
        raise ValueError("remote MCP endpoints require HTTPS")
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("MCP endpoint port is invalid")
    return value


__all__ = [
    "MCP_PROTOCOL_VERSION",
    "McpCallContext",
    "McpCallResult",
    "McpConnection",
    "McpDiscovery",
    "McpServerRecord",
    "McpServerStatus",
    "McpToolDescriptor",
    "McpToolPolicy",
    "McpTransport",
]
