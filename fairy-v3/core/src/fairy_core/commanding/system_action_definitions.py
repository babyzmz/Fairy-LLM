from __future__ import annotations

from fairy_core.commanding.registry import (
    ApprovalPolicy,
    RiskLevel,
    SideEffect,
    ToolDefinition,
    _tool,
)
from fairy_core.commanding.types import PermissionProfile


def system_action_definitions(
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
