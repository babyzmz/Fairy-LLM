from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fairy_core.workspace.mutations import decode_mutation
from fairy_core.workspace.object_store import AssetMutation, WorkspaceObject
from fairy_core.workspace.ports import WorkspaceId
from fairy_core.workspace.read_stream import FileReadSession
from fairy_core.workspace.worker_transport import WorkerTransport


class RustWorkspaceProvisioner:
    def __init__(self, transport: WorkerTransport, managed_root: Path) -> None:
        self._transport = transport
        self._managed_root = managed_root.resolve(strict=False)

    def version_path(self, project_id: WorkspaceId, version_id: WorkspaceId) -> Path:
        return self._managed_root / "projects" / str(project_id) / "versions" / str(version_id)

    def create_initial_version(
        self,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        *,
        source: Path | None = None,
    ) -> Path:
        params: dict[str, object] = {
            "project_id": str(project_id),
            "version_id": str(version_id),
        }
        method = "workspace.create_empty"
        if source is not None:
            method = "workspace.import"
            params["source"] = str(source.resolve(strict=True))
        return self._result_path(self._transport.call(method, params), "root")

    def fork_version(
        self,
        *,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        parent_version_id: WorkspaceId,
    ) -> Path:
        result = self._transport.call(
            "workspace.fork",
            {
                "project_id": str(project_id),
                "parent_version_id": str(parent_version_id),
                "version_id": str(version_id),
            },
        )
        return self._result_path(result, "root")

    def create_scratch(self, conversation_id: WorkspaceId, task_id: WorkspaceId) -> Path:
        result = self._transport.call(
            "workspace.create_scratch",
            {"conversation_id": str(conversation_id), "task_id": str(task_id)},
        )
        root = self._result_path(result, "root")
        return root

    def scratch_path(self, conversation_id: WorkspaceId, task_id: WorkspaceId) -> Path:
        return self._managed_root / "scratch" / str(conversation_id) / str(task_id)

    def write_text(
        self,
        *,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        relative_path: str,
        content: str,
    ) -> Path:
        result = self._transport.call(
            "workspace.write_text",
            {
                "project_id": str(project_id),
                "version_id": str(version_id),
                "relative_path": relative_path,
                "content": content,
            },
        )
        return self._result_path(result, "path")

    def apply_changeset(
        self,
        *,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        mutations: tuple[tuple[str, str], ...],
    ) -> tuple[Path, ...]:
        decoded = tuple(decode_mutation(path, patch) for path, patch in mutations)
        result = self._transport.call(
            "workspace.apply_changeset",
            {
                "project_id": str(project_id),
                "version_id": str(version_id),
                "mutations": [
                    {
                        "operation": mutation.operation.value,
                        "relative_path": mutation.path,
                        "destination_path": mutation.destination_path,
                        "content_base64": (
                            base64.b64encode(mutation.content).decode("ascii")
                            if mutation.content is not None
                            else None
                        ),
                        "expected_hash": mutation.expected_hash,
                    }
                    for mutation in decoded
                ],
            },
        )
        paths = result.get("paths")
        if not isinstance(paths, list):
            raise RuntimeError("worker result is missing paths")
        return tuple(self._value_path(path, "paths") for path in paths)

    def diff(self, *, project_id: WorkspaceId, version_id: WorkspaceId) -> str:
        result = self._transport.call(
            "workspace.diff",
            {"project_id": str(project_id), "version_id": str(version_id)},
        )
        return str(result["diff"])

    def checkpoint(
        self,
        *,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        message: str,
    ) -> str:
        result = self._transport.call(
            "workspace.checkpoint",
            {
                "project_id": str(project_id),
                "version_id": str(version_id),
                "message": message,
            },
        )
        return str(result["commit"])

    def discard_version(self, *, project_id: WorkspaceId, version_id: WorkspaceId) -> None:
        self._transport.call(
            "workspace.discard",
            {"project_id": str(project_id), "version_id": str(version_id)},
        )

    def import_asset(
        self,
        *,
        project_id: WorkspaceId,
        version_id: WorkspaceId,
        mutation: AssetMutation,
        max_file_bytes: int,
        max_workspace_bytes: int,
    ) -> WorkspaceObject:
        result = self._transport.call(
            "workspace.import_asset",
            {
                "project_id": str(project_id),
                "version_id": str(version_id),
                "operation": mutation.operation,
                "relative_path": mutation.path,
                "source": str(mutation.source.resolve(strict=True)),
                "expected_hash": mutation.expected_source_hash,
                "expected_target_hash": mutation.expected_target_hash,
                "max_file_bytes": max_file_bytes,
                "max_workspace_bytes": max_workspace_bytes,
            },
        )
        content_hash = result.get("content_hash")
        byte_length = result.get("byte_length")
        storage_path = result.get("storage_path")
        if (
            not isinstance(content_hash, str)
            or not isinstance(byte_length, int)
            or not isinstance(storage_path, str)
        ):
            raise RuntimeError("worker result is missing Workspace object metadata")
        return WorkspaceObject(content_hash, byte_length, Path(storage_path))

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
        result = self._transport.call(
            "workspace.open_read_stream",
            {
                "session_id": str(session_id),
                "workspace_id": str(workspace_id),
                "version_id": str(version_id),
                "relative_path": relative_path,
                "content_hash": content_hash,
                "byte_length": byte_length,
                "media_type": media_type,
                "expires_seconds": expires_seconds,
            },
        )
        expires_unix_ms = result.get("expires_unix_ms")
        url = result.get("url")
        if not isinstance(expires_unix_ms, int) or not isinstance(url, str):
            raise RuntimeError("worker result is missing read session metadata")
        return FileReadSession(
            session_id=session_id,
            workspace_id=workspace_id,
            version_id=version_id,
            path=relative_path,
            content_hash=content_hash,
            byte_length=byte_length,
            media_type=media_type,
            url=url,
            expires_at=datetime.fromtimestamp(expires_unix_ms / 1000, tz=UTC),
        )

    def revoke_read_session(self, session_id: UUID) -> None:
        self._transport.call(
            "workspace.revoke_read_stream",
            {"session_id": str(session_id)},
        )

    @staticmethod
    def _result_path(result: dict[str, object], key: str) -> Path:
        value = result.get(key)
        return RustWorkspaceProvisioner._value_path(value, key)

    @staticmethod
    def _value_path(value: object, key: str) -> Path:
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"worker result is missing {key}")
        return Path(value).resolve(strict=False)
