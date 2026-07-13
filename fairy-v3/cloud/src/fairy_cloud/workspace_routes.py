from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from fairy_core.contracts.workspaces import (
    WorkspaceFileContentModel,
    WorkspaceFilePageModel,
    WorkspaceModel,
)
from fastapi import APIRouter, Query


def install_workspace_routes(
    router: APIRouter,
    invoke: Callable[[str, Mapping[str, Any]], dict[str, Any]],
) -> None:
    @router.get(
        "/workspaces/{workspace_id}",
        operation_id="workspaces.get",
        response_model=WorkspaceModel,
    )
    def get_workspace(workspace_id: UUID) -> dict[str, Any]:
        return invoke("workspaces.get", {"workspace_id": str(workspace_id)})

    @router.get(
        "/workspaces/{workspace_id}/files",
        operation_id="workspaces.files.list",
        response_model=WorkspaceFilePageModel,
    )
    def list_workspace_files(
        workspace_id: UUID,
        version_id: UUID | None = None,
    ) -> dict[str, Any]:
        params = {"workspace_id": str(workspace_id)}
        if version_id is not None:
            params["version_id"] = str(version_id)
        return invoke("workspaces.files.list", params)

    @router.get(
        "/workspaces/{workspace_id}/file-content",
        operation_id="workspaces.files.read",
        response_model=WorkspaceFileContentModel,
    )
    def read_workspace_file(
        workspace_id: UUID,
        path: str = Query(min_length=1, max_length=1_024),
        version_id: UUID | None = None,
    ) -> dict[str, Any]:
        params = {"workspace_id": str(workspace_id), "path": path}
        if version_id is not None:
            params["version_id"] = str(version_id)
        return invoke("workspaces.files.read", params)


__all__ = ["install_workspace_routes"]
