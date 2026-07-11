from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from fairy_core.workspace.models import ProjectIndex, TaskWorkspace

WorkspaceId = UUID | str


class WorkspaceProvisioner(Protocol):
    def version_path(self, project_id: WorkspaceId, version_id: WorkspaceId) -> Path: ...

    def create_initial_version(
        self,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        *,
        source: Path | None = None,
    ) -> Path: ...

    def fork_version(
        self,
        *,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        parent_version_id: WorkspaceId,
    ) -> Path: ...

    def create_scratch(self, conversation_id: WorkspaceId, task_id: WorkspaceId) -> Path: ...

    def scratch_path(self, conversation_id: WorkspaceId, task_id: WorkspaceId) -> Path: ...

    def write_text(
        self,
        *,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        relative_path: str,
        content: str,
    ) -> Path: ...

    def apply_changeset(
        self,
        *,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        mutations: tuple[tuple[str, str], ...],
    ) -> tuple[Path, ...]: ...

    def diff(self, *, project_id: WorkspaceId, version_id: WorkspaceId) -> str: ...

    def checkpoint(
        self,
        *,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        message: str,
    ) -> str: ...

    def discard_version(
        self,
        *,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
    ) -> None: ...


class WorkspaceRepository(Protocol):
    def bind_once(
        self,
        *,
        task_id: WorkspaceId,
        project_id: WorkspaceId | None,
        conversation_id: WorkspaceId,
        version_id: WorkspaceId | None,
        root: Path,
        editable_files: tuple[str, ...],
        reference_files: tuple[str, ...],
        constraints: dict[str, Any],
    ) -> TaskWorkspace: ...

    def get(self, task_id: WorkspaceId) -> TaskWorkspace | None: ...


class ProjectIndexRepository(Protocol):
    def get(self, version_id: WorkspaceId) -> ProjectIndex | None: ...

    def replace_generation(
        self,
        index: ProjectIndex,
        *,
        expected_generation: int,
    ) -> ProjectIndex: ...
