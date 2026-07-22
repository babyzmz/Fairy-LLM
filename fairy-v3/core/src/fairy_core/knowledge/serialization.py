from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from fairy_core.knowledge.models import HarnessContextManifest
from fairy_core.knowledge.tool_snapshot import ToolDefinitionSnapshot


def manifest_from_row(row: Mapping[str, Any]) -> HarnessContextManifest:
    workspace_version = row["workspace_version_id"]
    return HarnessContextManifest(
        id=UUID(row["id"]),
        task_id=UUID(row["task_id"]),
        scope_digest=row["scope_digest"],
        workspace_id=UUID(row["workspace_id"]),
        workspace_version_id=(UUID(workspace_version) if workspace_version else None),
        memory_snapshot_id=UUID(row["memory_snapshot_id"]),
        memory_snapshot_hash=row["memory_snapshot_hash"],
        knowledge_snapshot_id=UUID(row["knowledge_snapshot_id"]),
        knowledge_snapshot_hash=row["knowledge_snapshot_hash"],
        tool_registry_generation=int(row["tool_registry_generation"]),
        tool_registry_digest=row["tool_registry_digest"],
        tool_definitions=tuple(
            ToolDefinitionSnapshot.from_payload(payload) for payload in row["tool_definitions"]
        ),
        skill_package_digests=tuple(row["skill_package_digests"]),
        mcp_capability_snapshot=tuple(row["mcp_capability_snapshot"]),
        model_selection=dict(row["model_selection"]),
        budget=dict(row["budget"]),
        persona_version=row["persona_version"],
        persona_digest=row["persona_digest"],
        persona_instruction=row.get("persona_instruction") or "",
        content_hash=row["content_hash"],
        created_at=row["created_at"],
    )


__all__ = ["manifest_from_row"]
