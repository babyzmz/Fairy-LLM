from __future__ import annotations

from fairy_core.commanding import tool_schemas
from fairy_core.commanding.registry import (
    ApprovalPolicy,
    RiskLevel,
    SideEffect,
    ToolDefinition,
    _tool,
)
from fairy_core.commanding.types import PermissionProfile


def project_definitions(
    all_profiles: frozenset[PermissionProfile],
    active_profiles: frozenset[PermissionProfile],
) -> list[ToolDefinition]:
    return [
        _tool(
            "execution.plan",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "project_tools",
            idempotent=True,
            description=(
                "Create the immutable file, dependency, entrypoint, and validation plan "
                "before modifying a Workspace. Paths must be canonical Workspace-relative "
                "paths. Validation commands must terminate; never include servers, watchers, "
                "dev runtimes, or Preview startup commands."
            ),
            input_schema=tool_schemas.execution_plan_tool_schema(),
        ),
        _tool(
            "project.list",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "project_tools",
            idempotent=True,
            description=("List a stable page of files from the Task-bound managed Project Index."),
            input_schema=tool_schemas.project_list_tool_schema(),
        ),
        _tool(
            "project.search",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "project_tools",
            idempotent=True,
            description=(
                "Search bounded UTF-8 source literally in the Task-bound managed Version. "
                "Use project.read before modifying a matching file."
            ),
            input_schema=tool_schemas.project_search_tool_schema(),
        ),
        _tool(
            "project.read",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "project_tools",
            idempotent=True,
            description="Read bounded UTF-8 source from the Task-bound managed Version.",
            input_schema=tool_schemas.project_read_tool_schema(),
        ),
        _tool(
            "artifact.list",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "project_tools",
            idempotent=True,
            description="List user-visible Artifacts owned by the current Task.",
            input_schema={"type": "object", "additionalProperties": False},
        ),
        _tool(
            "artifact.read",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "project_tools",
            idempotent=True,
            description="Read one bounded Artifact owned by the current Task Scope.",
            input_schema=tool_schemas.artifact_read_tool_schema(),
        ),
        _tool(
            "preview.status",
            SideEffect.READ,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            all_profiles,
            "project_tools",
            idempotent=True,
            description=(
                "Read the latest durable Runtime and Preview status after planned Workspace "
                "changes have been applied. This does not create files or replace execution.plan."
            ),
            input_schema={"type": "object", "additionalProperties": False},
        ),
        _tool(
            "edit.propose_changeset",
            SideEffect.WRITE,
            RiskLevel.LOW,
            ApprovalPolicy.NEVER,
            active_profiles,
            "project_tools",
            idempotent=True,
            description=(
                "Submit one complete immutable file batch as a governed Changeset. Use canonical "
                "Workspace-relative paths and complete file contents; never send comment-only, "
                "TODO-only, empty-body, or other placeholder files."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 25,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 1_024,
                                    "description": (
                                        "Canonical Workspace-relative path without a leading ./ "
                                        "or /workspace prefix."
                                    ),
                                },
                                "content": {
                                    "type": "string",
                                    "maxLength": 2_097_152,
                                    "description": (
                                        "Complete durable file content. Placeholder and "
                                        "comment-only source is rejected."
                                    ),
                                },
                            },
                            "required": ["path", "content"],
                            "additionalProperties": False,
                        },
                    },
                    "reason": {"type": "string", "minLength": 1, "maxLength": 10_000},
                },
                "required": ["files", "reason"],
                "additionalProperties": False,
            },
        ),
    ]
