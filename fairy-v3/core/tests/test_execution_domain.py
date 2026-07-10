from __future__ import annotations

from pathlib import Path

import pytest

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.execution import (
    Approval,
    ApprovalDecision,
    Artifact,
    ArtifactVisibility,
    Changeset,
    ChangesetStatus,
    Checkpoint,
    MemoryEntry,
    MemoryScope,
    PreviewSession,
    RuntimeSession,
    Workspace,
)
from fairy_core.domain.ids import new_id


def _scope_ids() -> dict[str, object]:
    return {
        "project_id": new_id(),
        "conversation_id": new_id(),
        "task_id": new_id(),
        "version_id": new_id(),
    }


def test_workspace_separates_editable_and_reference_files(tmp_path: Path) -> None:
    ids = _scope_ids()
    workspace = Workspace.create(
        **ids,
        project_root=tmp_path,
        editable_files=(Path("src/app.ts"),),
        reference_files=(Path("package.json"),),
        constraints=("Do not edit package.json",),
        allowed_paths=(tmp_path / "src",),
    )

    assert workspace.editable_files == (Path("src/app.ts"),)
    assert workspace.reference_files == (Path("package.json"),)
    assert workspace.allowed_paths == ((tmp_path / "src").resolve(strict=False),)


def test_changeset_cannot_apply_before_approval() -> None:
    ids = _scope_ids()
    changeset = Changeset.create(
        **ids,
        files=("src/app.ts",),
        patches=("@@ -1 +1 @@",),
        reason="Add pricing section",
        risk_level="medium",
    )

    with pytest.raises(InvalidTransitionError):
        changeset.transition_to(ChangesetStatus.APPLYING)

    changeset.transition_to(ChangesetStatus.AWAITING_APPROVAL)
    changeset.record_approval(ApprovalDecision.APPROVED)
    changeset.transition_to(ChangesetStatus.APPLYING)
    changeset.transition_to(ChangesetStatus.APPLIED)

    assert changeset.status is ChangesetStatus.APPLIED


def test_runtime_and_preview_bind_all_scope_ids(tmp_path: Path) -> None:
    ids = _scope_ids()
    runtime = RuntimeSession.create(
        **ids,
        project_root=tmp_path,
        execution_target="local",
        process_selectors=("npm:dev",),
        ports=(1420,),
    )
    preview = PreviewSession.create(
        **ids,
        runtime_id=runtime.id,
        project_root=tmp_path,
        url="http://127.0.0.1:1420",
        visibility="chat_draft",
    )

    assert preview.runtime_id == runtime.id
    assert preview.task_id == runtime.task_id
    assert preview.version_id == runtime.version_id


def test_artifact_checkpoint_and_memory_keep_ownership() -> None:
    ids = _scope_ids()
    artifact = Artifact.create(
        **ids,
        artifact_type="report",
        visibility=ArtifactVisibility.CONVERSATION,
        storage_location="objects/report.json",
        metadata={"title": "Review"},
    )
    checkpoint = Checkpoint.create(
        task_id=ids["task_id"],
        version_id=ids["version_id"],
        changed_files=("src/app.ts",),
        command_run_ids=(new_id(),),
        preview_artifact_id=artifact.id,
    )
    memory = MemoryEntry.create(
        project_id=ids["project_id"],
        conversation_id=ids["conversation_id"],
        version_id=ids["version_id"],
        scope=MemoryScope.CONVERSATION_DRAFT,
        content="The user prefers compact navigation.",
    )

    assert artifact.conversation_id == ids["conversation_id"]
    assert checkpoint.version_id == ids["version_id"]
    assert memory.scope is MemoryScope.CONVERSATION_DRAFT


def test_approval_records_actor_and_decision() -> None:
    approval = Approval.create(
        task_id=new_id(),
        command_run_id=new_id(),
        requested_by="agent",
        reason="Write two files",
    )

    approval.decide(decision=ApprovalDecision.APPROVED, decided_by="user")

    assert approval.decision is ApprovalDecision.APPROVED
    assert approval.decided_by == "user"
    with pytest.raises(InvalidTransitionError):
        approval.decide(decision=ApprovalDecision.REJECTED, decided_by="user")
