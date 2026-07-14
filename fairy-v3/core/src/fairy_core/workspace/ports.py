from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from fairy_core.workspace.models import ProjectIndex, TaskWorkspace
from fairy_core.workspace.object_store import AssetMutation, WorkspaceObject
from fairy_core.workspace.read_stream import FileReadSession

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

    def import_asset(
        self,
        *,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        mutation: AssetMutation,
        max_file_bytes: int,
        max_workspace_bytes: int,
    ) -> WorkspaceObject: ...

    def open_read_session(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        version_id: UUID,
        relative_path: str,
        content_hash: str,
        byte_length: int,
        media_type: str,
        expires_seconds: int,
    ) -> FileReadSession: ...

    def revoke_read_session(self, session_id: UUID) -> None: ...


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
