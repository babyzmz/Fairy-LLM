from __future__ import annotations

import hashlib
import io
import mimetypes
import zipfile
from pathlib import Path
from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import Version, Workspace
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.security.path_guard import PathGuard
from fairy_core.workspace.catalog import FileDescriptor, WorkspaceFileCatalog
from fairy_core.workspace.file_sets import FileSet, FileSetResolver
from fairy_core.workspace.index import ProjectIndexer
from fairy_core.workspace.models import ProjectFile, ProjectIndex
from fairy_core.workspace.ports import WorkspaceProvisioner
from fairy_core.workspace.read_stream import FileReadSession


class WorkspaceApplication:
    def __init__(
        self,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        project_indexer: ProjectIndexer,
        workspace_provisioner: WorkspaceProvisioner,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._project_indexer = project_indexer
        self._workspace_provisioner = workspace_provisioner
        self._file_catalog = WorkspaceFileCatalog()
        self._file_sets = FileSetResolver()

    def get(self, workspace_id: UUID) -> Workspace:
        with self._unit_of_work_factory() as unit_of_work:
            workspace = unit_of_work.state.get_workspace(workspace_id)
        if workspace is None:
            raise KeyError(f"Workspace not found: {workspace_id}")
        return workspace

    def files(
        self,
        *,
        workspace_id: UUID,
        version_id: UUID | None = None,
    ) -> ProjectIndex:
        with self._unit_of_work_factory() as unit_of_work:
            workspace = unit_of_work.state.get_workspace(workspace_id)
            if workspace is None:
                raise KeyError(f"Workspace not found: {workspace_id}")
            selected_version_id = version_id or workspace.active_version_id
            if selected_version_id is None:
                raise KeyError("Workspace has no active Version")
            version = unit_of_work.state.get_version(selected_version_id)
            if version is None:
                raise KeyError(f"Version not found: {selected_version_id}")
            self._require_ownership(version, workspace)
            index = unit_of_work.project_indexes.get(version.id)
            if index is None:
                index = self._project_indexer.build(
                    project_id=version.project_id,
                    workspace_id=workspace.id,
                    version_id=version.id,
                    root=version.project_root,
                    generation=1,
                )
                unit_of_work.project_indexes.replace_generation(index, expected_generation=0)
                unit_of_work.commit()
            return index

    def read_file(
        self,
        *,
        workspace_id: UUID,
        path: str,
        version_id: UUID | None = None,
        inline_only: bool = False,
    ) -> tuple[ProjectFile, bytes | None]:
        index = self.files(workspace_id=workspace_id, version_id=version_id)
        item = index.file(path)
        if inline_only and (
            item.kind not in {"source", "manifest", "config", "text"}
            or item.byte_length > 2 * 1024 * 1024
        ):
            return item, None
        with self._unit_of_work_factory() as unit_of_work:
            version = unit_of_work.state.get_version(index.version_id)
        if version is None:
            raise KeyError(f"Version not found: {index.version_id}")
        guard = PathGuard(
            project_root=version.project_root,
            allowed_roots=(version.project_root,),
            forbidden_roots=(),
        )
        lease = guard.issue_read_lease(item.path)
        authorized = guard.revalidate_read_lease(lease)
        content = authorized.read_bytes()
        if (
            len(content) != item.byte_length
            or hashlib.sha256(content).hexdigest() != item.content_hash
        ):
            raise InvalidTransitionError("Workspace file changed outside its indexed Version")
        return item, content

    def open_read_session(
        self,
        *,
        workspace_id: UUID,
        path: str,
        version_id: UUID | None = None,
        expires_seconds: int = 120,
    ) -> FileReadSession:
        index = self.files(workspace_id=workspace_id, version_id=version_id)
        item = index.file(path)
        with self._unit_of_work_factory() as unit_of_work:
            version = unit_of_work.state.get_version(index.version_id)
        if version is None:
            raise KeyError(f"Version not found: {index.version_id}")
        media_type = mimetypes.guess_type(item.path)[0] or "application/octet-stream"
        return self._workspace_provisioner.open_read_session(
            session_id=new_id(),
            workspace_id=workspace_id,
            version_id=index.version_id,
            relative_path=item.path,
            content_hash=item.content_hash,
            byte_length=item.byte_length,
            media_type=media_type,
            expires_seconds=expires_seconds,
        )

    def probe_file(
        self,
        *,
        workspace_id: UUID,
        path: str,
        version_id: UUID | None = None,
    ) -> FileDescriptor:
        index = self.files(workspace_id=workspace_id, version_id=version_id)
        item = index.file(path)
        with self._unit_of_work_factory() as unit_of_work:
            version = unit_of_work.state.get_version(index.version_id)
        if version is None:
            raise KeyError(f"Version not found: {index.version_id}")
        guard = PathGuard(
            project_root=version.project_root,
            allowed_roots=(version.project_root,),
            forbidden_roots=(),
        )
        lease = guard.issue_read_lease(item.path)
        source = guard.revalidate_read_lease(lease)
        with source.open("rb") as stream:
            prefix = stream.read(64)
            digest = hashlib.sha256(prefix)
            byte_length = len(prefix)
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                byte_length += len(chunk)
        if byte_length != item.byte_length or digest.hexdigest() != item.content_hash:
            raise InvalidTransitionError("Workspace file changed outside its indexed Version")
        return self._file_catalog.describe(item, prefix)

    def resolve_file_set(
        self,
        *,
        workspace_id: UUID,
        path: str,
        version_id: UUID | None = None,
    ) -> FileSet:
        index, root = self._file_set_context(workspace_id, version_id)
        return self._file_sets.resolve(index=index, root=root, primary_path=path)

    def get_file_set(
        self,
        *,
        workspace_id: UUID,
        file_set_id: UUID,
        version_id: UUID | None = None,
    ) -> FileSet:
        index, root = self._file_set_context(workspace_id, version_id)
        return self._file_sets.find(index=index, root=root, file_set_id=file_set_id)

    def _file_set_context(
        self,
        workspace_id: UUID,
        version_id: UUID | None,
    ) -> tuple[ProjectIndex, Path]:
        index = self.files(workspace_id=workspace_id, version_id=version_id)
        with self._unit_of_work_factory() as unit_of_work:
            version = unit_of_work.state.get_version(index.version_id)
        if version is None:
            raise KeyError(f"Version not found: {index.version_id}")
        return index, version.project_root

    def export(
        self,
        *,
        workspace_id: UUID,
        version_id: UUID | None = None,
    ) -> bytes:
        index = self.files(workspace_id=workspace_id, version_id=version_id)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in index.files:
                indexed, content = self.read_file(
                    workspace_id=workspace_id,
                    version_id=index.version_id,
                    path=item.path,
                )
                assert content is not None
                info = zipfile.ZipInfo(indexed.path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, content)
        return stream.getvalue()

    @staticmethod
    def _require_ownership(version: Version, workspace: Workspace) -> None:
        if version.workspace_id != workspace.id:
            raise InvalidTransitionError("Version does not belong to the Workspace")


__all__ = ["WorkspaceApplication"]
