from __future__ import annotations

import hashlib
import json
import os
from pathlib import PurePosixPath
from uuid import UUID

from fairy_core.application.core import CoreApplication
from fairy_core.assistant.tools import ToolExecutor, ToolResult, UnavailableToolExecutor
from fairy_core.commanding.registry import ToolDefinition
from fairy_core.contracts.models import ChangesetProposal, FileMutation
from fairy_core.domain.errors import ScopeViolationError
from fairy_core.domain.execution import Artifact, ArtifactVisibility
from fairy_core.domain.models import ScopeContract
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.security.path_guard import PathGuard
from fairy_core.workspace.models import ProjectIndex, TaskWorkspace

_PROJECT_TOOLS = frozenset(
    {
        "artifact.list",
        "artifact.read",
        "edit.propose_changeset",
        "preview.status",
        "project.read",
    }
)
_MAX_TOOL_TEXT = 24_000
_MAX_ARTIFACTS = 50


class ProjectToolExecutor:
    def __init__(
        self,
        *,
        application: CoreApplication,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        delegate: ToolExecutor | None = None,
    ) -> None:
        self._application = application
        self._unit_of_work_factory = unit_of_work_factory
        self._delegate = delegate or UnavailableToolExecutor()

    def close(self) -> None:
        close = getattr(self._delegate, "close", None)
        if callable(close):
            close()

    def execute(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        if definition.name not in _PROJECT_TOOLS:
            return self._delegate.execute(definition, scope, arguments)
        if definition.name == "project.read":
            return self._read_project(scope, arguments)
        if definition.name == "artifact.list":
            return self._list_artifacts(scope)
        if definition.name == "artifact.read":
            return self._read_artifact(scope, arguments)
        if definition.name == "preview.status":
            return self._preview_status(scope)
        return self._propose_changeset(scope, arguments)

    def _read_project(
        self,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        relative = _required_string(arguments, "path")
        workspace, index = self._project_context(scope)
        normalized = _relative_path(relative)
        if not _matches_workspace(workspace, normalized):
            raise ScopeViolationError(f"path is outside the Task file contract: {normalized}")
        try:
            indexed = index.file(normalized)
        except KeyError as error:
            raise ScopeViolationError(
                f"path is not present in the current Project Index: {normalized}"
            ) from error
        if indexed.kind in {"binary", "oversized"}:
            raise ScopeViolationError(f"indexed file is not bounded UTF-8 text: {normalized}")
        guard = PathGuard(
            project_root=workspace.root,
            allowed_roots=(workspace.root,),
            forbidden_roots=(),
        )
        lease = guard.issue_read_lease(normalized)
        configured_limit = workspace.constraints.get("max_read_bytes", 1_000_000)
        max_bytes = min(
            configured_limit if isinstance(configured_limit, int) else 1_000_000,
            1_000_000,
        )
        if indexed.byte_length > max_bytes:
            raise ScopeViolationError(f"indexed file exceeds the Task read limit: {normalized}")
        with lease.canonical_path.open("rb") as source:
            if _file_identity(os.fstat(source.fileno())) != lease.target_identity:
                raise ScopeViolationError(
                    "read target identity changed before open",
                    code="PATH_IDENTITY_CHANGED",
                )
            data = source.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ScopeViolationError(f"file exceeds the Task read limit: {normalized}")
            if _file_identity(os.fstat(source.fileno())) != lease.target_identity:
                raise ScopeViolationError(
                    "read target identity changed while open",
                    code="PATH_IDENTITY_CHANGED",
                )
        guard.revalidate_read_lease(lease)
        if hashlib.sha256(data).hexdigest() != indexed.content_hash:
            raise ScopeViolationError(
                "Project Index does not match the managed Version", code="SCOPE_MISMATCH"
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ScopeViolationError(f"file is not UTF-8 text: {normalized}") from error
        lines = text.splitlines()
        start = _optional_integer(arguments, "start_line", default=1)
        end = _optional_integer(arguments, "end_line", default=len(lines) or 1)
        if end < start:
            raise ValueError("end_line must be greater than or equal to start_line")
        selected = "\n".join(lines[start - 1 : end])
        if len(selected) > _MAX_TOOL_TEXT:
            selected = selected[:_MAX_TOOL_TEXT]
        return ToolResult.create(
            public_summary=f"Read {normalized}",
            model_content=(
                f"path={normalized}\ncontent_hash={indexed.content_hash}\n"
                f"lines={start}-{min(end, len(lines))}\n{selected}"
            ),
            artifact_ids=(),
        )

    def _list_artifacts(self, scope: ScopeContract) -> ToolResult:
        with self._unit_of_work_factory() as unit_of_work:
            self._require_task_scope(unit_of_work.state.get_task(scope.task_id), scope)
            artifacts = tuple(
                artifact
                for artifact in unit_of_work.state.artifacts_for_task(scope.task_id)
                if artifact.visibility is not ArtifactVisibility.PRIVATE
                and _artifact_matches_scope(artifact, scope)
            )[:_MAX_ARTIFACTS]
        payload = [
            {
                "id": str(artifact.id),
                "type": artifact.artifact_type.value,
                "media_type": artifact.media_type,
                "byte_length": artifact.byte_length,
                "content_hash": artifact.content_hash,
                "version_id": str(artifact.version_id) if artifact.version_id else None,
            }
            for artifact in artifacts
        ]
        return ToolResult.create(
            public_summary=f"Listed {len(payload)} Artifact(s)",
            model_content=json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            artifact_ids=tuple(artifact.id for artifact in artifacts),
        )

    def _read_artifact(
        self,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        artifact_id = UUID(_required_string(arguments, "artifact_id"))
        with self._unit_of_work_factory() as unit_of_work:
            artifact = unit_of_work.state.get_artifact(artifact_id)
        if artifact is None or not _artifact_matches_scope(artifact, scope):
            raise ScopeViolationError(
                "Artifact is outside the current Task Scope", code="SCOPE_MISMATCH"
            )
        if artifact.visibility is ArtifactVisibility.PRIVATE:
            raise ScopeViolationError(
                "Private Artifact is not model-visible", code="SCOPE_MISMATCH"
            )
        content = _inline_artifact_content(artifact)
        encoded = content.encode("utf-8")
        if (
            len(encoded) != artifact.byte_length
            or hashlib.sha256(encoded).hexdigest() != artifact.content_hash
        ):
            raise ScopeViolationError(
                "Artifact content does not match its durable manifest", code="SCOPE_MISMATCH"
            )
        return ToolResult.create(
            public_summary=f"Read Artifact {artifact.id}",
            model_content=content[:_MAX_TOOL_TEXT],
            artifact_ids=(artifact.id,),
        )

    def _preview_status(self, scope: ScopeContract) -> ToolResult:
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(scope.task_id)
            self._require_task_scope(task, scope)
            runtimes = unit_of_work.state.runtimes_for_task(scope.task_id)
            preview = unit_of_work.state.preview_for_task(
                scope.task_id,
                include_terminal=True,
            )
        runtime = runtimes[-1] if runtimes else None
        payload = {
            "runtime": (
                {
                    "id": str(runtime.id),
                    "status": runtime.status.value,
                    "health": runtime.health.value,
                    "kind": runtime.kind.value,
                }
                if runtime is not None
                else None
            ),
            "preview": (
                {
                    "id": str(preview.id),
                    "status": preview.status.value,
                    "health": preview.health.value,
                    "url": preview.url,
                }
                if preview is not None
                else None
            ),
        }
        return ToolResult.create(
            public_summary="Read Preview status",
            model_content=json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            artifact_ids=(),
        )

    def _propose_changeset(
        self,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        if scope.project_id is None or scope.target_version_id is None:
            raise ScopeViolationError("scratch Tasks cannot propose project Changesets")
        workspace, _index = self._project_context(scope)
        raw_files = arguments.get("files")
        if not isinstance(raw_files, list):
            raise ValueError("files must be an array")
        files = tuple(
            FileMutation(
                path=_required_string(item, "path"),
                content=_required_string(item, "content", allow_empty=True),
            )
            for item in raw_files
            if isinstance(item, dict)
        )
        if len(files) != len(raw_files):
            raise ValueError("each Changeset file must be an object")
        for file in files:
            normalized = _relative_path(file.path)
            if not _matches_patterns(workspace.editable_files, normalized):
                raise ScopeViolationError(
                    f"Changeset path is outside editable Task files: {normalized}"
                )
        reason = _required_string(arguments, "reason")
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "files": [file.model_dump(mode="json") for file in files],
                    "reason": reason,
                    "scope_digest": scope.scope_digest,
                },
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        pending = self._application.propose_changeset(
            ChangesetProposal(
                task_id=scope.task_id,
                files=files,
                reason=reason,
                idempotency_key=f"assistant:changeset:{fingerprint}",
            )
        )
        return ToolResult.create(
            public_summary=f"Changeset awaiting approval for {len(files)} file(s)",
            model_content=json.dumps(
                {
                    "changeset_id": str(pending.changeset.id),
                    "approval_id": str(pending.approval.id),
                    "status": pending.changeset.status.value,
                    "files": list(pending.changeset.files),
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            artifact_ids=(),
        )

    def _project_context(self, scope: ScopeContract) -> tuple[TaskWorkspace, ProjectIndex]:
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(scope.task_id)
            self._require_task_scope(task, scope)
            workspace = unit_of_work.workspaces.get(scope.task_id)
            index = (
                unit_of_work.project_indexes.get(scope.target_version_id)
                if scope.target_version_id is not None
                else None
            )
        if workspace is None or index is None:
            raise ScopeViolationError(
                "Task Workspace or Project Index is unavailable", code="SCOPE_MISMATCH"
            )
        if (
            workspace.project_id != scope.project_id
            or workspace.conversation_id != scope.conversation_id
            or workspace.version_id != scope.target_version_id
            or workspace.root != scope.project_root.resolve(strict=True)
            or index.project_id != scope.project_id
            or index.version_id != scope.target_version_id
        ):
            raise ScopeViolationError(
                "Task Workspace binding does not match Scope", code="SCOPE_MISMATCH"
            )
        return workspace, index

    @staticmethod
    def _require_task_scope(task, scope: ScopeContract) -> None:
        if (
            task is None
            or task.id != scope.task_id
            or task.project_id != scope.project_id
            or task.conversation_id != scope.conversation_id
            or task.target_version_id != scope.target_version_id
        ):
            raise ScopeViolationError("Task does not match Core Scope", code="SCOPE_MISMATCH")


def _required_string(
    values: dict[str, object],
    key: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = values.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{key} must be a string")
    return value if allow_empty else value.strip()


def _optional_integer(values: dict[str, object], key: str, *, default: int) -> int:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or ":" in normalized:
        raise ScopeViolationError(f"path is outside the Task Workspace: {value}")
    return path.as_posix()


def _matches_workspace(workspace: TaskWorkspace, path: str) -> bool:
    return _matches_patterns(
        (*workspace.editable_files, *workspace.reference_files),
        path,
    )


def _matches_patterns(patterns: tuple[str, ...], path: str) -> bool:
    candidate = PurePosixPath(path)
    return any(pattern == "*" or candidate.match(pattern) for pattern in patterns)


def _file_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _artifact_matches_scope(artifact: Artifact, scope: ScopeContract) -> bool:
    return (
        artifact.task_id == scope.task_id
        and artifact.project_id == scope.project_id
        and artifact.conversation_id == scope.conversation_id
        and (artifact.version_id is None or artifact.version_id == scope.target_version_id)
    )


def _inline_artifact_content(artifact: Artifact) -> str:
    if not artifact.storage_location.startswith("inline://"):
        raise ScopeViolationError(
            "Artifact storage adapter is unavailable", code="CAPABILITY_NOT_AVAILABLE"
        )
    for key in ("content", "report", "text"):
        value = artifact.metadata.get(key)
        if isinstance(value, str):
            return value
    raise ScopeViolationError(
        "Inline Artifact has no readable content", code="CAPABILITY_NOT_AVAILABLE"
    )


__all__ = ["ProjectToolExecutor"]
