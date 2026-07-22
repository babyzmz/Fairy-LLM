from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.execution import (
    Approval,
    ApprovalDecision,
    Artifact,
    ArtifactType,
    ArtifactVisibility,
    Changeset,
    ChangesetStatus,
    Checkpoint,
    MemoryEntry,
    MemoryScope,
    PreviewHealth,
    PreviewSession,
    PreviewStatus,
    PreviewVisibility,
    RuntimeHealth,
    RuntimeKind,
    RuntimeSession,
    RuntimeStatus,
    Workspace,
)
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import OperationMode, ScopeContract, WorkspaceType


def _scope_ids() -> dict[str, object]:
    return {
        "project_id": new_id(),
        "conversation_id": new_id(),
        "task_id": new_id(),
        "version_id": new_id(),
    }


def _scope(tmp_path: Path) -> ScopeContract:
    ids = _scope_ids()
    return ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=ids["project_id"],
        conversation_id=ids["conversation_id"],
        task_id=ids["task_id"],
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=ids["version_id"],
        target_version_id=ids["version_id"],
        project_root=tmp_path,
        allowed_write_paths=(tmp_path,),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="off",
        memory_read_scope=("project_canonical",),
        memory_write_scope=("conversation_draft",),
    )


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
        workspace_id=ids["project_id"],
        files=("src/app.ts",),
        patches=("@@ -1 +1 @@",),
        reason="Add pricing section",
        risk_level="medium",
        idempotency_key="changeset:domain",
    )

    with pytest.raises(InvalidTransitionError):
        changeset.transition_to(ChangesetStatus.APPLYING)

    changeset.transition_to(ChangesetStatus.AWAITING_APPROVAL)
    changeset.record_approval(ApprovalDecision.APPROVED)
    changeset.transition_to(ChangesetStatus.APPLYING)
    changeset.transition_to(ChangesetStatus.APPLIED)

    assert changeset.status is ChangesetStatus.APPLIED


def test_runtime_and_preview_bind_all_scope_ids(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    runtime = RuntimeSession.create(
        scope=scope,
        kind=RuntimeKind.STATIC_SITE,
        executor="rust_local_worker",
        idempotency_key="runtime:domain",
    )
    runtime.begin_start()
    runtime.mark_running(executor_handle="static:preview-domain", port=1420)
    preview = PreviewSession.create(
        scope=scope,
        runtime_id=runtime.id,
        visibility=PreviewVisibility.CHAT_DRAFT,
        idempotency_key="preview:domain",
    )
    preview.begin_start()
    preview.mark_ready("http://127.0.0.1:1420/preview-domain/")

    assert preview.runtime_id == runtime.id
    assert preview.task_id == runtime.task_id
    assert preview.version_id == runtime.version_id
    assert runtime.status is RuntimeStatus.RUNNING
    assert runtime.health is RuntimeHealth.HEALTHY
    assert preview.status is PreviewStatus.READY
    assert preview.health is PreviewHealth.HEALTHY
    assert preview.last_accessed_at == preview.created_at

    with pytest.raises(FrozenInstanceError):
        runtime.project_id = new_id()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        runtime.project_root = tmp_path / "rebound"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        preview.runtime_id = new_id()  # type: ignore[misc]


def test_runtime_and_preview_reject_invalid_failure_without_mutation(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    runtime = RuntimeSession.create(
        scope=scope,
        kind=RuntimeKind.STATIC_SITE,
        executor="rust_local_worker",
        idempotency_key="runtime:failure-atomicity",
    )
    runtime.begin_start()
    runtime.mark_running(executor_handle="static:failure-atomicity", port=1420)
    preview = PreviewSession.create(
        scope=scope,
        runtime_id=runtime.id,
        visibility=PreviewVisibility.CHAT_DRAFT,
        idempotency_key="preview:failure-atomicity",
    )
    preview.begin_start()

    runtime_revision = runtime.revision
    preview_revision = preview.revision
    with pytest.raises(ValueError):
        runtime.mark_failed(" ")
    with pytest.raises(ValueError):
        preview.mark_interrupted("")

    assert runtime.status is RuntimeStatus.RUNNING
    assert runtime.revision == runtime_revision
    assert preview.status is PreviewStatus.STARTING
    assert preview.revision == preview_revision


def test_artifact_checkpoint_and_memory_keep_ownership() -> None:
    ids = _scope_ids()
    artifact = Artifact.create(
        **ids,
        artifact_type=ArtifactType.REPORT,
        visibility=ArtifactVisibility.CONVERSATION,
        storage_location="objects/report.json",
        media_type="application/json",
        byte_length=17,
        content_hash="a" * 64,
        metadata={"title": "Review"},
    )
    checkpoint = Checkpoint.create(
        task_id=ids["task_id"],
        version_id=ids["version_id"],
        changed_files=("src/app.ts",),
        command_run_ids=(new_id(),),
        preview_artifact_id=artifact.id,
        evidence_artifact_ids=(artifact.id,),
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
    changeset_id = new_id()
    approval = Approval.create(
        task_id=new_id(),
        command_run_id=new_id(),
        changeset_id=changeset_id,
        requested_by="agent",
        reason="Write two files",
    )

    approval.decide(decision=ApprovalDecision.APPROVED, decided_by="user")

    assert approval.decision is ApprovalDecision.APPROVED
    assert approval.changeset_id == changeset_id
    assert approval.decided_by == "user"
    with pytest.raises(InvalidTransitionError):
        approval.decide(decision=ApprovalDecision.REJECTED, decided_by="user")


def test_approval_links_at_most_one_optional_subject() -> None:
    with pytest.raises(ValueError, match="both"):
        Approval.create(
            task_id=new_id(),
            command_run_id=new_id(),
            changeset_id=new_id(),
            tool_invocation_id=new_id(),
            requested_by="assistant",
            reason="Ambiguous subject",
        )
