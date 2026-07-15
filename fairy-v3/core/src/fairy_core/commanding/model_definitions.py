from __future__ import annotations

from fairy_core.commanding.registry import (
    ApprovalPolicy,
    RiskLevel,
    SideEffect,
    ToolDefinition,
)
from fairy_core.commanding.types import PermissionProfile


def model_generation_definitions(
    profiles: frozenset[PermissionProfile],
) -> tuple[ToolDefinition, ...]:
    shared = {
        "side_effect": SideEffect.READ,
        "profiles": profiles,
        "executor": "model_provider",
        "idempotent": True,
        "model_visible": False,
    }
    return (
        ToolDefinition(
            name="model.generate",
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            **shared,
        ),
        ToolDefinition(
            name="model.generate.expensive",
            risk_level=RiskLevel.MEDIUM,
            approval_policy=ApprovalPolicy.ALWAYS,
            description="Authorize a model route that exceeds the automatic cost policy.",
            input_schema={
                "type": "object",
                "properties": {
                    "turn_id": {"type": "string", "format": "uuid"},
                    "estimated_cost_usd": {"type": ["string", "null"]},
                    "execution_model_count": {"type": "integer", "minimum": 1},
                },
                "required": [
                    "turn_id",
                    "estimated_cost_usd",
                    "execution_model_count",
                ],
                "additionalProperties": False,
            },
            **shared,
        ),
    )


__all__ = ["model_generation_definitions"]
