from __future__ import annotations

import hashlib
import shutil
import threading
from pathlib import Path
from uuid import UUID

from fairy_core.security.path_guard import PathGuard
from fairy_core.workspace.file_transaction import (
    FileChangesetTransaction,
    atomic_write_scoped,
    unlink_scoped,
)
from fairy_core.workspace.mutations import decode_mutation


class FileSystemWorkspaceProvisioner:
    """Development adapter; production desktop provisioning is delegated to Rust."""

    def __init__(self, managed_root: Path) -> None:
        self.managed_root = managed_root.resolve(strict=False)
        self.managed_root.mkdir(parents=True, exist_ok=True)
        self._locks_guard = threading.Lock()
        self._version_locks: dict[tuple[str, str], threading.RLock] = {}

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
        with self._version_lock(project_id, version_id):
            root = self.version_path(project_id, version_id).resolve(strict=True)
            guard = PathGuard(
                project_root=root,
                allowed_roots=(root,),
                forbidden_roots=(),
            )
            staging_root = (
                self.managed_root / ".transactions" / "single-writes" / str(threading.get_ident())
            )
            try:
                return atomic_write_scoped(
                    guard=guard,
                    relative_path=relative_path,
                    content=content.encode("utf-8"),
                    staging_root=staging_root,
                )
            finally:
                shutil.rmtree(staging_root, ignore_errors=True)

    def apply_changeset(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        mutations: tuple[tuple[str, str], ...],
    ) -> tuple[Path, ...]:
        lock = self._version_lock(project_id, version_id)
        with lock:
            root = self.version_path(project_id, version_id).resolve(strict=True)
            guard = PathGuard(
                project_root=root,
                allowed_roots=(root,),
                forbidden_roots=(),
            )
            transaction_root = (
                self.managed_root
                / ".transactions"
                / "changesets"
                / str(project_id)
                / str(version_id)
                / "current"
            )
            FileChangesetTransaction.recover(transaction_root, guard=guard)
            decoded = tuple(decode_mutation(path, patch) for path, patch in mutations)
            for mutation in decoded:
                target = guard.validate_write(mutation.path)
                current = target.read_bytes() if target.is_file() else None
                destination_exists = (
                    guard.validate_write(mutation.destination_path).exists()
                    if mutation.destination_path is not None
                    else False
                )
                mutation.validate_current(current, destination_exists=destination_exists)
            transaction = FileChangesetTransaction.prepare(
                transaction_root,
                guard=guard,
                relative_paths=tuple(
                    path for mutation in decoded for path in mutation.affected_paths
                ),
            )
            staging_root = transaction_root / "writes"
            try:
                changed: list[Path] = []
                for mutation in decoded:
                    if mutation.operation.value in {"upsert", "create", "update"}:
                        assert mutation.content is not None
                        if (
                            mutation.operation.value == "upsert"
                            and mutation.expected_hash is None
                            and mutation.expected_workspace_revision is None
                        ):
                            changed.append(
                                self.write_text(
                                    project_id=project_id,
                                    version_id=version_id,
                                    relative_path=mutation.path,
                                    content=mutation.content.decode("utf-8"),
                                )
                            )
                        else:
                            changed.append(
                                atomic_write_scoped(
                                    guard=guard,
                                    relative_path=mutation.path,
                                    content=mutation.content,
                                    staging_root=staging_root,
                                )
                            )
                    elif mutation.operation.value == "delete":
                        unlink_scoped(guard=guard, relative_path=mutation.path)
                        changed.append(root / mutation.path)
                    else:
                        assert mutation.destination_path is not None
                        source = guard.validate_write(mutation.path)
                        content = source.read_bytes()
                        changed.append(
                            atomic_write_scoped(
                                guard=guard,
                                relative_path=mutation.destination_path,
                                content=content,
                                staging_root=staging_root,
                            )
                        )
                        unlink_scoped(guard=guard, relative_path=mutation.path)
                        changed.append(root / mutation.path)
                written = tuple(changed)
                transaction.mark_applied()
            except BaseException as error:
                try:
                    transaction.rollback()
                except Exception as rollback_error:
                    raise RuntimeError(
                        f"changeset write and rollback both failed: {rollback_error}"
                    ) from error
                transaction.cleanup(ignore_errors=True)
                raise
            transaction.cleanup(ignore_errors=True)
            return written

    def _version_lock(
        self,
        project_id: UUID | str,
        version_id: UUID | str,
    ) -> threading.RLock:
        key = str(project_id), str(version_id)
        with self._locks_guard:
            return self._version_locks.setdefault(key, threading.RLock())

    def diff(self, *, project_id: UUID | str, version_id: UUID | str) -> str:
        with self._version_lock(project_id, version_id):
            root = self.version_path(project_id, version_id).resolve(strict=True)
            return "\n".join(
                path.relative_to(root).as_posix()
                for path in sorted(root.rglob("*"))
                if path.is_file()
            )

    def checkpoint(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        message: str,
    ) -> str:
        with self._version_lock(project_id, version_id):
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
        with self._version_lock(project_id, version_id):
            target = self.version_path(project_id, version_id)
            if target.exists():
                shutil.rmtree(target)
