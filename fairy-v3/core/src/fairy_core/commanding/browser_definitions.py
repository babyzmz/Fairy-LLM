from __future__ import annotations

from fairy_core.commanding.registry import (
    ApprovalPolicy,
    RiskLevel,
    SideEffect,
    ToolDefinition,
)
from fairy_core.commanding.types import PermissionProfile


def browser_definitions(profiles: frozenset[PermissionProfile]) -> tuple[ToolDefinition, ...]:
    read_schema = {"type": "object", "additionalProperties": False}
    selector_schema = {
        "type": "object",
        "properties": {"selector": {"type": "string", "minLength": 1, "maxLength": 2048}},
        "required": ["selector"],
        "additionalProperties": False,
    }
    value_schema = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "minLength": 1, "maxLength": 2048},
            "value": {"type": "string", "maxLength": 32000},
        },
        "required": ["selector", "value"],
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
            description="Read a bounded accessibility snapshot of the current page.",
            input_schema=read_schema,
        ),
        ToolDefinition(
            name="browser.click",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.MEDIUM,
            approval_policy=ApprovalPolicy.PROFILE,
            profiles=profiles,
            executor="browser_worker",
            description="Click a referenced element in the scoped Fairy browser tab.",
            input_schema=selector_schema,
        ),
        ToolDefinition(
            name="browser.fill",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.MEDIUM,
            approval_policy=ApprovalPolicy.PROFILE,
            profiles=profiles,
            executor="browser_worker",
            description="Fill a non-secret value into a referenced form control.",
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
