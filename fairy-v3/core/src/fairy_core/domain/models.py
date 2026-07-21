from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError, VersionConflictError
from fairy_core.domain.ids import new_id

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


@dataclass(slots=True)
class Workspace:
    id: UUID
    active_version_id: UUID | None = None
    active_preview_id: UUID | None = None
    revision: int = 0
    max_files: int = 200
    max_bytes: int = 50 * 1024 * 1024 * 1024
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @classmethod
    def create(cls, *, workspace_id: UUID | None = None) -> Workspace:
        return cls(id=workspace_id or new_id())

    def accept_version(self, version_id: UUID, *, expected_revision: int) -> None:
        if expected_revision != self.revision:
            raise VersionConflictError(
                f"expected workspace revision {expected_revision}, "
                f"current revision is {self.revision}"
            )
        self.active_version_id = version_id
        self.revision += 1
        self.updated_at = _now()


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
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.INSTALLING,
            TaskStatus.PREVIEWING,
            TaskStatus.REVIEWING,
            TaskStatus.REJECTED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.INSTALLING: frozenset(
        {
            TaskStatus.EXECUTING,
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
    TaskStatus.FAILED: frozenset({TaskStatus.REPAIRING, TaskStatus.REJECTED, TaskStatus.ARCHIVED}),
}


@dataclass(slots=True)
class Project:
    id: UUID
    name: str
    residency: ProjectResidency
    workspace_id: UUID = field(default_factory=new_id)
    active_version_id: UUID | None = None
    active_preview_id: UUID | None = None
    revision: int = 0
    pinned_at: datetime | None = None
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    purged_at: datetime | None = None
    metadata_revision: int = 0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @classmethod
    def create(cls, *, name: str, residency: ProjectResidency) -> Project:
        normalized = name.strip()
        if not normalized:
            raise ValueError("project name is required")
        if len(normalized) > 255:
            raise ValueError("project name must contain at most 255 characters")
        project_id = new_id()
        return cls(
            id=project_id,
            name=normalized,
            residency=residency,
            workspace_id=project_id,
        )

    def update_metadata(
        self,
        *,
        name: str | None,
        pinned: bool | None,
        expected_revision: int,
    ) -> None:
        self._require_metadata_revision(expected_revision)
        self._require_recoverable()
        if name is not None:
            normalized = name.strip()
            if not normalized or len(normalized) > 255:
                raise ValueError("project name must contain 1 to 255 characters")
            self.name = normalized
        if pinned is not None:
            self.pinned_at = _now() if pinned else None
        self._touch_metadata()

    def archive(self, *, expected_revision: int) -> None:
        self._require_metadata_revision(expected_revision)
        self._require_recoverable()
        if self.archived_at is not None:
            raise InvalidTransitionError("Project is already archived")
        self.archived_at = _now()
        self.pinned_at = None
        self._touch_metadata()

    def restore_archive(self, *, expected_revision: int) -> None:
        self._require_metadata_revision(expected_revision)
        self._require_recoverable()
        if self.archived_at is None:
            raise InvalidTransitionError("Project is not archived")
        self.archived_at = None
        self._touch_metadata()

    def delete(self, *, expected_revision: int) -> None:
        self._require_metadata_revision(expected_revision)
        self._require_recoverable()
        self.deleted_at = _now()
        self.pinned_at = None
        self._touch_metadata(at=self.deleted_at)

    def restore_deleted(self, *, expected_revision: int) -> None:
        self._require_metadata_revision(expected_revision)
        if self.purged_at is not None:
            raise InvalidTransitionError("Purged Project cannot be restored")
        if self.deleted_at is None:
            raise InvalidTransitionError("Project is not deleted")
        self.deleted_at = None
        self._touch_metadata()

    def mark_purged(self, *, expected_revision: int) -> None:
        self._require_metadata_revision(expected_revision)
        if self.deleted_at is None:
            raise InvalidTransitionError("Project must be deleted before it is purged")
        if self.purged_at is not None:
            raise InvalidTransitionError("Project is already purged")
        self.purged_at = _now()
        self.name = "Deleted project"
        self._touch_metadata(at=self.purged_at)

    def _require_metadata_revision(self, expected_revision: int) -> None:
        if expected_revision != self.metadata_revision:
            raise VersionConflictError("Project metadata changed concurrently")

    def _require_recoverable(self) -> None:
        if self.purged_at is not None:
            raise InvalidTransitionError("Purged Project cannot be updated")
        if self.deleted_at is not None:
            raise InvalidTransitionError("Deleted Project cannot be updated")

    def _touch_metadata(self, *, at: datetime | None = None) -> None:
        self.metadata_revision += 1
        self.updated_at = at or _now()

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
    workspace_id: UUID | None = None
    active_draft_version_id: UUID | None = None
    active_task_id: UUID | None = None
    active_preview_id: UUID | None = None
    title: str = "New conversation"
    pinned_at: datetime | None = None
    deleted_at: datetime | None = None
    deleted_by_project_at: datetime | None = None
    purged_at: datetime | None = None
    revision: int = 0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.workspace_id is None:
            self.workspace_id = self.project_id or self.id

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID | None,
        workspace_type: WorkspaceType,
        base_version_id: UUID | None,
        workspace_id: UUID | None = None,
        title: str = "New conversation",
    ) -> Conversation:
        if workspace_type is WorkspaceType.PROJECT_CHAT and project_id is None:
            raise ValueError("project_chat requires project_id")
        if workspace_type is WorkspaceType.CHAT_SCRATCH and project_id is not None:
            raise ValueError("chat_scratch cannot bind project_id")
        normalized_title = title.strip()
        if not normalized_title or len(normalized_title) > 200:
            raise ValueError("Conversation title must contain 1 to 200 characters")
        return cls(
            id=new_id(),
            project_id=project_id,
            workspace_type=workspace_type,
            base_version_id=base_version_id,
            workspace_id=workspace_id,
            title=normalized_title,
        )

    def update_metadata(
        self,
        *,
        title: str | None,
        pinned: bool | None,
        expected_revision: int,
    ) -> None:
        if expected_revision != self.revision:
            raise VersionConflictError("Conversation metadata changed concurrently")
        if self.purged_at is not None:
            raise InvalidTransitionError("purged Conversation cannot be updated")
        if self.deleted_at is not None:
            raise InvalidTransitionError("deleted Conversation cannot be updated")
        if title is not None:
            normalized = title.strip()
            if not normalized or len(normalized) > 200:
                raise ValueError("Conversation title must contain 1 to 200 characters")
            self.title = normalized
        if pinned is not None:
            self.pinned_at = _now() if pinned else None
        self.revision += 1
        self.updated_at = _now()

    def delete(self, *, expected_revision: int, by_project: bool = False) -> None:
        if expected_revision != self.revision:
            raise VersionConflictError("Conversation metadata changed concurrently")
        if self.deleted_at is not None:
            raise InvalidTransitionError("Conversation is already deleted")
        if self.purged_at is not None:
            raise InvalidTransitionError("Purged Conversation cannot be deleted")
        self.deleted_at = _now()
        self.deleted_by_project_at = self.deleted_at if by_project else None
        self.pinned_at = None
        self.revision += 1
        self.updated_at = self.deleted_at

    def restore_deleted(self, *, expected_revision: int) -> None:
        if expected_revision != self.revision:
            raise VersionConflictError("Conversation metadata changed concurrently")
        if self.purged_at is not None:
            raise InvalidTransitionError("Purged Conversation cannot be restored")
        if self.deleted_at is None:
            raise InvalidTransitionError("Conversation is not deleted")
        self.deleted_at = None
        self.deleted_by_project_at = None
        self.revision += 1
        self.updated_at = _now()

    def mark_purged(self, *, expected_revision: int) -> None:
        if expected_revision != self.revision:
            raise VersionConflictError("Conversation metadata changed concurrently")
        if self.deleted_at is None:
            raise InvalidTransitionError("Conversation must be deleted before it is purged")
        if self.purged_at is not None:
            raise InvalidTransitionError("Conversation is already purged")
        self.purged_at = _now()
        self.title = "Deleted chat"
        self.revision += 1
        self.updated_at = self.purged_at


