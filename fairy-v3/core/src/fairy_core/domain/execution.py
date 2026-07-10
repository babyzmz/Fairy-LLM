from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Workspace:
    id: UUID
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    project_root: Path
    editable_files: tuple[Path, ...]
    reference_files: tuple[Path, ...]
    constraints: tuple[str, ...]
    allowed_paths: tuple[Path, ...]
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID,
        conversation_id: UUID,
        task_id: UUID,
        version_id: UUID,
        project_root: Path,
        editable_files: tuple[Path, ...],
        reference_files: tuple[Path, ...],
        constraints: tuple[str, ...],
        allowed_paths: tuple[Path, ...],
    ) -> Workspace:
        return cls(
            id=new_id(),
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            project_root=project_root.resolve(strict=False),
            editable_files=editable_files,
            reference_files=reference_files,
            constraints=constraints,
            allowed_paths=tuple(path.resolve(strict=False) for path in allowed_paths),
            created_at=_now(),
        )


class ChangesetStatus(StrEnum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


_CHANGESET_TRANSITIONS: dict[ChangesetStatus, frozenset[ChangesetStatus]] = {
    ChangesetStatus.PROPOSED: frozenset({ChangesetStatus.AWAITING_APPROVAL}),
    ChangesetStatus.AWAITING_APPROVAL: frozenset(
        {ChangesetStatus.APPLYING, ChangesetStatus.REJECTED}
    ),
    ChangesetStatus.APPLYING: frozenset({ChangesetStatus.APPLIED, ChangesetStatus.FAILED}),
    ChangesetStatus.APPLIED: frozenset({ChangesetStatus.ROLLED_BACK}),
    ChangesetStatus.REJECTED: frozenset(),
    ChangesetStatus.FAILED: frozenset({ChangesetStatus.ROLLED_BACK}),
    ChangesetStatus.ROLLED_BACK: frozenset(),
}


@dataclass(slots=True)
class Changeset:
    id: UUID
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    files: tuple[str, ...]
    patches: tuple[str, ...]
    reason: str
    risk_level: str
    idempotency_key: str
    status: ChangesetStatus = ChangesetStatus.PROPOSED
    approval_decision: ApprovalDecision = ApprovalDecision.PENDING
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID,
        conversation_id: UUID,
        task_id: UUID,
        version_id: UUID,
        files: tuple[str, ...],
        patches: tuple[str, ...],
        reason: str,
        risk_level: str,
        idempotency_key: str,
    ) -> Changeset:
        if not files or len(files) != len(patches):
            raise ValueError("changeset requires one patch per file")
        if not idempotency_key.strip():
            raise ValueError("changeset idempotency_key is required")
        return cls(
            id=new_id(),
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            files=files,
            patches=patches,
            reason=reason.strip(),
            risk_level=risk_level,
            idempotency_key=idempotency_key.strip(),
        )

    def record_approval(self, decision: ApprovalDecision) -> None:
        if self.status is not ChangesetStatus.AWAITING_APPROVAL:
            raise InvalidTransitionError("changeset approval is not currently pending")
        if decision is ApprovalDecision.PENDING:
            raise ValueError("approval decision must be terminal")
        self.approval_decision = decision
        self.updated_at = _now()

    def transition_to(self, status: ChangesetStatus) -> None:
        if status not in _CHANGESET_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition Changeset from {self.status} to {status}"
            )
        if (
            status is ChangesetStatus.APPLYING
            and self.approval_decision is not ApprovalDecision.APPROVED
        ):
            raise InvalidTransitionError("approved Changeset required before applying")
        self.status = status
        self.updated_at = _now()


@dataclass(slots=True)
class Approval:
    id: UUID
    task_id: UUID
    command_run_id: UUID
    requested_by: str
    reason: str
    changeset_id: UUID | None = None
    decision: ApprovalDecision = ApprovalDecision.PENDING
    decided_by: str | None = None
    created_at: datetime = field(default_factory=_now)
    decided_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        task_id: UUID,
        command_run_id: UUID,
        requested_by: str,
        reason: str,
        changeset_id: UUID | None = None,
    ) -> Approval:
        return cls(
            id=new_id(),
            task_id=task_id,
            command_run_id=command_run_id,
            requested_by=requested_by,
            reason=reason,
            changeset_id=changeset_id,
        )

    def decide(self, *, decision: ApprovalDecision, decided_by: str) -> None:
        if self.decision is not ApprovalDecision.PENDING:
            raise InvalidTransitionError("approval has already been decided")
        if decision is ApprovalDecision.PENDING:
            raise ValueError("approval decision must be terminal")
        self.decision = decision
        self.decided_by = decided_by
        self.decided_at = _now()


@dataclass(slots=True)
class RuntimeSession:
    id: UUID
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    project_root: Path
    execution_target: str
    process_selectors: tuple[str, ...]
    ports: tuple[int, ...]
    status: str = "not_started"
    created_at: datetime = field(default_factory=_now)

    @classmethod
    def create(cls, **values: Any) -> RuntimeSession:
        return cls(
            id=new_id(),
            project_id=values["project_id"],
            conversation_id=values["conversation_id"],
            task_id=values["task_id"],
            version_id=values["version_id"],
            project_root=Path(values["project_root"]).resolve(strict=False),
            execution_target=values["execution_target"],
            process_selectors=tuple(values["process_selectors"]),
            ports=tuple(values["ports"]),
        )


@dataclass(slots=True)
class PreviewSession:
    id: UUID
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    runtime_id: UUID
    project_root: Path
    url: str
    visibility: str
    status: str = "not_started"
    created_at: datetime = field(default_factory=_now)

    @classmethod
    def create(cls, **values: Any) -> PreviewSession:
        return cls(
            id=new_id(),
            project_id=values["project_id"],
            conversation_id=values["conversation_id"],
            task_id=values["task_id"],
            version_id=values["version_id"],
            runtime_id=values["runtime_id"],
            project_root=Path(values["project_root"]).resolve(strict=False),
            url=values["url"],
            visibility=values["visibility"],
        )


class ArtifactVisibility(StrEnum):
    CONVERSATION = "conversation"
    PROJECT = "project"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class Artifact:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    artifact_type: str
    visibility: ArtifactVisibility
    storage_location: str
    metadata: dict[str, Any]
    created_at: datetime

    @classmethod
    def create(cls, **values: Any) -> Artifact:
        return cls(id=new_id(), created_at=_now(), **values)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    id: UUID
    task_id: UUID
    version_id: UUID
    changed_files: tuple[str, ...]
    command_run_ids: tuple[UUID, ...]
    preview_artifact_id: UUID | None
    created_at: datetime

    @classmethod
    def create(cls, **values: Any) -> Checkpoint:
        return cls(id=new_id(), created_at=_now(), **values)


class MemoryScope(StrEnum):
    PROJECT_CANONICAL = "project_canonical"
    CONVERSATION_DRAFT = "conversation_draft"
    FAILURE_LESSON = "failure_lesson"
    PERSONAL = "personal"


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID | None
    version_id: UUID | None
    scope: MemoryScope
    content: str
    created_at: datetime

    @classmethod
    def create(cls, **values: Any) -> MemoryEntry:
        content = str(values.pop("content")).strip()
        if not content:
            raise ValueError("memory content is required")
        return cls(id=new_id(), content=content, created_at=_now(), **values)
