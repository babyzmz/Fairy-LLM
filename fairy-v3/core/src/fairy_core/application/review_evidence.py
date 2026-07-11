from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fairy_core.domain.execution import ArtifactType, Changeset
from fairy_core.domain.models import Task
from fairy_core.storage import StateStore
from fairy_core.workspace.ports import ProjectIndexRepository


@dataclass(frozen=True, slots=True)
class CheckpointEvidence:
    changed_files: tuple[str, ...]
    command_run_ids: tuple[UUID, ...]
    preview_artifact_id: UUID | None
    evidence_artifact_ids: tuple[UUID, ...]


def collect_checkpoint_evidence(
    *,
    state: StateStore,
    project_indexes: ProjectIndexRepository,
    task: Task,
    changesets: list[Changeset],
) -> CheckpointEvidence:
    if task.target_version_id is None:
        raise ValueError("Checkpoint evidence requires a target Version")
    changed_files = tuple(dict.fromkeys(path for item in changesets for path in item.files))
    approvals = [
        approval
        for item in changesets
        if (approval := state.find_approval_by_changeset_id(item.id)) is not None
    ]
    current_index = project_indexes.get(task.target_version_id)
    generation = current_index.generation if current_index is not None else None
    artifacts = state.artifacts_for_task(task.id)
    preview_artifact = next(
        (
            artifact
            for artifact in reversed(artifacts)
            if artifact.artifact_type is ArtifactType.PREVIEW_MANIFEST
            and artifact.version_id == task.target_version_id
            and artifact.metadata.get("status") == "ready"
            and artifact.metadata.get("workspace_generation") == generation
        ),
        None,
    )
    evidence_artifacts = tuple(
        artifact
        for artifact in artifacts
        if artifact.version_id == task.target_version_id
        and artifact.metadata.get("status") == "completed"
        and artifact.metadata.get("workspace_generation") == generation
        and (
            artifact.metadata.get("runtime_review") is not True
            or (
                preview_artifact is not None
                and artifact.metadata.get("preview_manifest_id") == str(preview_artifact.id)
            )
        )
        and (
            artifact.artifact_type is ArtifactType.REPORT
            or (
                artifact.artifact_type is ArtifactType.SCREENSHOT
                and preview_artifact is not None
                and artifact.metadata.get("preview_manifest_id") == str(preview_artifact.id)
            )
        )
    )
    evidence_command_ids = tuple(
        UUID(str(artifact.metadata["command_run_id"]))
        for artifact in evidence_artifacts
        if isinstance(artifact.metadata.get("command_run_id"), str)
    )
    return CheckpointEvidence(
        changed_files=changed_files,
        command_run_ids=(
            tuple(dict.fromkeys(evidence_command_ids))
            or tuple(approval.command_run_id for approval in approvals)
        ),
        preview_artifact_id=(preview_artifact.id if preview_artifact is not None else None),
        evidence_artifact_ids=tuple(artifact.id for artifact in evidence_artifacts),
    )


__all__ = ["CheckpointEvidence", "collect_checkpoint_evidence"]
