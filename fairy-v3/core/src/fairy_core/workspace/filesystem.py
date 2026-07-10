from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from uuid import UUID

from fairy_core.security.path_guard import PathGuard


class FileSystemWorkspaceProvisioner:
    """Development adapter; production desktop provisioning is delegated to Rust."""

    def __init__(self, managed_root: Path) -> None:
        self.managed_root = managed_root.resolve(strict=False)
        self.managed_root.mkdir(parents=True, exist_ok=True)

    def project_versions_root(self, project_id: UUID | str) -> Path:
        return self.managed_root / "projects" / str(project_id) / "versions"

    def version_path(self, project_id: UUID | str, version_id: UUID | str) -> Path:
        return self.project_versions_root(project_id) / str(version_id)

    def create_initial_version(
        self,
        project_id: UUID | str,
        version_id: UUID | str,
        *,
        source: Path | None = None,
    ) -> Path:
        target = self.version_path(project_id, version_id)
        if target.exists():
            return target.resolve(strict=True)
        if source is None:
            target.mkdir(parents=True, exist_ok=False)
        else:
            shutil.copytree(source.resolve(strict=True), target)
        return target.resolve(strict=True)

    def fork_version(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        parent_version_id: UUID | str,
    ) -> Path:
        target = self.version_path(project_id, version_id)
        if target.exists():
            return target.resolve(strict=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        parent_root = self.version_path(project_id, parent_version_id)
        shutil.copytree(parent_root, target)
        return target.resolve(strict=True)

    def scratch_path(self, conversation_id: UUID | str, task_id: UUID | str) -> Path:
        return self.managed_root / "scratch" / str(conversation_id) / str(task_id)

    def create_scratch(self, conversation_id: UUID | str, task_id: UUID | str) -> Path:
        target = self.scratch_path(conversation_id, task_id)
        target.mkdir(parents=True, exist_ok=True)
        return target.resolve(strict=True)

    def write_text(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        relative_path: str,
        content: str,
    ) -> Path:
        root = self.version_path(project_id, version_id).resolve(strict=True)
        target = PathGuard(
            project_root=root,
            allowed_roots=(root,),
            forbidden_roots=(),
        ).validate_write(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def diff(self, *, project_id: UUID | str, version_id: UUID | str) -> str:
        root = self.version_path(project_id, version_id).resolve(strict=True)
        return "\n".join(
            path.relative_to(root).as_posix() for path in sorted(root.rglob("*")) if path.is_file()
        )

    def checkpoint(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        message: str,
    ) -> str:
        root = self.version_path(project_id, version_id).resolve(strict=True)
        digest = hashlib.sha1(usedforsecurity=False)
        digest.update(message.encode("utf-8"))
        for path in sorted(root.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(root).as_posix().encode("utf-8"))
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def discard_version(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
    ) -> None:
        target = self.version_path(project_id, version_id)
        if target.exists():
            shutil.rmtree(target)
