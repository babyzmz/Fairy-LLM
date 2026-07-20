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
from fairy_core.workspace.object_store import (
    AssetMutation,
    FileSystemWorkspaceObjectStore,
    WorkspaceObject,
    WorkspaceStoragePolicy,
)
from fairy_core.workspace.read_stream import FileReadSession, LoopbackFileReadServer


class FileSystemWorkspaceProvisioner:
    """Development adapter; production desktop provisioning is delegated to Rust."""

    def __init__(self, managed_root: Path) -> None:
        self.managed_root = managed_root.resolve(strict=False)
        self.managed_root.mkdir(parents=True, exist_ok=True)
        self._locks_guard = threading.Lock()
        self._version_locks: dict[tuple[str, str], threading.RLock] = {}
        self._lifecycle_lock = threading.RLock()
        self._read_server = LoopbackFileReadServer()

    def workspace_size(self, workspace_id: UUID | str) -> int:
        segment = _workspace_segment(workspace_id)
        with self._lifecycle_lock:
            staging = self.managed_root / ".purge-staging" / segment
            if staging.exists() or staging.is_symlink():
                return _tree_bytes(staging)
            return sum(_tree_bytes(root) for root in self._workspace_roots(segment))

    def purge_workspace(self, workspace_id: UUID | str) -> int:
        segment = _workspace_segment(workspace_id)
        with self._lifecycle_lock:
            staging = self.managed_root / ".purge-staging" / segment
            if staging.exists() or staging.is_symlink():
                released = _tree_bytes(staging)
                shutil.rmtree(staging)
                return released

            sources = tuple(
                (label, root)
                for label, root in zip(
                    ("project", "scratch"),
                    self._workspace_roots(segment),
                    strict=True,
                )
                if root.exists() or root.is_symlink()
            )
            released = sum(_tree_bytes(root) for _label, root in sources)
            if not sources:
                return 0

            staging.mkdir(parents=True, exist_ok=False)
            moved: list[tuple[Path, Path]] = []
            try:
                for label, source in sources:
                    destination = staging / label
                    source.replace(destination)
                    moved.append((source, destination))
            except BaseException:
                for source, destination in reversed(moved):
                    if destination.exists() and not source.exists():
                        source.parent.mkdir(parents=True, exist_ok=True)
                        destination.replace(source)
                shutil.rmtree(staging, ignore_errors=True)
                raise

            # A failed removal intentionally leaves the stable staging path for retry.
            shutil.rmtree(staging)
            return released

    def _workspace_roots(self, segment: str) -> tuple[Path, Path]:
        return (
            self.managed_root / "projects" / segment,
            self.managed_root / "scratch" / segment,
        )

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

    def import_asset(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        mutation: AssetMutation,
        max_file_bytes: int,
        max_workspace_bytes: int,
    ) -> WorkspaceObject:
        with self._version_lock(project_id, version_id):
            root = self.version_path(project_id, version_id).resolve(strict=True)
            guard = PathGuard(project_root=root, allowed_roots=(root,), forbidden_roots=())
            target = guard.validate_write(mutation.path)
            if mutation.operation == "create" and target.exists():
                raise FileExistsError(mutation.path)
            if mutation.operation == "update":
                if not target.is_file():
                    raise FileNotFoundError(mutation.path)
                target_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                if target_hash != mutation.expected_target_hash:
                    raise ValueError("AssetMutation target digest changed")
            workspace_bytes = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
            object_store = FileSystemWorkspaceObjectStore(
                self.managed_root,
                policy=WorkspaceStoragePolicy(
                    max_file_bytes=max_file_bytes,
                    max_workspace_bytes=max_workspace_bytes,
                ),
            )
            workspace_object = object_store.put_file(
                mutation.source,
                expected_hash=mutation.expected_source_hash,
                workspace_bytes=workspace_bytes,
            )
            object_store.materialize(workspace_object, target)
            return workspace_object

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
    ) -> FileReadSession:
        root = self.version_path(workspace_id, version_id).resolve(strict=True)
        guard = PathGuard(project_root=root, allowed_roots=(root,), forbidden_roots=())
        lease = guard.issue_read_lease(relative_path)
        source = guard.revalidate_read_lease(lease)
        return self._read_server.open(
            session_id=session_id,
            workspace_id=workspace_id,
            version_id=version_id,
            path=relative_path,
            source=source,
            content_hash=content_hash,
            byte_length=byte_length,
            media_type=media_type,
            expires_seconds=expires_seconds,
        )

    def revoke_read_session(self, session_id: UUID) -> None:
        self._read_server.revoke(session_id)

    def close(self) -> None:
        self._read_server.close()


def _workspace_segment(workspace_id: UUID | str) -> str:
    value = str(workspace_id)
    valid = all(
        character.isascii() and (character.isalnum() or character in "-_") for character in value
    )
    if not value or not valid:
        raise ValueError("workspace_id contains invalid path characters")
    return value


def _tree_bytes(root: Path) -> int:
    if root.is_symlink():
        raise ValueError(f"workspace contains a symbolic link: {root}")
    if not root.exists():
        return 0
    if not root.is_dir():
        raise ValueError(f"workspace root is not a directory: {root}")
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in directory.iterdir():
            if entry.is_symlink():
                raise ValueError(f"workspace contains a symbolic link: {entry}")
            if entry.is_dir():
                pending.append(entry)
            elif entry.is_file():
                total += entry.stat().st_size
    return total
