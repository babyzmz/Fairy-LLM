from __future__ import annotations

from fairy_core.commanding.registry import (
    ApprovalPolicy,
    RiskLevel,
    SideEffect,
    ToolDefinition,
)
from fairy_core.commanding.types import PermissionProfile


def media_generation_definitions(
    profiles: frozenset[PermissionProfile],
) -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            name="media.images.generate",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.MEDIUM,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="media_application",
            idempotent=True,
            description="Generate one standard 1K image into the current Workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "minLength": 1, "maxLength": 20_000},
                    "output_path": {"type": "string", "minLength": 1, "maxLength": 1_024},
                    "size": {"type": "string", "enum": ["1024x1024"]},
                    "aspect_ratio": {
                        "type": "string",
                        "enum": ["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"],
                    },
                    "seed": {"type": "integer", "minimum": 0, "maximum": 2_147_483_647},
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="media.audio.generate",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.HIGH,
            approval_policy=ApprovalPolicy.ALWAYS,
            profiles=profiles,
            executor="media_application",
            idempotent=True,
            description="Generate music into the current Workspace after explicit approval.",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "minLength": 1, "maxLength": 20_000},
                    "output_path": {"type": "string", "minLength": 1, "maxLength": 1_024},
                    "output_format": {"type": "string", "enum": ["wav"]},
                    "seed": {"type": "integer", "minimum": 0, "maximum": 2_147_483_647},
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="media.videos.start",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.HIGH,
            approval_policy=ApprovalPolicy.ALWAYS,
            profiles=profiles,
            executor="media_application",
            idempotent=True,
            description="Start an asynchronous video generation after explicit approval.",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "minLength": 1, "maxLength": 20_000},
                    "output_path": {"type": "string", "minLength": 1, "maxLength": 1_024},
                    "duration_seconds": {"type": "integer", "minimum": 1, "maximum": 20},
                    "resolution": {"type": "string", "enum": ["720p", "1080p"]},
                    "aspect_ratio": {"type": "string", "enum": ["1:1", "16:9", "9:16"]},
                    "generate_audio": {"type": "boolean"},
                    "seed": {"type": "integer", "minimum": 0, "maximum": 2_147_483_647},
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="media.videos.poll",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.LOW,
            approval_policy=ApprovalPolicy.NEVER,
            profiles=profiles,
            executor="media_application",
            idempotent=True,
            model_visible=False,
            description="Refresh one durable video generation job.",
            input_schema={
                "type": "object",
                "properties": {"job_id": {"type": "string", "format": "uuid"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="media.videos.cancel",
            side_effect=SideEffect.WRITE,
            risk_level=RiskLevel.MEDIUM,
            approval_policy=ApprovalPolicy.ALWAYS,
            profiles=profiles,
            executor="media_application",
            idempotent=True,
            model_visible=False,
            description=(
                "Stop Fairy polling and downloading a video job; upstream generation may continue."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "format": "uuid"},
                    "expected_revision": {"type": "integer", "minimum": 0},
                },
                "required": ["job_id", "expected_revision"],
                "additionalProperties": False,
            },
        ),
    )


__all__ = ["media_generation_definitions"]
