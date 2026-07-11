from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from fairy_core.application.recoverable_command import start_recoverable_core_command
from fairy_core.commanding import CommandRun, CommandStatus
from fairy_core.commanding.bus import CommandBus
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.domain.execution import (
    Artifact,
    ArtifactType,
    ArtifactVisibility,
    PreviewSession,
    PreviewStatus,
    RuntimeSession,
    RuntimeStatus,
)
from fairy_core.domain.models import ScopeContract, Task
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.runtime.models import RuntimeExecutorError
from fairy_core.runtime.review import (
    BrowserCapture,
    RuntimeEvidenceStore,
    RuntimeHealthCheck,
    RuntimeReviewer,
    RuntimeReviewRequest,
)
from fairy_core.storage import StateStore

ScopeResolver = Callable[[StateStore, Task], ScopeContract]


@dataclass(frozen=True, slots=True)
class _ReviewContext:
    task: Task
    scope: ScopeContract
    runtime: RuntimeSession
    preview: PreviewSession
    manifest: Artifact
    workspace_generation: int

    def request(self) -> RuntimeReviewRequest:
        assert self.task.project_id is not None
        assert self.task.target_version_id is not None
        assert self.preview.url is not None
        return RuntimeReviewRequest(
            project_id=self.task.project_id,
            conversation_id=self.task.conversation_id,
            task_id=self.task.id,
            version_id=self.task.target_version_id,
            runtime_id=self.runtime.id,
            preview_id=self.preview.id,
            preview_manifest_id=self.manifest.id,
            workspace_generation=self.workspace_generation,
            scope_digest=self.scope.scope_digest,
            execution_target=self.scope.execution_target,
            url=self.preview.url,
        )


class RuntimeReviewApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        reviewer: RuntimeReviewer,
        evidence_store: RuntimeEvidenceStore,
        registry: ToolRegistry,
        policy: PolicyEngine,
        scope_resolver: ScopeResolver,
        execution_policy: ExecutionPolicyResolver | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._reviewer = reviewer
        self._evidence_store = evidence_store
        self._registry = registry
        self._policy = policy
        self._scope_resolver = scope_resolver
        self._execution_policy = execution_policy or ExecutionPolicyResolver()

    def review_task(self, task_id: UUID) -> tuple[Artifact, ...]:
        health = self._reviewer.health()
        if not health.http_available:
            raise RuntimeExecutorError(
                "Runtime health Review is unavailable",
                error_code="CAPABILITY_NOT_AVAILABLE",
            )
        report = self._run_health(task_id, health.executor, health.version)
        if not health.browser_available:
            return (report,)
        screenshot = self._run_browser(task_id, health.executor, health.version)
        return report, screenshot

    def _run_health(
        self,
        task_id: UUID,
        executor: str,
        version: str | None,
    ) -> Artifact:
        prepared = self._prepare(task_id, "review.health")
        if isinstance(prepared, Artifact):
            return prepared
        command, context = prepared
        try:
            result = self._reviewer.check(context.request())
            return self._finish_health(command, context, result, executor, version)
        except Exception as error:
            self._fail(command, error)
            raise

    def _run_browser(
        self,
        task_id: UUID,
        executor: str,
        version: str | None,
    ) -> Artifact:
        prepared = self._prepare(task_id, "review.browser")
        if isinstance(prepared, Artifact):
            return prepared
        command, context = prepared
        try:
            capture = self._reviewer.capture(context.request())
            stored = self._evidence_store.put(
                content=capture.png,
                media_type="image/png",
            )
            return self._finish_browser(
                command,
                context,
                capture,
                stored.storage_location,
                stored.content_hash,
                stored.byte_length,
                executor,
                version,
            )
        except Exception as error:
            self._fail(command, error)
            raise

    def _prepare(
        self,
        task_id: UUID,
        tool_name: str,
    ) -> tuple[CommandRun, _ReviewContext] | Artifact:
        with self._transaction() as (unit_of_work, commands):
            context = self._context(unit_of_work, task_id)
            existing = self._artifact_for(
                unit_of_work.state,
                task_id,
                tool_name,
                context.manifest.id,
                context.workspace_generation,
            )
            if existing is not None:
                return existing
            command = start_recoverable_core_command(
                unit_of_work,
                commands,
                execution_policy=self._execution_policy,
                tool_name=tool_name,
                scope=context.scope,
                payload={
                    "runtime_id": str(context.runtime.id),
                    "preview_id": str(context.preview.id),
                    "preview_manifest_id": str(context.manifest.id),
                    "workspace_generation": context.workspace_generation,
                },
                idempotency_key=(
                    f"task:{task_id}:{tool_name}:manifest:{context.manifest.id}:"
                    f"generation:{context.workspace_generation}"
                ),
            )
            if command.status is CommandStatus.SUCCEEDED:
                replay = self._artifact_for(
                    unit_of_work.state,
                    task_id,
                    tool_name,
                    context.manifest.id,
                    context.workspace_generation,
                )
                if replay is None:
                    raise RuntimeError("succeeded Runtime Review has no Artifact")
                return replay
            unit_of_work.commit()
            return command, context

    def _finish_health(
        self,
        command: CommandRun,
        expected: _ReviewContext,
        result: RuntimeHealthCheck,
        executor: str,
        version: str | None,
    ) -> Artifact:
        payload = {
            "schema_version": 1,
            "status_code": result.status_code,
            "latency_ms": result.latency_ms,
            "content_type": result.content_type,
            "body_sha256": result.body_sha256,
            "body_bytes": result.body_bytes,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return self._finish(
            command=command,
            expected=expected,
            tool_name="review.health",
            artifact_type=ArtifactType.REPORT,
            storage_location=f"inline://runtime-review/{command.id}/health",
            media_type="application/json",
            byte_length=len(encoded),
            content_hash=hashlib.sha256(encoded).hexdigest(),
            executor=executor,
            version=version,
            operation_metadata={**payload, "content": encoded.decode("utf-8")},
        )

    def _finish_browser(
        self,
        command: CommandRun,
        expected: _ReviewContext,
        capture: BrowserCapture,
        storage_location: str,
        content_hash: str,
        byte_length: int,
        executor: str,
        version: str | None,
    ) -> Artifact:
        return self._finish(
            command=command,
            expected=expected,
            tool_name="review.browser",
            artifact_type=ArtifactType.SCREENSHOT,
            storage_location=storage_location,
            media_type="image/png",
            byte_length=byte_length,
            content_hash=content_hash,
            executor=executor,
            version=version,
            operation_metadata={
                "width": capture.width,
                "height": capture.height,
                "device_scale_factor": capture.device_scale_factor,
            },
        )

    def _finish(
        self,
        *,
        command: CommandRun,
        expected: _ReviewContext,
        tool_name: str,
        artifact_type: ArtifactType,
        storage_location: str,
        media_type: str,
        byte_length: int,
        content_hash: str,
        executor: str,
        version: str | None,
        operation_metadata: dict[str, object],
    ) -> Artifact:
        with self._transaction() as (unit_of_work, commands):
            current = self._context(unit_of_work, expected.task.id)
            if (
                current.runtime.id != expected.runtime.id
                or current.preview.id != expected.preview.id
                or current.manifest.id != expected.manifest.id
                or current.workspace_generation != expected.workspace_generation
                or current.scope.scope_digest != expected.scope.scope_digest
            ):
                raise RuntimeExecutorError(
                    "Runtime Review Scope changed before evidence persistence",
                    error_code="SCOPE_MISMATCH",
                )
            existing = self._artifact_for(
                unit_of_work.state,
                current.task.id,
                tool_name,
                current.manifest.id,
                current.workspace_generation,
            )
            if existing is not None:
                return existing
            artifact = Artifact.create(
                project_id=current.task.project_id,
                conversation_id=current.task.conversation_id,
                task_id=current.task.id,
                version_id=current.task.target_version_id,
                artifact_type=artifact_type,
                visibility=ArtifactVisibility.CONVERSATION,
                storage_location=storage_location,
                media_type=media_type,
                byte_length=byte_length,
                content_hash=content_hash,
                metadata={
                    "runtime_review": True,
                    "tool_name": tool_name,
                    "status": "completed",
                    "command_run_id": str(command.id),
                    "runtime_id": str(current.runtime.id),
                    "preview_id": str(current.preview.id),
                    "preview_manifest_id": str(current.manifest.id),
                    "workspace_generation": current.workspace_generation,
                    "executor": executor,
                    "executor_version": version,
                    **operation_metadata,
                },
            )
            unit_of_work.state.append_artifact(artifact)
            commands.complete(
                command.id,
                output={"artifact_id": str(artifact.id)},
                lease_owner=command.lease_owner,
                lease_fence=command.lease_fence,
            )
            unit_of_work.commit()
            return artifact

    def _fail(self, command: CommandRun, error: Exception) -> None:
        with self._transaction() as (unit_of_work, commands):
            persisted = unit_of_work.commands.get_run(command.id)
            if persisted is not None and persisted.status is CommandStatus.RUNNING:
                commands.fail(
                    command.id,
                    error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
                    lease_owner=command.lease_owner,
                    lease_fence=command.lease_fence,
                )
                unit_of_work.commit()

    def _context(self, unit_of_work: CoreUnitOfWork, task_id: UUID) -> _ReviewContext:
        state = unit_of_work.state
        task = state.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        if task.project_id is None or task.target_version_id is None:
            raise RuntimeExecutorError(
                "Runtime Review requires a Project Version",
                error_code="SCOPE_MISMATCH",
            )
        scope = self._scope_resolver(state, task)
        index = unit_of_work.project_indexes.get(task.target_version_id)
        preview = state.preview_for_task(task.id, include_terminal=True)
        if index is None or preview is None or preview.status is not PreviewStatus.READY:
            raise RuntimeExecutorError(
                "Runtime Review requires a ready current Preview",
                error_code="CAPABILITY_NOT_AVAILABLE",
            )
        runtime = state.get_runtime(preview.runtime_id)
        if runtime is None or runtime.status is not RuntimeStatus.RUNNING:
            raise RuntimeExecutorError(
                "Runtime Review requires a running Runtime",
                error_code="WORKER_INTERRUPTED",
            )
        manifest = next(
            (
                artifact
                for artifact in reversed(state.artifacts_for_task(task.id))
                if artifact.artifact_type is ArtifactType.PREVIEW_MANIFEST
                and artifact.version_id == task.target_version_id
                and artifact.metadata.get("preview_id") == str(preview.id)
                and artifact.metadata.get("runtime_id") == str(runtime.id)
                and artifact.metadata.get("workspace_generation") == index.generation
                and artifact.metadata.get("status") == "ready"
            ),
            None,
        )
        if manifest is None:
            raise RuntimeExecutorError(
                "Runtime Review Preview manifest is stale",
                error_code="SCOPE_MISMATCH",
            )
        return _ReviewContext(task, scope, runtime, preview, manifest, index.generation)

    @staticmethod
    def _artifact_for(
        state: StateStore,
        task_id: UUID,
        tool_name: str,
        manifest_id: UUID,
        generation: int,
    ) -> Artifact | None:
        return next(
            (
                artifact
                for artifact in reversed(state.artifacts_for_task(task_id))
                if artifact.metadata.get("tool_name") == tool_name
                and artifact.metadata.get("preview_manifest_id") == str(manifest_id)
                and artifact.metadata.get("workspace_generation") == generation
                and artifact.metadata.get("status") == "completed"
            ),
            None,
        )

    @contextmanager
    def _transaction(self) -> Iterator[tuple[CoreUnitOfWork, CommandBus]]:
        with self._unit_of_work_factory() as unit_of_work:
            yield (
                unit_of_work,
                CommandBus(
                    registry=self._registry,
                    policy=self._policy,
                    ledger=unit_of_work.commands,
                ),
            )


__all__ = ["RuntimeReviewApplication"]
