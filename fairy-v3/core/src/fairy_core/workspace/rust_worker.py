from __future__ import annotations

import base64
from pathlib import Path

from fairy_core.workspace.mutations import decode_mutation
from fairy_core.workspace.ports import WorkspaceId
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

    @staticmethod
    def _result_path(result: dict[str, object], key: str) -> Path:
        value = result.get(key)
        return RustWorkspaceProvisioner._value_path(value, key)

    @staticmethod
    def _value_path(value: object, key: str) -> Path:
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"worker result is missing {key}")
        return Path(value).resolve(strict=False)
