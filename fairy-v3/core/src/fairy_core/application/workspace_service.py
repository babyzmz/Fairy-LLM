from __future__ import annotations

import base64
import hashlib
import mimetypes
from typing import Any

from fairy_core.application.workspaces import WorkspaceApplication
from fairy_core.contracts.files import FileSetGetInput, FileSetResolveInput
from fairy_core.contracts.workspaces import (
    WorkspaceExportInput,
    WorkspaceFileReadInput,
    WorkspaceFileStreamInput,
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
            inline_only=True,
        )
        media_type = mimetypes.guess_type(item.path)[0] or "application/octet-stream"
        response: dict[str, Any] = {
            "file": item,
            "media_type": media_type,
            "text": None,
            "stream_required": False,
        }
        if content is not None:
            response["text"] = content.decode("utf-8")
        else:
            response["stream_required"] = True
        return response

    def open_stream(self, request: WorkspaceFileStreamInput) -> Any:
        return self._application.open_read_session(
            workspace_id=request.workspace_id,
            version_id=request.version_id,
            path=request.path,
            expires_seconds=request.expires_seconds,
        )

    def probe_file(self, request: WorkspaceFileReadInput) -> Any:
        return self._application.probe_file(
            workspace_id=request.workspace_id,
            version_id=request.version_id,
            path=request.path,
        )

    def resolve_file_set(self, request: FileSetResolveInput) -> Any:
        return self._application.resolve_file_set(
            workspace_id=request.workspace_id,
            version_id=request.version_id,
            path=request.path,
        )

    def get_file_set(self, request: FileSetGetInput) -> Any:
        return self._application.get_file_set(
            workspace_id=request.workspace_id,
            version_id=request.version_id,
            file_set_id=request.file_set_id,
        )

    def export(self, request: WorkspaceExportInput) -> dict[str, Any]:
        content = self._application.export(
            workspace_id=request.workspace_id,
            version_id=request.version_id,
        )
        return {
            "filename": request.filename,
            "media_type": "application/zip",
            "byte_length": len(content),
            "content_hash": hashlib.sha256(content).hexdigest(),
            "content_base64": base64.b64encode(content).decode("ascii"),
        }


__all__ = ["WorkspaceService"]
