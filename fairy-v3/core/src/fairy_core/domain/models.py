from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError, VersionConflictError
from fairy_core.domain.ids import new_id


def _now() -> datetime:
    return datetime.now(UTC)


class ProjectResidency(StrEnum):
    LOCAL_ONLY = "local_only"
    SYNCED = "synced"


class WorkspaceType(StrEnum):
    PROJECT_CHAT = "project_chat"
    CHAT_SCRATCH = "chat_scratch"


class OperationMode(StrEnum):
    CONTINUE_CURRENT_DRAFT = "continue_current_chat_draft"
    CREATE_NEW_VERSION = "create_new_version"
    ANSWER = "answer"


class VersionVisibility(StrEnum):
    CHAT_DRAFT = "chat_draft"
    PROJECT_CANDIDATE = "project_candidate"
    PROJECT_ACTIVE = "project_active"
    ARCHIVED = "archived"
    REJECTED = "rejected"


class TaskStatus(StrEnum):
    CREATED = "created"
    RESOLVING_SCOPE = "resolving_scope"
    BUILDING_WORKSPACE = "building_workspace"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    INSTALLING = "installing"
    PREVIEWING = "previewing"
    REVIEWING = "reviewing"
    REPAIRING = "repairing"
    READY = "ready"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ARCHIVED = "archived"
    FAILED = "failed"