@dataclass(slots=True)
class Task:
    id: UUID
    project_id: UUID | None
    conversation_id: UUID
    user_request: str
    operation_mode: OperationMode
    base_version_id: UUID | None
    execution_target: str
    workspace_id: UUID | None = None
    target_version_id: UUID | None = None
    memory_snapshot_id: UUID | None = None
    memory_snapshot_hash: str | None = None
    knowledge_snapshot_id: UUID | None = None
    knowledge_snapshot_hash: str | None = None
    harness_manifest_id: UUID | None = None
    harness_manifest_hash: str | None = None
    status: TaskStatus = TaskStatus.CREATED
    display_title: str = ""
    pinned_at: datetime | None = None
    metadata_revision: int = 0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.workspace_id is None:
            self.workspace_id = self.project_id or self.conversation_id
        if not self.display_title:
            self.display_title = self.user_request[:200]
        if (self.memory_snapshot_id is None) != (self.memory_snapshot_hash is None):
            raise ValueError("memory Snapshot ID and hash must be both present")
        self._validate_binding(
            self.memory_snapshot_id,
            self.memory_snapshot_hash,
            "memory Snapshot",
        )
        self._validate_binding(
            self.knowledge_snapshot_id,
            self.knowledge_snapshot_hash,
            "Knowledge Snapshot",
        )
        self._validate_binding(
            self.harness_manifest_id,
            self.harness_manifest_hash,
            "Harness Manifest",
        )

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
        workspace_id: UUID | None = None,
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
            workspace_id=workspace_id,
        )

    def bind_target_version(self, version_id: UUID) -> None:
        if self.target_version_id is not None and self.target_version_id != version_id:
            raise ValueError("target version is already bound")
        self.target_version_id = version_id
        self.updated_at = _now()

    def bind_memory_snapshot(self, snapshot_id: UUID, content_hash: str) -> None:
        if _SHA256_PATTERN.fullmatch(content_hash) is None:
            raise ValueError("content_hash must be a lowercase SHA-256 hex digest")
        if self.memory_snapshot_id is None and self.memory_snapshot_hash is None:
            self.memory_snapshot_id = snapshot_id
            self.memory_snapshot_hash = content_hash
            self.updated_at = _now()
            return
        if self.memory_snapshot_id == snapshot_id and self.memory_snapshot_hash == content_hash:
            return
        raise InvalidTransitionError("memory Snapshot is already bound")

    def bind_knowledge_snapshot(self, snapshot_id: UUID, content_hash: str) -> None:
        if _SHA256_PATTERN.fullmatch(content_hash) is None:
            raise ValueError("content_hash must be a lowercase SHA-256 hex digest")
        if self.knowledge_snapshot_id is None and self.knowledge_snapshot_hash is None:
            self.knowledge_snapshot_id = snapshot_id
            self.knowledge_snapshot_hash = content_hash
            self.updated_at = _now()
            return
        if (
            self.knowledge_snapshot_id == snapshot_id
            and self.knowledge_snapshot_hash == content_hash
        ):
            return
        raise InvalidTransitionError("Knowledge Snapshot is already bound")

    def bind_harness_manifest(self, manifest_id: UUID, content_hash: str) -> None:
        if _SHA256_PATTERN.fullmatch(content_hash) is None:
            raise ValueError("content_hash must be a lowercase SHA-256 hex digest")
        if self.harness_manifest_id is None and self.harness_manifest_hash is None:
            self.harness_manifest_id = manifest_id
            self.harness_manifest_hash = content_hash
            self.updated_at = _now()
            return
        if self.harness_manifest_id == manifest_id and self.harness_manifest_hash == content_hash:
            return
        raise InvalidTransitionError("Harness Manifest is already bound")

    @staticmethod
    def _validate_binding(
        identity: UUID | None,
        content_hash: str | None,
        label: str,
    ) -> None:
        if (identity is None) != (content_hash is None):
            raise ValueError(f"{label} ID and hash must be both present")
        if content_hash is not None and _SHA256_PATTERN.fullmatch(content_hash) is None:
            raise ValueError(f"{label} hash must be a lowercase SHA-256 hex digest")

    def transition_to(self, status: TaskStatus) -> None:
        if status not in _TASK_TRANSITIONS[self.status]:
            raise InvalidTransitionError(f"cannot transition Task from {self.status} to {status}")
        self.status = status
        self.updated_at = _now()

    def update_metadata(
        self,
        *,
        display_title: str | None,
        pinned: bool | None,
        expected_revision: int,
    ) -> None:
        if expected_revision != self.metadata_revision:
            raise VersionConflictError("Task metadata changed concurrently")
        if display_title is not None:
            normalized = display_title.strip()
            if not normalized or len(normalized) > 200:
                raise ValueError("Task display title must contain 1 to 200 characters")
            self.display_title = normalized
        if pinned is not None:
            self.pinned_at = _now() if pinned else None
        self.metadata_revision += 1
        self.updated_at = _now()

    def archive(self, *, expected_revision: int) -> None:
        if expected_revision != self.metadata_revision:
            raise VersionConflictError("Task metadata changed concurrently")
        self.transition_to(TaskStatus.ARCHIVED)
        self.pinned_at = None
        self.metadata_revision += 1


