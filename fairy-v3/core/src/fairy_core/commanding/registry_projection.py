from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from fairy_core.commanding.types import PermissionProfile

if TYPE_CHECKING:
    from fairy_core.commanding.registry import SlashCommandDefinition, ToolDefinition


def bounded_public_description(description: str) -> str:
    if len(description) <= 1_000:
        return description
    return description[:997].rstrip() + "..."


def frontend_metadata(
    definitions: Iterable[ToolDefinition],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "name": definition.name,
            "side_effect": definition.side_effect.value,
            "risk_level": definition.risk_level.value,
            "approval_policy": definition.approval_policy.value,
            "profiles": sorted(profile.value for profile in definition.profiles),
            "requires_sandbox": definition.requires_sandbox,
            "idempotent": definition.idempotent,
            "concurrency_policy": definition.concurrency_policy.value,
            "concurrency_resource_keys": list(definition.concurrency_resource_keys),
            "model_visible": definition.model_visible,
            "description": definition.description,
            "source": definition.source,
            "origin_id": definition.origin_id,
            "required_operations": sorted(definition.required_operations),
            "required_extensions": sorted(definition.required_extensions),
            "definition_digest": definition.definition_digest,
            "input_schema": dict(definition.input_schema),
        }
        for definition in definitions
    )


def capability_manifest(
    definitions: Mapping[str, ToolDefinition],
    *,
    ready_extensions: frozenset[str],
    profile: PermissionProfile,
    sandbox_healthy: bool,
    overrides: dict[str, bool] | None,
) -> dict[str, bool]:
    effective_overrides = overrides or {}
    resolved: dict[str, bool] = {}

    def enabled(name: str, visiting: frozenset[str] = frozenset()) -> bool:
        cached = resolved.get(name)
        if cached is not None:
            return cached
        definition = definitions.get(name)
        if definition is None or name in visiting:
            return False
        allowed = (
            profile in definition.profiles
            and effective_overrides.get(name, True)
            and (not definition.requires_sandbox or sandbox_healthy)
            and definition.required_extensions.issubset(ready_extensions)
        )
        if allowed:
            path = visiting | {name}
            allowed = all(
                enabled(dependency, path) for dependency in definition.required_operations
            )
        resolved[name] = allowed
        return allowed

    return {name: enabled(name) for name in definitions}


def available_agent_definitions(
    definitions: Mapping[str, ToolDefinition],
    *,
    ready_extensions: frozenset[str],
    profile: PermissionProfile,
    sandbox_healthy: bool,
    overrides: dict[str, bool] | None,
) -> tuple[ToolDefinition, ...]:
    manifest = capability_manifest(
        definitions,
        ready_extensions=ready_extensions,
        profile=profile,
        sandbox_healthy=sandbox_healthy,
        overrides=overrides,
    )
    return tuple(
        definition
        for definition in definitions.values()
        if definition.model_visible and manifest.get(definition.name, False)
    )


def slash_command_metadata(
    commands: Iterable[SlashCommandDefinition],
    operations: Mapping[str, bool],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "name": command.name,
            "description": command.description,
            "argument_hint": command.argument_hint,
            "required_operation": command.required_operation,
            "available": (
                command.required_operation is None
                or operations.get(command.required_operation, False) is True
            ),
        }
        for command in commands
    )


__all__ = [
    "available_agent_definitions",
    "bounded_public_description",
    "capability_manifest",
    "frontend_metadata",
    "slash_command_metadata",
]
