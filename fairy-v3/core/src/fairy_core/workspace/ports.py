from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

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
