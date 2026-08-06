from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from uuid import UUID

from fairy_core.application.core import CoreApplication
from fairy_core.assistant.evidence import (
    EvidenceDraft,
    EvidenceRequirementKind,
    EvidenceSourceKind,
    query_digest,
)
from fairy_core.assistant.tools import ToolExecutor, ToolResult, UnavailableToolExecutor
from fairy_core.commanding.registry import ToolDefinition
from fairy_core.contracts.models import ChangesetProposal, FileMutation
from fairy_core.contracts.planning import ExecutionPlanCreateInput, PlannedFileInput
from fairy_core.domain.errors import ScopeViolationError
from fairy_core.domain.execution import Artifact, ArtifactVisibility, ChangesetStatus
from fairy_core.domain.models import ScopeContract
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.security.path_guard import PathGuard
from fairy_core.workspace.models import ProjectFile, ProjectIndex, TaskWorkspace

_PROJECT_TOOLS = frozenset(
    {
        "artifact.list",
        "artifact.read",
        "edit.propose_changeset",
        "execution.plan",
        "preview.status",
        "project.list",
        "project.read",
        "project.search",
    }
)
_MAX_TOOL_TEXT = 24_000
_MAX_ARTIFACTS = 50
_MAX_SEARCH_BYTES = 32 * 1024 * 1024
_ABSENT_FILE_HASH = "0" * 64


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
        if definition.name == "project.list":
            return self._list_project(scope, arguments)
        if definition.name == "project.search":
            return self._search_project(scope, arguments)
        if definition.name == "project.read":
            return self._read_project(scope, arguments)
        if definition.name == "artifact.list":
            return self._list_artifacts(scope)
        if definition.name == "artifact.read":
            return self._read_artifact(scope, arguments)
        if definition.name == "preview.status":
            return self._preview_status(scope)
        if definition.name == "execution.plan":
            return self._create_execution_plan(scope, arguments)
        return self._propose_changeset(scope, arguments)

    def _list_project(
        self,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        workspace, index = self._project_context(scope)
        prefix = _optional_prefix(arguments.get("prefix"))
        glob = _optional_glob(arguments.get("glob"))
        limit = _bounded_integer(arguments, "limit", default=100, maximum=200)
        cursor_scope = _discovery_scope(
            scope=scope,
            index=index,
            operation="list",
            values={"prefix": prefix, "glob": glob},
        )
        offset = _decode_discovery_cursor(
            arguments.get("cursor"),
            expected_scope=cursor_scope,
            shape="list",
        )[0]
        files = tuple(
            item
            for item in index.files
            if _matches_workspace(workspace, item.path)
            and _matches_prefix(item.path, prefix)
            and _matches_glob(item.path, glob)
        )
        page = files[offset : offset + limit]
        next_offset = offset + len(page)
        next_cursor = (
            _encode_discovery_cursor(cursor_scope, (next_offset,))
            if next_offset < len(files)
            else None
        )
        payload = {
            "index_generation": index.generation,
            "source_hash": index.source_hash,
            "items": [
                {
                    "path": item.path,
                    "kind": item.kind,
                    "language": item.language,
                    "byte_length": item.byte_length,
                    "content_hash": item.content_hash,
                }
                for item in page
            ],
            "next_cursor": next_cursor,
        }
        return ToolResult.create(
            public_summary=f"Listed {len(page)} Workspace file(s)",
            model_content=json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            artifact_ids=(),
            evidence_drafts=(
                EvidenceDraft(
                    requirement_kind=EvidenceRequirementKind.WORKSPACE_STRUCTURE,
                    source_kind=EvidenceSourceKind.PROJECT_INDEX,
                    public_label="Current Workspace file index",
                    content_hash=index.source_hash,
                    source_revision=f"index:{index.generation}",
                    workspace_generation=index.generation,
                    truncated=next_cursor is not None,
                ),
            ),
        )

    def _search_project(
        self,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        query = _required_string(arguments, "query")
        if len(query) > 512:
            raise ValueError("query is too long")
        workspace, index = self._project_context(scope)
        glob = _optional_glob(arguments.get("glob"))
        case_sensitive = _optional_boolean(arguments, "case_sensitive", default=False)
        limit = _bounded_integer(arguments, "limit", default=20, maximum=50)
        cursor_scope = _discovery_scope(
            scope=scope,
            index=index,
            operation="search",
            values={"query": query, "glob": glob, "case_sensitive": case_sensitive},
        )
        file_offset, line_offset, column_offset = _decode_discovery_cursor(
            arguments.get("cursor"),
            expected_scope=cursor_scope,
            shape="search",
        )
        matches: list[dict[str, object]] = []
        scanned_bytes = 0
        skipped_binary = 0
        next_position: tuple[int, int, int] | None = None
        pattern = re.compile(re.escape(query), 0 if case_sensitive else re.IGNORECASE)
        guard = PathGuard(
            project_root=workspace.root,
            allowed_roots=(workspace.root,),
            forbidden_roots=(),
        )
        for file_index in range(file_offset, len(index.files)):
            indexed = index.files[file_index]
            if not _matches_workspace(workspace, indexed.path) or not _matches_glob(
                indexed.path,
                glob,
            ):
                continue
            if indexed.kind in {"binary", "oversized"}:
                skipped_binary += 1
                continue
            if scanned_bytes and scanned_bytes + indexed.byte_length > _MAX_SEARCH_BYTES:
                next_position = (file_index, 0, 0)
                break
            data = _read_indexed_bytes(
                workspace=workspace,
                indexed=indexed,
                guard=guard,
            )
            scanned_bytes += len(data)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                skipped_binary += 1
                continue
            lines = text.splitlines()
            start_line = line_offset if file_index == file_offset else 0
            for line_index in range(start_line, len(lines)):
                line = lines[line_index]
                start_column = (
                    column_offset
                    if file_index == file_offset and line_index == start_line
                    else 0
                )
                for match in pattern.finditer(line, pos=start_column):
                    candidate = {
                        "path": indexed.path,
                        "line": line_index + 1,
                        "column": match.start() + 1,
                        "text": line[:500],
                        "content_hash": indexed.content_hash,
                    }
                    projected = json.dumps(
                        [*matches, candidate],
                        ensure_ascii=True,
                        separators=(",", ":"),
                    )
                    if len(projected) > _MAX_TOOL_TEXT - 2_000:
                        next_position = (file_index, line_index, match.start())
                        break
                    matches.append(candidate)
                    if len(matches) >= limit:
                        next_position = (
                            file_index,
                            line_index,
                            max(match.end(), match.start() + 1),
                        )
                        break
                if next_position is not None:
                    break
            if next_position is not None:
                break
            line_offset = 0
            column_offset = 0
        next_cursor = (
            _encode_discovery_cursor(cursor_scope, next_position)
            if next_position is not None
            else None
        )
        payload = {
            "query": query,
            "index_generation": index.generation,
            "matches": matches,
            "scanned_bytes": scanned_bytes,
            "skipped_binary_or_oversized": skipped_binary,
            "truncated": next_cursor is not None,
            "next_cursor": next_cursor,
        }
        return ToolResult.create(
            public_summary=f"Found {len(matches)} Workspace match(es)",
            model_content=json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            artifact_ids=(),
            evidence_drafts=(
                EvidenceDraft(
                    requirement_kind=EvidenceRequirementKind.WORKSPACE_CONTENT,
                    source_kind=EvidenceSourceKind.PROJECT_INDEX,
                    public_label="Current Workspace text search",
                    content_hash=index.source_hash,
                    source_revision=f"index:{index.generation}",
                    workspace_generation=index.generation,
                    truncated=next_cursor is not None,
                ),
            ),
        )

    def _create_execution_plan(
        self,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        request = ExecutionPlanCreateInput.model_validate({"task_id": scope.task_id, **arguments})
        workspace, index = self._project_context(scope)
        indexed_files = {item.path: item for item in index.files}
        normalized_files: list[PlannedFileInput] = []
        for planned in request.files:
            normalized = _relative_path(planned.path)
            if not _matches_patterns(workspace.editable_files, normalized):
                raise ScopeViolationError(
                    f"planned path is outside editable Task files: {normalized}",
                    code="SCOPE_MISMATCH",
                )
            indexed = indexed_files.get(normalized)
            if indexed is None and planned.expected_hash is not None:
                raise ScopeViolationError(
                    f"planned hash does not match absent file: {normalized}",
                    code="SCOPE_MISMATCH",
                    model_detail=(
                        "The planned file does not exist. Omit expected_hash for a new file."
                    ),
                )
            if indexed is not None and planned.expected_hash is None:
                raise ScopeViolationError(
                    f"planned existing file was not read first: {normalized}",
                    code="EVIDENCE_REQUIRED_BEFORE_PLAN",
                    model_detail=(
                        "Read every existing planned file with project.read in this Turn and "
                        "submit its returned content hash as expected_hash."
                    ),
                )
            if (
                indexed is not None
                and planned.expected_hash is not None
                and planned.expected_hash != indexed.content_hash
            ):
                raise ScopeViolationError(
                    f"planned hash is stale for existing file: {normalized}",
                    code="SCOPE_MISMATCH",
                    model_detail=(
                        "Read the current file with project.read and create a new Task from the "
                        "latest Workspace Version."
                    ),
                )
            normalized_files.append(
                planned.model_copy(
                    update={
                        "path": normalized,
                        "expected_hash": (indexed.content_hash if indexed is not None else None),
                    }
                )
            )
        request = request.model_copy(
            update={
                "files": tuple(normalized_files),
                "validation_commands": tuple(
                    command
                    for command in request.validation_commands
                    if not _is_runtime_command(command)
                ),
            }
        )
        context = self._application.execution_planning.create(
            request,
            initial_model_calls=1,
            initial_tool_calls=1,
        )
        return ToolResult.create(
            public_summary=f"Planned {len(request.files)} file(s)",
            model_content=json.dumps(
                {
                    "plan_id": str(context.plan.id),
                    "status": context.plan.status.value,
                    "batches": max(item.batch for item in request.files),
                    "files": len(request.files),
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            artifact_ids=(),
        )

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
        resolved_end = max(1, min(end, len(lines)))
        return ToolResult.create(
            public_summary=f"Read {normalized}",
            model_content=(
                f"path={normalized}\ncontent_hash={indexed.content_hash}\n"
                f"lines={start}-{resolved_end}\n{selected}"
            ),
            artifact_ids=(),
            evidence_drafts=(
                EvidenceDraft(
                    requirement_kind=EvidenceRequirementKind.WORKSPACE_CONTENT,
                    source_kind=EvidenceSourceKind.PROJECT_FILE,
                    public_label=f"Workspace file {normalized}",
                    relative_path=normalized,
                    line_start=start,
                    line_end=max(start, resolved_end),
                    content_hash=indexed.content_hash,
                    source_revision=f"index:{index.generation}",
                    workspace_generation=index.generation,
                    truncated=end < len(lines) or len(selected) >= _MAX_TOOL_TEXT,
                ),
            ),
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
            plan = unit_of_work.state.execution_plan_for_task(scope.task_id)
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
            "next_action": ("execution.plan" if plan is None and preview is None else None),
        }
        observed_at = datetime.now(UTC)
        revision = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        ).hexdigest()
        return ToolResult.create(
            public_summary=(
                "Preview is not available before planning"
                if plan is None and preview is None
                else "Read Preview status"
            ),
            model_content=json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            artifact_ids=(),
            evidence_drafts=(
                EvidenceDraft(
                    requirement_kind=EvidenceRequirementKind.RUNTIME_CURRENT,
                    source_kind=EvidenceSourceKind.RUNTIME_SNAPSHOT,
                    public_label="Current Preview and Runtime status",
                    content_hash=revision,
                    source_revision=revision,
                    observed_at=observed_at,
                    expires_at=observed_at + timedelta(seconds=30),
                ),
            ),
        )

    def _propose_changeset(
        self,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        if scope.target_version_id is None:
            raise ScopeViolationError("Task has no writable Workspace Version")
        with self._unit_of_work_factory() as unit_of_work:
            plan = unit_of_work.state.execution_plan_for_task(scope.task_id)
        if plan is None:
            raise ScopeViolationError(
                "Create an Execution Plan before proposing file changes",
                code="SCOPE_MISMATCH",
                model_detail=(
                    "Create execution.plan before edit.propose_changeset. The plan must include "
                    "every intended file; use expected_hash=null for a new file."
                ),
            )
        workspace, index = self._project_context(scope)
        raw_files = arguments.get("files")
        if not isinstance(raw_files, list):
            raise ValueError("files must be an array")
        files = tuple(
            FileMutation(
                path=_relative_path(_required_string(item, "path")),
                content=_required_string(item, "content", allow_empty=True),
            )
            for item in raw_files
            if isinstance(item, dict)
        )
        if len(files) != len(raw_files):
            raise ValueError("each Changeset file must be an object")
        if len({file.path for file in files}) != len(files):
            raise ScopeViolationError(
                "Changeset contains duplicate canonical file paths",
                code="SCOPE_MISMATCH",
                model_detail="Submit each planned Workspace path exactly once per batch.",
            )
        planned_files = {str(item["path"]): item for item in plan.manifest["files"]}
        planned_paths = set(planned_files)
        unplanned = sorted(file.path for file in files if file.path not in planned_paths)
        if unplanned:
            raise ScopeViolationError(
                f"Changeset contains unplanned files: {', '.join(unplanned[:5])}",
                code="SCOPE_MISMATCH",
                model_detail=(
                    "Only submit files declared by execution.plan. Start a new Task if the "
                    "immutable plan must change."
                ),
            )
        _require_complete_planned_batch(plan.manifest["files"], files)
        indexed_files = {item.path: item for item in index.files}
        for file in files:
            expected_hash = _expected_planned_hash(planned_files[file.path])
            indexed = indexed_files.get(file.path)
            if indexed is None:
                _reject_obvious_placeholder(file.path, file.content)
            if indexed is None and expected_hash is not None:
                raise ScopeViolationError(
                    f"planned hash does not match absent file: {file.path}",
                    code="SCOPE_MISMATCH",
                    model_detail=(
                        "The planned file does not exist. Use expected_hash=null for new files "
                        "when creating execution.plan."
                    ),
                )
            if indexed is not None and expected_hash != indexed.content_hash:
                raise ScopeViolationError(
                    f"planned hash is stale or missing for existing file: {file.path}",
                    code="SCOPE_MISMATCH",
                    model_detail=(
                        "An existing planned file must use the content_hash from the current "
                        "Project Index. Start a new Task to rebuild a stale immutable plan."
                    ),
                )
        for file in files:
            normalized = _relative_path(file.path)
            if not _matches_patterns(workspace.editable_files, normalized):
                raise ScopeViolationError(
                    f"Changeset path is outside editable Task files: {normalized}"
                )
        reason = _required_string(arguments, "reason")
        with self._unit_of_work_factory() as unit_of_work:
            aggregate = unit_of_work.state.get_workspace(workspace.workspace_id)
        if aggregate is None:
            raise ScopeViolationError(
                "Workspace aggregate is unavailable",
                code="SCOPE_MISMATCH",
            )
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
        paths = tuple(file.path for file in files)
        self._application.execution_planning.start_file_batch(scope.task_id, paths)
        try:
            pending = self._application.propose_changeset(
                ChangesetProposal(
                    task_id=scope.task_id,
                    files=files,
                    reason=reason,
                    idempotency_key=f"assistant:changeset:{fingerprint}",
                    expected_workspace_revision=aggregate.revision,
                )
            )
        except Exception as error:
            self._application.execution_planning.fail_file_batch(
                scope.task_id,
                paths,
                error_code=str(getattr(error, "code", "WORKER_INTERRUPTED")),
            )
            raise
        if pending.changeset.status is ChangesetStatus.APPLIED:
            self._application.execution_planning.complete_file_batch(scope.task_id, paths)
        applied = pending.changeset.status is ChangesetStatus.APPLIED
        return ToolResult.create(
            public_summary=(
                f"Applied {len(files)} Workspace file(s)"
                if applied
                else f"Changeset awaiting approval for {len(files)} file(s)"
            ),
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
            awaiting_approval=not applied,
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


def _bounded_integer(
    values: dict[str, object],
    key: str,
    *,
    default: int,
    maximum: int,
) -> int:
    value = _optional_integer(values, key, default=default)
    if value > maximum:
        raise ValueError(f"{key} exceeds the supported limit")
    return value


def _optional_boolean(values: dict[str, object], key: str, *, default: bool) -> bool:
    value = values.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _optional_prefix(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("prefix must be a string")
    return _relative_path(value.strip().rstrip("/"))


def _optional_glob(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("glob must be a bounded string")
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or ":" in normalized:
        raise ScopeViolationError("glob is outside the Task Workspace")
    return normalized


def _matches_prefix(path: str, prefix: str | None) -> bool:
    return prefix is None or path == prefix or path.startswith(f"{prefix}/")


def _matches_glob(path: str, glob: str | None) -> bool:
    return glob is None or PurePosixPath(path).match(glob)


def _discovery_scope(
    *,
    scope: ScopeContract,
    index: ProjectIndex,
    operation: str,
    values: object,
) -> str:
    return query_digest(
        {
            "scope_digest": scope.scope_digest,
            "version_id": str(index.version_id),
            "generation": index.generation,
            "operation": operation,
            "query": values,
        }
    )


def _encode_discovery_cursor(scope_digest: str, position: tuple[int, ...]) -> str:
    payload = json.dumps(
        {"v": 1, "s": scope_digest, "p": list(position)},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_discovery_cursor(
    value: object,
    *,
    expected_scope: str,
    shape: str,
) -> tuple[int, ...]:
    expected_size = 1 if shape == "list" else 3
    if value is None:
        return (0,) if shape == "list" else (0, 0, 0)
    try:
        if not isinstance(value, str) or not value or len(value) > 2_048:
            raise ValueError
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded)
        position = payload.get("p") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "s", "p"}
            or payload["v"] != 1
            or not isinstance(payload["s"], str)
            or not hmac.compare_digest(payload["s"], expected_scope)
            or not isinstance(position, list)
            or len(position) != expected_size
            or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0
                for item in position
            )
        ):
            raise ValueError
    except (
        binascii.Error,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        raise ValueError("cursor is invalid for this Workspace query") from error
    return tuple(position)


def _read_indexed_bytes(
    *,
    workspace: TaskWorkspace,
    indexed: ProjectFile,
    guard: PathGuard,
) -> bytes:
    lease = guard.issue_read_lease(indexed.path)
    configured_limit = workspace.constraints.get("max_read_bytes", 1_000_000)
    max_bytes = min(
        configured_limit if isinstance(configured_limit, int) else 1_000_000,
        1_000_000,
    )
    if indexed.byte_length > max_bytes:
        raise ScopeViolationError(f"indexed file exceeds the Task read limit: {indexed.path}")
    with lease.canonical_path.open("rb") as source:
        if _file_identity(os.fstat(source.fileno())) != lease.target_identity:
            raise ScopeViolationError(
                "read target identity changed before open",
                code="PATH_IDENTITY_CHANGED",
            )
        data = source.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ScopeViolationError(f"file exceeds the Task read limit: {indexed.path}")
        if _file_identity(os.fstat(source.fileno())) != lease.target_identity:
            raise ScopeViolationError(
                "read target identity changed while open",
                code="PATH_IDENTITY_CHANGED",
            )
    guard.revalidate_read_lease(lease)
    if hashlib.sha256(data).hexdigest() != indexed.content_hash:
        raise ScopeViolationError(
            "Project Index does not match the managed Version",
            code="SCOPE_MISMATCH",
        )
    return data


def _relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or ":" in normalized:
        raise ScopeViolationError(f"path is outside the Task Workspace: {value}")
    canonical = path.as_posix()
    if canonical in {"", "."}:
        raise ScopeViolationError("path must identify a Task Workspace file")
    return canonical


def _matches_workspace(workspace: TaskWorkspace, path: str) -> bool:
    return _matches_patterns(
        (*workspace.editable_files, *workspace.reference_files),
        path,
    )


def _matches_patterns(patterns: tuple[str, ...], path: str) -> bool:
    candidate = PurePosixPath(path)
    return any(pattern == "*" or candidate.match(pattern) for pattern in patterns)


def _expected_planned_hash(planned_file: object) -> str | None:
    if not isinstance(planned_file, dict):
        raise ScopeViolationError(
            "Execution Plan file metadata is invalid",
            code="SCOPE_MISMATCH",
        )
    value = planned_file.get("expected_hash")
    return None if value in {None, _ABSENT_FILE_HASH} else str(value)


def _require_complete_planned_batch(
    planned_files: object,
    files: tuple[FileMutation, ...],
) -> None:
    if not isinstance(planned_files, list):
        raise ScopeViolationError(
            "Execution Plan file manifest is invalid",
            code="SCOPE_MISMATCH",
        )
    planned = {
        str(item["path"]): int(item["batch"])
        for item in planned_files
        if isinstance(item, dict) and "path" in item and "batch" in item
    }
    try:
        batches = {planned[file.path] for file in files}
    except KeyError as error:
        raise ScopeViolationError(
            f"Changeset contains an unplanned file: {error.args[0]}",
            code="SCOPE_MISMATCH",
        ) from error
    if len(batches) != 1:
        raise ScopeViolationError(
            "Changeset cannot span multiple planned batches",
            code="SCOPE_MISMATCH",
            model_detail="Submit exactly one complete execution.plan batch per Changeset.",
        )
    batch = batches.pop()
    expected = {path for path, planned_batch in planned.items() if planned_batch == batch}
    supplied = {file.path for file in files}
    if supplied != expected:
        missing = ", ".join(sorted(expected - supplied)[:10])
        extra = ", ".join(sorted(supplied - expected)[:10])
        detail = f"Submit every file in planned batch {batch} in one edit.propose_changeset call."
        if missing:
            detail += f" Missing: {missing}."
        if extra:
            detail += f" Unexpected: {extra}."
        raise ScopeViolationError(
            "Changeset does not contain the complete planned file batch",
            code="SCOPE_MISMATCH",
            model_detail=detail,
        )


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


def _reject_obvious_placeholder(path: str, content: str) -> None:
    suffix = PurePosixPath(path).suffix.casefold()
    if suffix in {".html", ".htm"}:
        body = re.search(r"<body(?:\s[^>]*)?>(.*?)</body\s*>", content, re.I | re.S)
        if body is not None and not _without_comments(body.group(1), html=True).strip():
            raise ScopeViolationError(
                f"generated HTML has an empty body: {path}",
                code="SCOPE_MISMATCH",
                model_detail="Submit the complete planned HTML file, not a placeholder.",
            )
        if body is None and not _without_comments(content, html=True).strip():
            raise ScopeViolationError(
                f"generated HTML is only comments or whitespace: {path}",
                code="SCOPE_MISMATCH",
            )
    if suffix in {".css", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"} and not (
        _without_comments(content, html=False).strip()
    ):
        raise ScopeViolationError(
            f"generated source is only comments or whitespace: {path}",
            code="SCOPE_MISMATCH",
            model_detail="Submit complete source content for every planned file.",
        )


def _without_comments(content: str, *, html: bool) -> str:
    if html:
        return re.sub(r"<!--.*?-->", "", content, flags=re.S)
    without_blocks = re.sub(r"/\*.*?\*/", "", content, flags=re.S)
    return re.sub(r"(?m)^\s*//[^\r\n]*(?:\r?\n|$)", "", without_blocks)


def _is_runtime_command(command: str) -> bool:
    normalized = " ".join(command.casefold().split())
    patterns = (
        r"(?:^|\s)python(?:3)?(?:\.exe)?\s+-m\s+http\.server(?:\s|$)",
        r"(?:^|\s)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve)(?:\s|$)",
        r"(?:^|\s)(?:next\s+dev|vite|uvicorn|gunicorn|flask\s+run|nodemon)(?:\s|$)",
        r"(?:^|\s)--watch(?:\s|$)",
    )
    return any(re.search(pattern, normalized) is not None for pattern in patterns)


__all__ = ["ProjectToolExecutor"]
