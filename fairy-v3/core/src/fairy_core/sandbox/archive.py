from __future__ import annotations

import hashlib
import io
import os
import zipfile
from dataclasses import dataclass

from fairy_core.domain.errors import ScopeViolationError
from fairy_core.domain.models import ScopeContract
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.security.path_guard import PathGuard, ReadLease
from fairy_core.workspace.models import ProjectFile, ProjectIndex, TaskWorkspace

_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_FILES = 20_000
_CHUNK_BYTES = 1024 * 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class WorkspaceArchive:
    content: bytes
    generation: int


class WorkspaceArchiveBuilder:
    def __init__(self, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def build(self, scope: ScopeContract) -> WorkspaceArchive:
        workspace, index = self._context(scope)
        if index is None:
            return WorkspaceArchive(
                content=_scratch_archive(),
                generation=workspace.generation,
            )
        if len(index.files) > _MAX_FILES:
            raise ScopeViolationError(
                "Project Index exceeds the Sandbox file limit",
                code="SCOPE_MISMATCH",
            )
        if not index.files:
            return WorkspaceArchive(
                content=_marker_archive(".fairy-project-empty"),
                generation=index.generation,
            )
        output = io.BytesIO()
        guard = PathGuard(
            project_root=workspace.root,
            allowed_roots=(workspace.root,),
            forbidden_roots=(),
        )
        with zipfile.ZipFile(
            output,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            strict_timestamps=True,
        ) as archive:
            for indexed in index.files:
                self._write_indexed_file(
                    archive=archive,
                    indexed=indexed,
                    guard=guard,
                    output=output,
                )
        content = output.getvalue()
        if not content or len(content) > _MAX_ARCHIVE_BYTES:
            raise ScopeViolationError(
                "managed Workspace archive exceeds the Sandbox limit",
                code="SCOPE_MISMATCH",
            )
        return WorkspaceArchive(content=content, generation=index.generation)

    def _context(
        self,
        scope: ScopeContract,
    ) -> tuple[TaskWorkspace, ProjectIndex | None]:
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(scope.task_id)
            workspace = unit_of_work.workspaces.get(scope.task_id)
            index = (
                unit_of_work.project_indexes.get(scope.target_version_id)
                if scope.target_version_id is not None
                else None
            )
        if (
            task is None
            or workspace is None
            or task.id != scope.task_id
            or task.project_id != scope.project_id
            or task.workspace_id != scope.workspace_id
            or task.conversation_id != scope.conversation_id
            or task.target_version_id != scope.target_version_id
            or workspace.task_id != scope.task_id
            or workspace.project_id != scope.project_id
            or workspace.workspace_id != scope.workspace_id
            or workspace.conversation_id != scope.conversation_id
            or workspace.version_id != scope.target_version_id
        ):
            raise ScopeViolationError(
                "Task Workspace binding does not match Scope",
                code="SCOPE_MISMATCH",
            )
        try:
            scope_root = scope.project_root.resolve(strict=True)
        except OSError as error:
            raise ScopeViolationError(
                "Task Workspace root is unavailable",
                code="SCOPE_MISMATCH",
            ) from error
        if workspace.root != scope_root:
            raise ScopeViolationError(
                "Task Workspace root does not match Scope",
                code="SCOPE_MISMATCH",
            )
        if (
            index is None
            or index.project_id != scope.project_id
            or index.workspace_id != scope.workspace_id
            or index.version_id != scope.target_version_id
        ):
            raise ScopeViolationError(
                "Project Index binding does not match Scope",
                code="SCOPE_MISMATCH",
            )
        return workspace, index

    @staticmethod
    def _write_indexed_file(
        *,
        archive: zipfile.ZipFile,
        indexed: ProjectFile,
        guard: PathGuard,
        output: io.BytesIO,
    ) -> None:
        if indexed.byte_length > _MAX_ARCHIVE_BYTES:
            raise ScopeViolationError(
                f"indexed file exceeds the Sandbox limit: {indexed.path}",
                code="SCOPE_MISMATCH",
            )
        lease = guard.issue_read_lease(indexed.path)
        info = zipfile.ZipInfo(indexed.path, date_time=_ZIP_TIMESTAMP)
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        digest = hashlib.sha256()
        byte_length = 0
        with lease.canonical_path.open("rb") as source:
            _require_open_identity(source.fileno(), lease)
            with archive.open(info, mode="w", force_zip64=True) as destination:
                while chunk := source.read(_CHUNK_BYTES):
                    byte_length += len(chunk)
                    if byte_length > indexed.byte_length:
                        raise ScopeViolationError(
                            f"indexed file grew before Sandbox synchronization: {indexed.path}",
                            code="SCOPE_MISMATCH",
                        )
                    digest.update(chunk)
                    destination.write(chunk)
                    if output.tell() > _MAX_ARCHIVE_BYTES:
                        raise ScopeViolationError(
                            "managed Workspace archive exceeds the Sandbox limit",
                            code="SCOPE_MISMATCH",
                        )
            _require_open_identity(source.fileno(), lease)
        guard.revalidate_read_lease(lease)
        if byte_length != indexed.byte_length or digest.hexdigest() != indexed.content_hash:
            raise ScopeViolationError(
                f"Project Index does not match the managed Version: {indexed.path}",
                code="SCOPE_MISMATCH",
            )


def _require_open_identity(file_descriptor: int, lease: ReadLease) -> None:
    metadata = os.fstat(file_descriptor)
    if (metadata.st_dev, metadata.st_ino) != lease.target_identity:
        raise ScopeViolationError(
            "Sandbox archive source identity changed during authorization",
            code="PATH_IDENTITY_CHANGED",
        )


def _scratch_archive() -> bytes:
    return _marker_archive(".fairy-scratch")


def _marker_archive(name: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
        info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        archive.writestr(info, b"")
    return output.getvalue()


__all__ = ["WorkspaceArchive", "WorkspaceArchiveBuilder"]
