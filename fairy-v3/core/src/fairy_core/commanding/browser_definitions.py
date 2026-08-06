from __future__ import annotations

from fairy_core.commanding.registry import (
    ApprovalPolicy,
    RiskLevel,
    SideEffect,
    ToolDefinition,
)
from fairy_core.commanding.types import PermissionProfile


def browser_definitions(profiles: frozenset[PermissionProfile]) -> tuple[ToolDefinition, ...]:
    target_properties = {
        "element_ref": {"type": "string", "minLength": 1, "maxLength": 255},
        "selector": {"type": "string", "minLength": 1, "maxLength": 2048},
    }
    selector_schema = {
        "type": "object",
        "properties": target_properties,
        "anyOf": [{"required": ["element_ref"]}, {"required": ["selector"]}],
        "additionalProperties": False,
    }
    value_schema = {
        "type": "object",
        "properties": {
            **target_properties,
            "value": {"type": "string", "maxLength": 32000},
        },
        "required": ["value"],
        "anyOf": [{"required": ["element_ref"]}, {"required": ["selector"]}],
        "additionalProperties": False,
    }
    return (
        ToolDefinition(
            name="browser.navigate",
            side_effect=SideEffect.READ,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="browser_worker",
            idempotent=True,
            description="Navigate the scoped Fairy browser tab to an HTTP or HTTPS URL.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string", "minLength": 1, "maxLength": 4096}},
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="browser.snapshot",
            side_effect=SideEffect.READ,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="browser_worker",
            idempotent=True,
            description=(
                "Read a bounded accessibility snapshot and optionally capture transient visual "
                "evidence from the current page."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "include_screenshot": {"type": "boolean"},
                    "capture_label": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 64,
                    },
                },
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="browser.viewport",
            side_effect=SideEffect.READ,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="browser_worker",
            idempotent=True,
            description="Set the scoped Browser viewport for responsive visual inspection.",
            input_schema={
                "type": "object",
                "properties": {
                    "width": {"type": "integer", "minimum": 320, "maximum": 3840},
                    "height": {"type": "integer", "minimum": 240, "maximum": 2160},
                },
                "required": ["width", "height"],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="browser.wait",
            side_effect=SideEffect.READ,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="browser_worker",
            idempotent=True,
            description="Wait briefly for the scoped Browser page to settle.",
            input_schema={
                "type": "object",
                "properties": {
                    "timeout_ms": {"type": "integer", "minimum": 0, "maximum": 10000},
                },
                "required": ["timeout_ms"],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="browser.reload",
            side_effect=SideEffect.READ,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="browser_worker",
            description="Reload the current scoped Browser tab.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolDefinition(
            name="browser.back",
            side_effect=SideEffect.READ,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="browser_worker",
            description="Navigate the current scoped Browser tab back one history entry.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolDefinition(
            name="browser.forward",
            side_effect=SideEffect.READ,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="browser_worker",
            description="Navigate the current scoped Browser tab forward one history entry.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolDefinition(
            name="browser.hover",
            side_effect=SideEffect.READ,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="browser_worker",
            idempotent=True,
            description="Hover a stable element reference from the latest Browser snapshot.",
            input_schema=selector_schema,
        ),
        ToolDefinition(
            name="browser.click",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.MEDIUM,
            approval_policy=ApprovalPolicy.PROFILE,
            profiles=profiles,
            executor="browser_worker",
            description=(
                "Click a stable element reference from the latest Browser snapshot. "
                "Purchases, publishing, sending, and account or permission changes are blocked."
            ),
            input_schema=selector_schema,
        ),
        ToolDefinition(
            name="browser.fill",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.MEDIUM,
            approval_policy=ApprovalPolicy.PROFILE,
            profiles=profiles,
            executor="browser_worker",
            description=(
                "Fill a non-secret value into a stable form-control reference. Passwords, tokens, "
                "one-time codes, and payment secrets are blocked."
            ),
            input_schema=value_schema,
        ),
        ToolDefinition(
            name="browser.press",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.MEDIUM,
            approval_policy=ApprovalPolicy.PROFILE,
            profiles=profiles,
            executor="browser_worker",
            description="Press a key in a referenced element.",
            input_schema=value_schema,
        ),
        ToolDefinition(
            name="browser.select",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.MEDIUM,
            approval_policy=ApprovalPolicy.PROFILE,
            profiles=profiles,
            executor="browser_worker",
            description="Select a non-secret option in a stable form-control reference.",
            input_schema=value_schema,
        ),
        ToolDefinition(
            name="browser.check",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.MEDIUM,
            approval_policy=ApprovalPolicy.PROFILE,
            profiles=profiles,
            executor="browser_worker",
            description="Set a non-secret checkbox or radio control to a requested state.",
            input_schema={
                "type": "object",
                "properties": {
                    **target_properties,
                    "checked": {"type": "boolean"},
                },
                "required": ["checked"],
                "anyOf": [{"required": ["element_ref"]}, {"required": ["selector"]}],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="browser.tab",
            side_effect=SideEffect.READ,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="browser_worker",
            description="Open, select, or close a tab inside the scoped Fairy Browser session.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["open", "select", "close"]},
                    "tab_id": {"type": "string", "format": "uuid"},
                    "url": {"type": "string", "minLength": 1, "maxLength": 4096},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="browser.download",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.MEDIUM,
            approval_policy=ApprovalPolicy.PROFILE,
            profiles=profiles,
            executor="browser_worker",
            description=(
                "Download a referenced file into the current task's isolated download directory. "
                "The file is limited to 50 MiB, hashed, recorded, and never opened or executed."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    **target_properties,
                    "max_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50 * 1024 * 1024,
                    },
                },
                "anyOf": [{"required": ["element_ref"]}, {"required": ["selector"]}],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="browser.scroll",
            side_effect=SideEffect.READ,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="browser_worker",
            idempotent=True,
            description="Scroll the current page by a bounded vertical distance.",
            input_schema={
                "type": "object",
                "properties": {"delta_y": {"type": "number", "minimum": -4000, "maximum": 4000}},
                "required": ["delta_y"],
                "additionalProperties": False,
            },
        ),
    )


__all__ = ["browser_definitions"]
