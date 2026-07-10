from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID


class FileSystemWorkspaceProvisioner:
    """Development adapter; production desktop provisioning is delegated to Rust."""

    def __init__(self, managed_root: Path) -> None:
        self.managed_root = managed_root.resolve(strict=False)
        self.managed_root.mkdir(parents=True, exist_ok=True)

    def project_versions_root(self, project_id: UUID) -> Path:
        return self.managed_root / "projects" / str(project_id) / "versions"

    def version_path(self, project_id: UUID, version_id: UUID) -> Path:
        return self.project_versions_root(project_id) / str(version_id)

    def create_initial_version(self, project_id: UUID, version_id: UUID) -> Path:
        target = self.version_path(project_id, version_id)
        target.mkdir(parents=True, exist_ok=False)
        return target.resolve(strict=True)

    def fork_version(
        self,
        *,
        project_id: UUID,
        version_id: UUID,
        parent_root: Path,
    ) -> Path:
        target = self.version_path(project_id, version_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(parent_root, target)
        return target.resolve(strict=True)

    def scratch_path(self, conversation_id: UUID, task_id: UUID) -> Path:
        return self.managed_root / "scratch" / str(conversation_id) / str(task_id)

    def create_scratch(self, conversation_id: UUID, task_id: UUID) -> Path:
        target = self.scratch_path(conversation_id, task_id)
        target.mkdir(parents=True, exist_ok=False)
        return target.resolve(strict=True)
