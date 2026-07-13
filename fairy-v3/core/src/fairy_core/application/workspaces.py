from __future__ import annotations

import hashlib
import io
import zipfile
from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.models import Version, Workspace
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.security.path_guard import PathGuard
from fairy_core.workspace.index import ProjectIndexer
from fairy_core.workspace.models import ProjectFile, ProjectIndex


class WorkspaceApplication:
    def __init__(
        self,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        project_indexer: ProjectIndexer,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._project_indexer = project_indexer

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
    ) -> tuple[ProjectFile, bytes]:
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
        authorized = guard.revalidate_read_lease(lease)
        content = authorized.read_bytes()
        if (
            len(content) != item.byte_length
            or hashlib.sha256(content).hexdigest() != item.content_hash
        ):
            raise InvalidTransitionError("Workspace file changed outside its indexed Version")
        return item, content

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
