from __future__ import annotations

import base64
import mimetypes
from typing import Any

from fairy_core.application.workspaces import WorkspaceApplication
from fairy_core.contracts.workspaces import (
    WorkspaceFileReadInput,
    WorkspaceIdInput,
    WorkspaceVersionInput,
)


class WorkspaceService:
    def __init__(self, application: WorkspaceApplication) -> None:
        self._application = application

    def get(self, request: WorkspaceIdInput) -> Any:
        return self._application.get(request.workspace_id)

    def list_files(self, request: WorkspaceVersionInput) -> dict[str, Any]:
        index = self._application.files(
            workspace_id=request.workspace_id,
            version_id=request.version_id,
        )
        return {
            "workspace_id": index.workspace_id,
            "version_id": index.version_id,
            "generation": index.generation,
            "source_hash": index.source_hash,
            "items": index.files,
        }

    def read_file(self, request: WorkspaceFileReadInput) -> dict[str, Any]:
        item, content = self._application.read_file(
            workspace_id=request.workspace_id,
            version_id=request.version_id,
            path=request.path,
        )
        media_type = mimetypes.guess_type(item.path)[0] or "application/octet-stream"
        response: dict[str, Any] = {
            "file": item,
            "media_type": media_type,
            "text": None,
            "content_base64": None,
        }
        if item.kind in {"source", "manifest", "config", "text"}:
            response["text"] = content.decode("utf-8")
        else:
            response["content_base64"] = base64.b64encode(content).decode("ascii")
        return response


__all__ = ["WorkspaceService"]
