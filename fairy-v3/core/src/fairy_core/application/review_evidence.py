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
    review_command_ids = tuple(
        UUID(str(artifact.metadata["command_run_id"]))
        for artifact in artifacts
        if artifact.artifact_type is ArtifactType.REPORT
        and artifact.metadata.get("status") == "completed"
        and artifact.metadata.get("workspace_generation") == generation
        and isinstance(artifact.metadata.get("command_run_id"), str)
    )
    preview_artifact = next(
        (
            artifact
            for artifact in reversed(artifacts)
            if artifact.artifact_type is ArtifactType.PREVIEW_MANIFEST
            and artifact.version_id == task.target_version_id
            and artifact.metadata.get("status") == "ready"
        ),
        None,
    )
    return CheckpointEvidence(
        changed_files=changed_files,
        command_run_ids=(
            review_command_ids or tuple(approval.command_run_id for approval in approvals)
        ),
        preview_artifact_id=(preview_artifact.id if preview_artifact is not None else None),
    )


__all__ = ["CheckpointEvidence", "collect_checkpoint_evidence"]
