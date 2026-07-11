from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fairy_core.domain.execution import (
    Artifact,
    ArtifactType,
    ArtifactVisibility,
    PreviewSession,
    RuntimeSession,
)
from fairy_core.storage import StateStore


def ensure_preview_manifest(
    state: StateStore,
    *,
    runtime: RuntimeSession,
    preview: PreviewSession,
    url: str,
    command_run_id: UUID | None,
    adapter: str = "static",
    dependency_key: str | None = None,
    entry_path: str | None = "index.html",
    readiness_path: str = "/",
    workspace_generation: int | None = None,
) -> Artifact:
    expected_preview_id = str(preview.id)
    existing = next(
        (
            artifact
            for artifact in state.artifacts_for_task(preview.task_id)
            if artifact.artifact_type is ArtifactType.PREVIEW_MANIFEST
            and artifact.metadata.get("preview_id") == expected_preview_id
        ),
        None,
    )
    if existing is not None:
        return existing

    payload = {
        "schema_version": 1,
        "preview_id": expected_preview_id,
        "runtime_id": str(runtime.id),
        "kind": runtime.kind.value,
        "executor": runtime.executor,
        "execution_target": runtime.execution_target,
        "adapter": adapter,
        "dependency_key": dependency_key,
        "entry_path": entry_path,
        "readiness_path": readiness_path,
        "workspace_generation": workspace_generation,
        "url": url,
        "command_run_id": str(command_run_id) if command_run_id is not None else None,
    }
    content = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    encoded = content.encode("utf-8")
    artifact = Artifact.create(
        project_id=preview.project_id,
        conversation_id=preview.conversation_id,
        task_id=preview.task_id,
        version_id=preview.version_id,
        artifact_type=ArtifactType.PREVIEW_MANIFEST,
        visibility=ArtifactVisibility.CONVERSATION,
        storage_location=f"inline://preview/{preview.id}/manifest",
        media_type="application/json",
        byte_length=len(encoded),
        content_hash=hashlib.sha256(encoded).hexdigest(),
        metadata={**payload, "content": content, "status": "ready"},
    )
    return state.append_artifact(artifact)


__all__ = ["ensure_preview_manifest"]