_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset({TaskStatus.RESOLVING_SCOPE}),
    TaskStatus.RESOLVING_SCOPE: frozenset(
        {TaskStatus.BUILDING_WORKSPACE, TaskStatus.REJECTED, TaskStatus.FAILED}
    ),
    TaskStatus.BUILDING_WORKSPACE: frozenset(
        {TaskStatus.PLANNING, TaskStatus.REJECTED, TaskStatus.FAILED}
    ),
    TaskStatus.PLANNING: frozenset(
        {
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.EXECUTING,
            TaskStatus.REJECTED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.AWAITING_APPROVAL: frozenset(
        {TaskStatus.EXECUTING, TaskStatus.REJECTED, TaskStatus.FAILED}
    ),
    TaskStatus.EXECUTING: frozenset(
        {
            TaskStatus.INSTALLING,
            TaskStatus.PREVIEWING,
            TaskStatus.REVIEWING,
            TaskStatus.REJECTED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.INSTALLING: frozenset(
        {
            TaskStatus.PREVIEWING,
            TaskStatus.REVIEWING,
            TaskStatus.REPAIRING,
            TaskStatus.REJECTED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.PREVIEWING: frozenset(
        {TaskStatus.REVIEWING, TaskStatus.REPAIRING, TaskStatus.REJECTED, TaskStatus.FAILED}
    ),
    TaskStatus.REVIEWING: frozenset(
        {TaskStatus.READY, TaskStatus.REPAIRING, TaskStatus.REJECTED, TaskStatus.FAILED}
    ),
    TaskStatus.REPAIRING: frozenset(
        {
            TaskStatus.EXECUTING,
            TaskStatus.INSTALLING,
            TaskStatus.PREVIEWING,
            TaskStatus.REVIEWING,
            TaskStatus.REJECTED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.READY: frozenset({TaskStatus.ACCEPTED, TaskStatus.REJECTED, TaskStatus.ARCHIVED}),
    TaskStatus.ACCEPTED: frozenset({TaskStatus.ARCHIVED}),
    TaskStatus.REJECTED: frozenset({TaskStatus.ARCHIVED}),
    TaskStatus.ARCHIVED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.REPAIRING, TaskStatus.ARCHIVED}),
}


@dataclass(slots=True)
class Project:
    id: UUID
    name: str
    residency: ProjectResidency
    active_version_id: UUID | None = None
    active_preview_id: UUID | None = None
    revision: int = 0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @classmethod
    def create(cls, *, name: str, residency: ProjectResidency) -> Project:
        normalized = name.strip()
        if not normalized:
            raise ValueError("project name is required")
        return cls(id=new_id(), name=normalized, residency=residency)

    def accept_version(self, version_id: UUID, *, expected_revision: int) -> None:
        if expected_revision != self.revision:
            raise VersionConflictError(
                f"expected project revision {expected_revision}, "
                f"current revision is {self.revision}"
            )
        self.active_version_id = version_id
        self.revision += 1
        self.updated_at = _now()


@dataclass(slots=True)
class Conversation:
    id: UUID
    project_id: UUID | None
    workspace_type: WorkspaceType
    base_version_id: UUID | None
    active_draft_version_id: UUID | None = None
    active_task_id: UUID | None = None
    active_preview_id: UUID | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID | None,
        workspace_type: WorkspaceType,
        base_version_id: UUID | None,
    ) -> Conversation:
        if workspace_type is WorkspaceType.PROJECT_CHAT and project_id is None:
            raise ValueError("project_chat requires project_id")
        if workspace_type is WorkspaceType.CHAT_SCRATCH and project_id is not None:
            raise ValueError("chat_scratch cannot bind project_id")
        return cls(
            id=new_id(),
            project_id=project_id,
            workspace_type=workspace_type,
            base_version_id=base_version_id,
        )


@dataclass(slots=True)
class Task:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    user_request: str
    operation_mode: OperationMode
    base_version_id: UUID | None
    execution_target: str
    target_version_id: UUID | None = None
    status: TaskStatus = TaskStatus.CREATED
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID | None,
        conversation_id: UUID,
        user_request: str,
        operation_mode: OperationMode,
        base_version_id: UUID | None,
        execution_target: str,
    ) -> Task:
        request = user_request.strip()
        if not request:
            raise ValueError("user_request is required")
        if execution_target not in {"local", "cloud"}:
            raise ValueError("execution_target must be local or cloud")
        return cls(
            id=new_id(),
            project_id=project_id,
            conversation_id=conversation_id,
            user_request=request,
            operation_mode=operation_mode,
            base_version_id=base_version_id,
            execution_target=execution_target,
        )

    def bind_target_version(self, version_id: UUID) -> None:
        if self.target_version_id is not None and self.target_version_id != version_id:
            raise ValueError("target version is already bound")
        self.target_version_id = version_id
        self.updated_at = _now()

    def transition_to(self, status: TaskStatus) -> None:
        if status not in _TASK_TRANSITIONS[self.status]:
            raise InvalidTransitionError(f"cannot transition Task from {self.status} to {status}")
        self.status = status
        self.updated_at = _now()


@dataclass(slots=True)
class Version:
    id: UUID
    project_id: UUID
    source_conversation_id: UUID | None
    source_task_id: UUID | None
    parent_version_id: UUID | None
    project_root: Path
    visibility: VersionVisibility
    created_at: datetime = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        *,
        version_id: UUID | None = None,
        project_id: UUID,
        source_conversation_id: UUID | None,
        source_task_id: UUID | None,
        parent_version_id: UUID | None,
        project_root: Path,
        visibility: VersionVisibility,
    ) -> Version:
        return cls(
            id=version_id or new_id(),
            project_id=project_id,
            source_conversation_id=source_conversation_id,
            source_task_id=source_task_id,
            parent_version_id=parent_version_id,
            project_root=project_root.resolve(strict=False),
            visibility=visibility,
        )


@dataclass(frozen=True, slots=True)
class ScopeContract:
    workspace_type: WorkspaceType
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    operation_mode: OperationMode
    base_version_id: UUID | None
    target_version_id: UUID | None
    project_root: Path
    allowed_write_paths: tuple[Path, ...]
    forbidden_write_paths: tuple[Path, ...]
    execution_target: str
    network_policy: str
    memory_read_scope: tuple[str, ...]
    memory_write_scope: tuple[str, ...]
    scope_digest: str

    @classmethod
    def create(
        cls,
        *,
        workspace_type: WorkspaceType,
        project_id: UUID | None,
        conversation_id: UUID,
        task_id: UUID,
        operation_mode: OperationMode,
        base_version_id: UUID | None,
        target_version_id: UUID | None,
        project_root: Path,
        allowed_write_paths: tuple[Path, ...],
        forbidden_write_paths: tuple[Path, ...],
        execution_target: str,
        network_policy: str,
        memory_read_scope: tuple[str, ...],
        memory_write_scope: tuple[str, ...],
    ) -> ScopeContract:
        root = project_root.resolve(strict=False)
        allowed = tuple(path.resolve(strict=False) for path in allowed_write_paths)
        forbidden = tuple(path.resolve(strict=False) for path in forbidden_write_paths)
        digest_payload = {
            "workspace_type": workspace_type.value,
            "project_id": str(project_id) if project_id else None,
            "conversation_id": str(conversation_id),
            "task_id": str(task_id),
            "operation_mode": operation_mode.value,
            "base_version_id": str(base_version_id) if base_version_id else None,
            "target_version_id": str(target_version_id) if target_version_id else None,
            "project_root": str(root),
            "allowed_write_paths": [str(path) for path in allowed],
            "forbidden_write_paths": [str(path) for path in forbidden],
            "execution_target": execution_target,
            "network_policy": network_policy,
            "memory_read_scope": list(memory_read_scope),
            "memory_write_scope": list(memory_write_scope),
        }
        encoded = json.dumps(digest_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        return cls(
            workspace_type=workspace_type,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            operation_mode=operation_mode,
            base_version_id=base_version_id,
            target_version_id=target_version_id,
            project_root=root,
            allowed_write_paths=allowed,
            forbidden_write_paths=forbidden,
            execution_target=execution_target,
            network_policy=network_policy,
            memory_read_scope=memory_read_scope,
            memory_write_scope=memory_write_scope,
            scope_digest=hashlib.sha256(encoded).hexdigest(),
        )