@dataclass(slots=True)
class Version:
    id: UUID
    project_id: UUID | None
    source_conversation_id: UUID | None
    source_task_id: UUID | None
    parent_version_id: UUID | None
    project_root: Path
    visibility: VersionVisibility
    workspace_id: UUID | None = None
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.workspace_id is None:
            self.workspace_id = self.project_id or self.source_conversation_id or self.id

    @classmethod
    def create(
        cls,
        *,
        version_id: UUID | None = None,
        project_id: UUID | None,
        source_conversation_id: UUID | None,
        source_task_id: UUID | None,
        parent_version_id: UUID | None,
        project_root: Path,
        visibility: VersionVisibility,
        workspace_id: UUID | None = None,
    ) -> Version:
        return cls(
            id=version_id or new_id(),
            project_id=project_id,
            source_conversation_id=source_conversation_id,
            source_task_id=source_task_id,
            parent_version_id=parent_version_id,
            project_root=project_root.resolve(strict=False),
            visibility=visibility,
            workspace_id=workspace_id,
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
    memory_snapshot_id: UUID | None
    memory_snapshot_hash: str | None
    knowledge_snapshot_id: UUID | None
    knowledge_snapshot_hash: str | None
    workspace_id: UUID
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
        memory_snapshot_id: UUID | None = None,
        memory_snapshot_hash: str | None = None,
        knowledge_snapshot_id: UUID | None = None,
        knowledge_snapshot_hash: str | None = None,
        workspace_id: UUID | None = None,
    ) -> ScopeContract:
        if (memory_snapshot_id is None) != (memory_snapshot_hash is None):
            raise ValueError("memory_snapshot_id and memory_snapshot_hash must be both present")
        if (
            memory_snapshot_hash is not None
            and _SHA256_PATTERN.fullmatch(memory_snapshot_hash) is None
        ):
            raise ValueError("memory_snapshot_hash must be a lowercase SHA-256 hex digest")
        if (knowledge_snapshot_id is None) != (knowledge_snapshot_hash is None):
            raise ValueError(
                "knowledge_snapshot_id and knowledge_snapshot_hash must be both present"
            )
        if (
            knowledge_snapshot_hash is not None
            and _SHA256_PATTERN.fullmatch(knowledge_snapshot_hash) is None
        ):
            raise ValueError("knowledge_snapshot_hash must be a lowercase SHA-256 hex digest")
        root = project_root.resolve(strict=False)
        allowed = tuple(path.resolve(strict=False) for path in allowed_write_paths)
        forbidden = tuple(path.resolve(strict=False) for path in forbidden_write_paths)
        resolved_workspace_id = workspace_id or project_id or conversation_id
        digest_payload = {
            "workspace_type": workspace_type.value,
            "workspace_id": str(resolved_workspace_id),
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
            "memory_snapshot_id": (
                str(memory_snapshot_id) if memory_snapshot_id is not None else None
            ),
            "memory_snapshot_hash": memory_snapshot_hash,
            "knowledge_snapshot_id": (
                str(knowledge_snapshot_id) if knowledge_snapshot_id is not None else None
            ),
            "knowledge_snapshot_hash": knowledge_snapshot_hash,
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
            memory_snapshot_id=memory_snapshot_id,
            memory_snapshot_hash=memory_snapshot_hash,
            knowledge_snapshot_id=knowledge_snapshot_id,
            knowledge_snapshot_hash=knowledge_snapshot_hash,
            workspace_id=resolved_workspace_id,
            scope_digest=hashlib.sha256(encoded).hexdigest(),
        )
