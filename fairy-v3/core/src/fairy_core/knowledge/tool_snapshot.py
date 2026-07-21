from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from fairy_core.commanding.registry import (
    ApprovalPolicy,
    RiskLevel,
    SideEffect,
    ToolDefinition,
)
from fairy_core.commanding.types import PermissionProfile


@dataclass(frozen=True, slots=True)
class ToolDefinitionSnapshot:
    name: str
    side_effect: str
    risk_level: str
    approval_policy: str
    profiles: tuple[str, ...]
    executor: str
    requires_sandbox: bool
    idempotent: bool
    model_visible: bool
    description: str
    source: str
    origin_id: str | None
    required_operations: tuple[str, ...]
    required_extensions: tuple[str, ...]
    input_schema: Mapping[str, Any]
    definition_digest: str

    def __post_init__(self) -> None:
        schema = json.loads(
            json.dumps(
                dict(self.input_schema),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        definition = ToolDefinition(
            name=self.name,
            side_effect=SideEffect(self.side_effect),
            risk_level=RiskLevel(self.risk_level),
            approval_policy=ApprovalPolicy(self.approval_policy),
            profiles=frozenset(PermissionProfile(profile) for profile in self.profiles),
            executor=self.executor,
            requires_sandbox=self.requires_sandbox,
            idempotent=self.idempotent,
            model_visible=self.model_visible,
            description=self.description,
            source=self.source,
            origin_id=self.origin_id,
            required_operations=frozenset(self.required_operations),
            required_extensions=frozenset(self.required_extensions),
            input_schema=schema,
        )
        if definition.definition_digest != self.definition_digest:
            raise ValueError("Harness Tool Definition digest does not match its content")
        object.__setattr__(self, "profiles", tuple(sorted(self.profiles)))
        object.__setattr__(
            self,
            "required_operations",
            tuple(sorted(self.required_operations)),
        )
        object.__setattr__(
            self,
            "required_extensions",
            tuple(sorted(self.required_extensions)),
        )
        object.__setattr__(self, "input_schema", MappingProxyType(schema))

    @classmethod
    def capture(cls, definition: ToolDefinition) -> ToolDefinitionSnapshot:
        return cls(
            name=definition.name,
            side_effect=definition.side_effect.value,
            risk_level=definition.risk_level.value,
            approval_policy=definition.approval_policy.value,
            profiles=tuple(sorted(profile.value for profile in definition.profiles)),
            executor=definition.executor,
            requires_sandbox=definition.requires_sandbox,
            idempotent=definition.idempotent,
            model_visible=definition.model_visible,
            description=definition.description,
            source=definition.source,
            origin_id=definition.origin_id,
            required_operations=tuple(sorted(definition.required_operations)),
            required_extensions=tuple(sorted(definition.required_extensions)),
            input_schema=definition.input_schema,
            definition_digest=definition.definition_digest,
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ToolDefinitionSnapshot:
        return cls(
            name=str(payload["name"]),
            side_effect=str(payload["side_effect"]),
            risk_level=str(payload["risk_level"]),
            approval_policy=str(payload["approval_policy"]),
            profiles=tuple(str(value) for value in payload["profiles"]),
            executor=str(payload["executor"]),
            requires_sandbox=bool(payload["requires_sandbox"]),
            idempotent=bool(payload["idempotent"]),
            model_visible=bool(payload["model_visible"]),
            description=str(payload["description"]),
            source=str(payload["source"]),
            origin_id=(str(payload["origin_id"]) if payload.get("origin_id") else None),
            required_operations=tuple(str(value) for value in payload["required_operations"]),
            required_extensions=tuple(str(value) for value in payload["required_extensions"]),
            input_schema=dict(payload["input_schema"]),
            definition_digest=str(payload["definition_digest"]),
        )

    def to_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            side_effect=SideEffect(self.side_effect),
            risk_level=RiskLevel(self.risk_level),
            approval_policy=ApprovalPolicy(self.approval_policy),
            profiles=frozenset(PermissionProfile(profile) for profile in self.profiles),
            executor=self.executor,
            requires_sandbox=self.requires_sandbox,
            idempotent=self.idempotent,
            model_visible=self.model_visible,
            description=self.description,
            source=self.source,
            origin_id=self.origin_id,
            required_operations=frozenset(self.required_operations),
            required_extensions=frozenset(self.required_extensions),
            input_schema=self.input_schema,
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "side_effect": self.side_effect,
            "risk_level": self.risk_level,
            "approval_policy": self.approval_policy,
            "profiles": list(self.profiles),
            "executor": self.executor,
            "requires_sandbox": self.requires_sandbox,
            "idempotent": self.idempotent,
            "model_visible": self.model_visible,
            "description": self.description,
            "source": self.source,
            "origin_id": self.origin_id,
            "required_operations": list(self.required_operations),
            "required_extensions": list(self.required_extensions),
            "input_schema": dict(self.input_schema),
            "definition_digest": self.definition_digest,
        }


__all__ = ["ToolDefinitionSnapshot"]
