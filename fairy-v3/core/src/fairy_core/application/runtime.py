from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from fairy_core.application.runtime_contracts import (
    PreviewContext,
    PreviewResolveRequest,
    PreviewStartRequest,
    PreviewStopRequest,
    RuntimeHealthResult,
)
from fairy_core.application.runtime_support import RuntimeApplicationSupport
from fairy_core.commanding import CommandRun, EventVisibility
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.domain.execution import (
    ArtifactType,
    PreviewSession,
    PreviewStatus,
    PreviewVisibility,
    RuntimeKind,
    RuntimeSession,
    RuntimeStatus,
)
from fairy_core.domain.models import ScopeContract, TaskStatus
from fairy_core.persistence.unit_of_work import CoreUnitOfWork
from fairy_core.runtime.artifacts import ensure_preview_manifest
from fairy_core.runtime.models import (
    DynamicRuntimeStart,
    ExecutorRuntimeState,
    RuntimeExecutorError,
    RuntimeExecutorHealth,
    RuntimeProbeResult,
    RuntimeRecoveryTarget,
    RuntimeStartResult,
    StaticRuntimeStart,
)
from fairy_core.runtime.templates import (
    RuntimeAdapter,
    RuntimeTemplate,
    RuntimeTemplateError,
    select_runtime_template,
)


@dataclass(frozen=True, slots=True)
class _StartIntent:
    context: PreviewContext
    command: CommandRun
    scope: ScopeContract
    template: RuntimeTemplate
    workspace_generation: int


@dataclass(frozen=True, slots=True)
class _StopIntent:
    preview: PreviewSession
    runtime: RuntimeSession
    command: CommandRun
    executor_handle: str


class RuntimeApplication(RuntimeApplicationSupport):
    def start_preview(self, request: PreviewStartRequest) -> PreviewContext:
        with self._operation_lock:
            return self._start_preview(request)

    def _start_preview(self, request: PreviewStartRequest) -> PreviewContext:
        intent_or_replay = self._prepare_start(request)
        if isinstance(intent_or_replay, PreviewContext):
            if (
                intent_or_replay.preview.status is PreviewStatus.STARTING
                and intent_or_replay.runtime.status
                in {RuntimeStatus.STARTING, RuntimeStatus.RUNNING}
            ):
                if self._recovery_blocked_by_live_lease(
                    intent_or_replay.runtime,
                    "preview.start",
                ):
                    return intent_or_replay
                self._recover_start(
                    intent_or_replay.runtime,
                    intent_or_replay.preview,
                )
                return self.get_preview(intent_or_replay.preview.id)
            return intent_or_replay
        intent = intent_or_replay
        preview = intent.context.preview
        runtime = intent.context.runtime
        assert runtime.project_id is not None and runtime.version_id is not None
        try:
            if intent.template.adapter is RuntimeAdapter.STATIC:
                result = self._executor.start_static(
                    StaticRuntimeStart(
                        project_id=runtime.project_id,
                        version_id=runtime.version_id,
                        preview_id=preview.id,
                        project_root=runtime.project_root,
                    )
                )
            else:
                archive = self._archive_builder.build(intent.scope)
                if archive.generation != intent.workspace_generation:
                    raise RuntimeExecutorError(
                        "Workspace changed after dependency layer validation",
                        error_code="SCOPE_MISMATCH",
                    )
                dependency_key = intent.template.dependency_key
                assert dependency_key is not None
                result = self._executor.start_dynamic(
                    DynamicRuntimeStart(
                        project_id=runtime.project_id,
                        conversation_id=runtime.conversation_id,
                        task_id=runtime.task_id,
                        version_id=runtime.version_id,
                        runtime_id=runtime.id,
                        preview_id=preview.id,
                        project_root=runtime.project_root,
                        execution_target=runtime.execution_target,
                        kind=runtime.kind,
                        adapter=intent.template.adapter.value,
                        scope_digest=intent.scope.scope_digest,
                        workspace_generation=archive.generation,
                        lease_fence=runtime.revision,
                        argv=intent.template.argv,
                        cwd=intent.template.cwd,
                        readiness_path=intent.template.readiness_path,
                        startup_timeout_seconds=(intent.template.startup_timeout_seconds),
                        dependency_key=dependency_key,
                        workspace_archive=archive.content,
                        archive_sha256=hashlib.sha256(archive.content).hexdigest(),
                    )
                )
            if result.execution_target != runtime.execution_target:
                raise RuntimeExecutorError(
                    "Runtime executor rebound the execution target",
                    error_code="SCOPE_MISMATCH",
                )
        except Exception as error:
            self._fail_start(intent, error)
            raise
        return self._finish_start(intent, result)

    def stop_preview(self, request: PreviewStopRequest) -> PreviewSession:
        with self._operation_lock:
            return self._stop_preview(request)

    def _stop_preview(self, request: PreviewStopRequest) -> PreviewSession:
        with self._transaction() as (unit_of_work, _commands):
            pending = self._require_preview(unit_of_work.state, request.preview_id)
            pending_runtime = self._require_runtime(
                unit_of_work.state,
                pending.runtime_id,
            )
        if (
            pending.status is PreviewStatus.STOPPING
            and pending_runtime.status is RuntimeStatus.STOPPING
        ):
            if self._recovery_blocked_by_live_lease(
                pending_runtime,
                "preview.stop",
            ):
                return pending
            return self._recover_stop(pending_runtime, pending)
        intent_or_replay = self._prepare_stop(request)
        if isinstance(intent_or_replay, PreviewSession):
            return intent_or_replay
        intent = intent_or_replay
        try:
            self._executor.stop(intent.executor_handle)
        except Exception as error:
            self._interrupt_stop(intent, error)
            raise
        return self._finish_stop(intent.preview.id, intent.command)

    def get_preview(self, preview_id: UUID) -> PreviewContext:
        with self._transaction() as (unit_of_work, _commands):
            preview = self._require_preview(unit_of_work.state, preview_id)
            return self._context_for_preview(unit_of_work.state, preview)

    def resolve_preview(self, request: PreviewResolveRequest) -> PreviewContext | None:
        with self._transaction() as (unit_of_work, _commands):
            state = unit_of_work.state
            conversation = self._require_conversation(state, request.conversation_id)
            if request.preview_id is not None:
                preview = self._require_preview(state, request.preview_id)
                if preview.conversation_id != conversation.id:
                    raise ValueError("Preview does not belong to the Conversation")
                return self._context_for_preview(state, preview)

            if conversation.active_task_id is not None:
                preview = state.preview_for_task(
                    conversation.active_task_id,
                    include_terminal=True,
                )
                if preview is not None and self._is_resolvable(preview):
                    return self._context_for_preview(state, preview)

            previews = tuple(reversed(state.previews_for_conversation(conversation.id)))
            for version_id in (
                conversation.active_draft_version_id,
                conversation.base_version_id,
            ):
                if version_id is None:
                    continue
                preview = next(
                    (
                        item
                        for item in previews
                        if item.version_id == version_id and self._is_resolvable(item)
                    ),
                    None,
                )
                if preview is not None:
                    return self._context_for_preview(state, preview)

            if conversation.project_id is None:
                return None
            project = self._require_project(state, conversation.project_id)
            if project.active_preview_id is None:
                return None
            preview = state.get_preview(project.active_preview_id)
            if (
                preview is None
                or preview.project_id != project.id
                or not self._is_resolvable(preview)
            ):
                return None
            return self._context_for_preview(state, preview)

    def runtime_health(self, task_id: UUID) -> RuntimeHealthResult:
        health = self._executor.health()
        with self._transaction() as (unit_of_work, _commands):
            self._require_task(unit_of_work.state, task_id)
            preview = unit_of_work.state.preview_for_task(task_id, include_terminal=True)
            runtimes = unit_of_work.state.runtimes_for_task(task_id)
        return RuntimeHealthResult(
            executor=health,
            runtime=runtimes[-1] if runtimes else None,
            preview=preview,
        )

    def recover_interrupted(self) -> tuple[PreviewSession, ...]:
        with self._operation_lock:
            return self._recover_interrupted()

    def _recover_interrupted(self) -> tuple[PreviewSession, ...]:
        with self._transaction() as (unit_of_work, _commands):
            candidates = tuple(unit_of_work.state.recoverable_runtimes())
        recovered: list[PreviewSession] = []
        for candidate in candidates:
            with self._transaction() as (unit_of_work, _commands):
                runtime = unit_of_work.state.get_runtime(candidate.id)
                if runtime is None:
                    continue
                preview = self._preview_for_runtime(unit_of_work.state, runtime)
                if preview is None:
                    continue
                if (
                    runtime.status is RuntimeStatus.RUNNING
                    and preview.status is PreviewStatus.READY
                ):
                    continue

            if runtime.status in {RuntimeStatus.STARTING, RuntimeStatus.RUNNING}:
                if self._recovery_blocked_by_live_lease(runtime, "preview.start"):
                    continue
                recovered.append(self._recover_start(runtime, preview))
            elif runtime.status is RuntimeStatus.STOPPING:
                if self._recovery_blocked_by_live_lease(runtime, "preview.stop"):
                    continue
                recovered.append(self._recover_stop(runtime, preview))
        return tuple(recovered)

    def _prepare_start(self, request: PreviewStartRequest) -> _StartIntent | PreviewContext:
        key = request.idempotency_key.strip()
        with self._transaction() as (unit_of_work, commands):
            state = unit_of_work.state
            existing = state.find_preview_by_idempotency_key(key)
            if existing is not None:
                if existing.task_id != request.task_id:
                    raise IdempotencyConflictError(
                        "Preview idempotency key is already bound to another Task"
                    )
                context = self._context_for_preview(state, existing)
                if existing.status not in {PreviewStatus.FAILED, PreviewStatus.INTERRUPTED}:
                    return context
                runtime = context.runtime
                if runtime.status not in {RuntimeStatus.FAILED, RuntimeStatus.INTERRUPTED}:
                    raise InvalidTransitionError("Preview Runtime cannot be restarted")
                task = context.task
                if task.status is TaskStatus.FAILED:
                    task.transition_to(TaskStatus.REPAIRING)
                    task.transition_to(TaskStatus.EXECUTING)
                    state.save_task(task)
                elif task.status is TaskStatus.REPAIRING:
                    task.transition_to(TaskStatus.EXECUTING)
                    state.save_task(task)
                elif task.status is not TaskStatus.EXECUTING:
                    raise InvalidTransitionError("Task must be executing before Preview start")
                scope = self._scope_resolver(state, task)
                template = self._select_template(scope)
                if runtime.kind is not template.kind:
                    raise RuntimeExecutorError(
                        "Preview retry cannot rebind the Runtime kind",
                        error_code="SCOPE_MISMATCH",
                    )
                workspace_generation = self._validate_template_dependencies(
                    unit_of_work,
                    task.id,
                    scope,
                    template,
                )
                health = self._health_for(template.kind)
                self._require_executor_health(health)
                runtime_revision = runtime.revision
                preview_revision = existing.revision
                runtime.begin_start()
                existing.begin_start()
                state.save_runtime(runtime, expected_revision=runtime_revision)
                state.save_preview(existing, expected_revision=preview_revision)
                preview = existing
            else:
                task = self._require_task(state, request.task_id)
                if task.status is not TaskStatus.EXECUTING:
                    raise InvalidTransitionError("Task must be executing before Preview start")
                scope = self._scope_resolver(state, task)
                template = self._select_template(scope)
                workspace_generation = self._validate_template_dependencies(
                    unit_of_work,
                    task.id,
                    scope,
                    template,
                )
                health = self._health_for(template.kind)
                self._require_executor_health(health)
                runtime = RuntimeSession.create(
                    scope=scope,
                    kind=template.kind,
                    executor=health.executor,
                    idempotency_key=f"{key}:runtime",
                )
                preview = PreviewSession.create(
                    scope=scope,
                    runtime_id=runtime.id,
                    visibility=PreviewVisibility.CHAT_DRAFT,
                    idempotency_key=key,
                )
                runtime.begin_start()
                preview.begin_start()
                state.append_runtime(runtime)
                state.append_preview(preview)

            command = self._start_user_command(
                unit_of_work,
                commands,
                tool_name="preview.start",
                scope=scope,
                payload={
                    "preview_id": str(preview.id),
                    "runtime_id": str(runtime.id),
                    "version_id": str(runtime.version_id),
                    "adapter": template.adapter.value,
                    "dependency_key": template.dependency_key,
                    "workspace_generation": workspace_generation,
                },
                idempotency_key=f"{key}:start:{preview.revision}",
                worker_id=self._start_worker_id(runtime.id),
            )
            unit_of_work.commands.append_event(
                run_id=command.id,
                event_type="preview.starting",
                visibility=EventVisibility.USER,
                message="Preview starting",
                payload={"preview_id": str(preview.id), "runtime_id": str(runtime.id)},
                lease_owner=command.lease_owner,
                lease_fence=command.lease_fence,
            )
            unit_of_work.commit()
        return _StartIntent(
            context=PreviewContext(task=task, runtime=runtime, preview=preview),
            command=command,
            scope=scope,
            template=template,
            workspace_generation=workspace_generation,
        )

    def _finish_start(
        self,
        intent: _StartIntent,
        result: RuntimeStartResult,
    ) -> PreviewContext:
        preview_id = intent.context.preview.id
        runtime_id = intent.context.runtime.id
        with self._transaction() as (unit_of_work, commands):
            state = unit_of_work.state
            preview = self._require_preview(state, preview_id)
            runtime = self._require_runtime(state, runtime_id)
            task = self._require_task(state, preview.task_id)
            if preview.status is PreviewStatus.READY and runtime.status is RuntimeStatus.RUNNING:
                return PreviewContext(task=task, runtime=runtime, preview=preview)
            runtime_revision = runtime.revision
            preview_revision = preview.revision
            runtime.mark_running(
                executor_handle=result.executor_handle,
                port=result.port,
            )
            preview.mark_ready(result.url)
            if task.status is TaskStatus.EXECUTING:
                task.transition_to(TaskStatus.PREVIEWING)
            conversation = self._require_conversation(state, preview.conversation_id)
            conversation.active_preview_id = preview.id
            state.save_runtime(runtime, expected_revision=runtime_revision)
            state.save_preview(preview, expected_revision=preview_revision)
            state.save_task(task)
            state.save_conversation(conversation)
            manifest = ensure_preview_manifest(
                state,
                runtime=runtime,
                preview=preview,
                url=result.url,
                command_run_id=intent.command.id,
                adapter=intent.template.adapter.value,
                dependency_key=intent.template.dependency_key,
                entry_path=intent.template.entry_path,
                readiness_path=intent.template.readiness_path,
                workspace_generation=intent.workspace_generation,
            )
            unit_of_work.commands.append_event(
                run_id=intent.command.id,
                event_type="preview.ready",
                visibility=EventVisibility.USER,
                message="Preview ready",
                payload={
                    "preview_id": str(preview.id),
                    "url": result.url,
                    "artifact_id": str(manifest.id),
                },
                lease_owner=intent.command.lease_owner,
                lease_fence=intent.command.lease_fence,
            )
            commands.complete(
                intent.command.id,
                output={"preview_id": str(preview.id), "url": result.url},
                lease_owner=intent.command.lease_owner,
                lease_fence=intent.command.lease_fence,
            )
            unit_of_work.commit()
        return PreviewContext(task=task, runtime=runtime, preview=preview)

    def _health_for(self, kind: RuntimeKind) -> RuntimeExecutorHealth:
        health_for = getattr(self._executor, "health_for", None)
        return health_for(kind) if callable(health_for) else self._executor.health()

    @staticmethod
    def _require_executor_health(health: RuntimeExecutorHealth) -> None:
        if not health.available:
            raise RuntimeExecutorError(
                "Runtime executor is unavailable",
                error_code=health.error_code or "WORKER_INTERRUPTED",
            )

    def _select_template(self, scope: ScopeContract) -> RuntimeTemplate:
        self._validate_project_scope(scope)
        try:
            template = select_runtime_template(
                scope.project_root,
                execution_target=scope.execution_target,
            )
        except RuntimeTemplateError as error:
            raise RuntimeExecutorError(
                str(error),
                error_code=error.error_code,
            ) from error
        if template.adapter is RuntimeAdapter.STATIC:
            self._validate_static_scope(scope)
            self._validate_static_entry(scope.project_root)
        return template

    @staticmethod
    def _validate_template_dependencies(
        unit_of_work: CoreUnitOfWork,
        task_id: UUID,
        scope: ScopeContract,
        template: RuntimeTemplate,
    ) -> int:
        assert scope.target_version_id is not None
        index = unit_of_work.project_indexes.get(scope.target_version_id)
        if index is None:
            raise RuntimeExecutorError(
                "Dynamic Preview Project Index is unavailable",
                error_code="SCOPE_MISMATCH",
            )
        if template.adapter is RuntimeAdapter.STATIC:
            return index.generation
        matching = next(
            (
                artifact
                for artifact in reversed(unit_of_work.state.artifacts_for_task(task_id))
                if artifact.artifact_type is ArtifactType.LOG
                and artifact.version_id == scope.target_version_id
                and artifact.metadata.get("tool_name") == "deps.install"
                and artifact.metadata.get("status") == "completed"
                and artifact.metadata.get("workspace_generation") == index.generation
                and artifact.metadata.get("dependency_key") == template.dependency_key
            ),
            None,
        )
        if matching is None:
            raise RuntimeExecutorError(
                "Dynamic Preview requires the current dependency layer",
                error_code="DEPENDENCY_LAYER_MISSING",
            )
        return index.generation

    def _fail_start(self, intent: _StartIntent, error: Exception) -> None:
        error_code = str(getattr(error, "error_code", "WORKER_INTERRUPTED"))
        with self._transaction() as (unit_of_work, commands):
            state = unit_of_work.state
            runtime = self._require_runtime(state, intent.context.runtime.id)
            preview = self._require_preview(state, intent.context.preview.id)
            task = self._require_task(state, preview.task_id)
            if runtime.status is RuntimeStatus.STARTING:
                revision = runtime.revision
                runtime.mark_failed(error_code)
                state.save_runtime(runtime, expected_revision=revision)
            if preview.status is PreviewStatus.STARTING:
                revision = preview.revision
                preview.mark_failed(error_code)
                state.save_preview(preview, expected_revision=revision)
            if task.status in {
                TaskStatus.EXECUTING,
                TaskStatus.INSTALLING,
                TaskStatus.PREVIEWING,
                TaskStatus.REVIEWING,
                TaskStatus.REPAIRING,
            }:
                task.transition_to(TaskStatus.FAILED)
                state.save_task(task)
            commands.fail(
                intent.command.id,
                error_code=error_code,
                lease_owner=intent.command.lease_owner,
                lease_fence=intent.command.lease_fence,
            )
            unit_of_work.commit()

    def _prepare_stop(self, request: PreviewStopRequest) -> _StopIntent | PreviewSession:
        key = request.idempotency_key.strip()
        with self._transaction() as (unit_of_work, commands):
            state = unit_of_work.state
            preview = self._require_preview(state, request.preview_id)
            if preview.status is PreviewStatus.STOPPED:
                return preview
            if preview.status not in {PreviewStatus.READY, PreviewStatus.INTERRUPTED}:
                raise InvalidTransitionError("Preview must be ready before stop")
            runtime = self._require_runtime(state, preview.runtime_id)
            if runtime.status not in {RuntimeStatus.RUNNING, RuntimeStatus.INTERRUPTED}:
                raise InvalidTransitionError("Preview Runtime is not stoppable")
            executor_handle = runtime.executor_handle
            if executor_handle is None:
                raise InvalidTransitionError("Preview Runtime has no executor handle")
            task = self._require_task(state, preview.task_id)
            scope = self._scope_resolver(state, task)
            runtime_revision = runtime.revision
            preview_revision = preview.revision
            runtime.begin_stop()
            preview.begin_stop()
            state.save_runtime(runtime, expected_revision=runtime_revision)
            state.save_preview(preview, expected_revision=preview_revision)
            command = self._start_user_command(
                unit_of_work,
                commands,
                tool_name="preview.stop",
                scope=scope,
                payload={"preview_id": str(preview.id), "runtime_id": str(runtime.id)},
                idempotency_key=f"{key}:stop:{preview.revision}",
                worker_id=self._stop_worker_id(runtime.id),
            )
            unit_of_work.commands.append_event(
                run_id=command.id,
                event_type="preview.stopping",
                visibility=EventVisibility.USER,
                message="Preview stopping",
                payload={"preview_id": str(preview.id)},
                lease_owner=command.lease_owner,
                lease_fence=command.lease_fence,
            )
            unit_of_work.commit()
        return _StopIntent(
            preview=preview,
            runtime=runtime,
            command=command,
            executor_handle=executor_handle,
        )

    def _finish_stop(self, preview_id: UUID, command: CommandRun | None) -> PreviewSession:
        with self._transaction() as (unit_of_work, commands):
            state = unit_of_work.state
            preview = self._require_preview(state, preview_id)
            runtime = self._require_runtime(state, preview.runtime_id)
            if preview.status is PreviewStatus.STOPPED:
                return preview
            runtime_revision = runtime.revision
            preview_revision = preview.revision
            runtime.mark_stopped()
            preview.mark_stopped()
            state.save_runtime(runtime, expected_revision=runtime_revision)
            state.save_preview(preview, expected_revision=preview_revision)
            self._clear_active_preview(state, preview)
            if command is not None:
                unit_of_work.commands.append_event(
                    run_id=command.id,
                    event_type="preview.stopped",
                    visibility=EventVisibility.USER,
                    message="Preview stopped",
                    payload={"preview_id": str(preview.id)},
                    lease_owner=command.lease_owner,
                    lease_fence=command.lease_fence,
                )
                commands.complete(
                    command.id,
                    output={"preview_id": str(preview.id), "stopped": True},
                    lease_owner=command.lease_owner,
                    lease_fence=command.lease_fence,
                )
            unit_of_work.commit()
        return preview

    def _interrupt_stop(self, intent: _StopIntent, error: Exception) -> None:
        error_code = str(getattr(error, "error_code", "WORKER_INTERRUPTED"))
        with self._transaction() as (unit_of_work, commands):
            state = unit_of_work.state
            runtime = self._require_runtime(state, intent.runtime.id)
            preview = self._require_preview(state, intent.preview.id)
            if runtime.status is RuntimeStatus.STOPPING:
                revision = runtime.revision
                runtime.mark_interrupted(error_code)
                state.save_runtime(runtime, expected_revision=revision)
            if preview.status is PreviewStatus.STOPPING:
                revision = preview.revision
                preview.mark_interrupted(error_code)
                state.save_preview(preview, expected_revision=revision)
            commands.fail(
                intent.command.id,
                error_code=error_code,
                lease_owner=intent.command.lease_owner,
                lease_fence=intent.command.lease_fence,
            )
            unit_of_work.commit()

    def _recover_start(
        self,
        runtime: RuntimeSession,
        preview: PreviewSession,
    ) -> PreviewSession:
        command = self._recovery_command(runtime, "preview.start")
        if command is None:
            return preview
        try:
            handle = runtime.executor_handle or self._executor.recovery_handle(
                RuntimeRecoveryTarget(
                    runtime_id=runtime.id,
                    preview_id=preview.id,
                    execution_target=runtime.execution_target,
                    kind=runtime.kind,
                    lease_fence=runtime.revision,
                )
            )
            probe = self._executor.probe(handle)
        except Exception as error:
            return self._mark_recovery_interrupted(runtime.id, preview.id, "preview.start", error)
        if probe.state is not ExecutorRuntimeState.RUNNING:
            return self._mark_recovery_interrupted(
                runtime.id,
                preview.id,
                "preview.start",
                RuntimeExecutorError("Runtime did not survive start"),
            )
        if probe.executor_handle != handle:
            return self._mark_recovery_interrupted(
                runtime.id,
                preview.id,
                "preview.start",
                RuntimeExecutorError(
                    "Runtime probe rebound the executor handle",
                    error_code="SCOPE_MISMATCH",
                ),
            )
        if probe.execution_target != runtime.execution_target:
            return self._mark_recovery_interrupted(
                runtime.id,
                preview.id,
                "preview.start",
                RuntimeExecutorError(
                    "Runtime probe rebound the execution target",
                    error_code="SCOPE_MISMATCH",
                ),
            )
        assert probe.port is not None and probe.url is not None
        return self._finish_recovered_start(runtime.id, preview.id, probe, command)

    def _finish_recovered_start(
        self,
        runtime_id: UUID,
        preview_id: UUID,
        probe: RuntimeProbeResult,
        command: CommandRun | None,
    ) -> PreviewSession:
        assert probe.port is not None and probe.url is not None
        with self._transaction() as (unit_of_work, commands):
            state = unit_of_work.state
            runtime = self._require_runtime(state, runtime_id)
            preview = self._require_preview(state, preview_id)
            task = self._require_task(state, preview.task_id)
            scope = self._scope_resolver(state, task)
            template = self._select_template(scope)
            generation = self._validate_template_dependencies(
                unit_of_work,
                task.id,
                scope,
                template,
            )
            if runtime.kind is not template.kind:
                raise RuntimeExecutorError(
                    "Recovered Runtime kind no longer matches the Project",
                    error_code="SCOPE_MISMATCH",
                )
            if runtime.status is RuntimeStatus.STARTING:
                revision = runtime.revision
                runtime.mark_running(executor_handle=probe.executor_handle, port=probe.port)
                state.save_runtime(runtime, expected_revision=revision)
            if preview.status is PreviewStatus.STARTING:
                revision = preview.revision
                preview.mark_ready(probe.url)
                state.save_preview(preview, expected_revision=revision)
            if task.status is TaskStatus.EXECUTING:
                task.transition_to(TaskStatus.PREVIEWING)
                state.save_task(task)
            conversation = self._require_conversation(state, preview.conversation_id)
            conversation.active_preview_id = preview.id
            state.save_conversation(conversation)
            manifest = ensure_preview_manifest(
                state,
                runtime=runtime,
                preview=preview,
                url=probe.url,
                command_run_id=command.id if command is not None else None,
                adapter=template.adapter.value,
                dependency_key=template.dependency_key,
                entry_path=template.entry_path,
                readiness_path=template.readiness_path,
                workspace_generation=generation,
            )
            if command is not None:
                unit_of_work.commands.append_event(
                    run_id=command.id,
                    event_type="preview.ready",
                    visibility=EventVisibility.USER,
                    message="Preview recovered",
                    payload={
                        "preview_id": str(preview.id),
                        "url": probe.url,
                        "artifact_id": str(manifest.id),
                    },
                    lease_owner=command.lease_owner,
                    lease_fence=command.lease_fence,
                )
                commands.complete(
                    command.id,
                    output={"preview_id": str(preview.id), "url": probe.url},
                    lease_owner=command.lease_owner,
                    lease_fence=command.lease_fence,
                )
            unit_of_work.commit()
        return preview

    def _recover_stop(
        self,
        runtime: RuntimeSession,
        preview: PreviewSession,
    ) -> PreviewSession:
        assert runtime.executor_handle is not None
        try:
            probe = self._executor.probe(runtime.executor_handle)
            if probe.state is ExecutorRuntimeState.RUNNING:
                self._executor.stop(runtime.executor_handle)
            elif probe.state is not ExecutorRuntimeState.STOPPED:
                raise RuntimeExecutorError("Runtime stop could not be confirmed")
        except Exception as error:
            return self._mark_recovery_interrupted(runtime.id, preview.id, "preview.stop", error)
        command = self._recovery_command(runtime, "preview.stop")
        return self._finish_stop(preview.id, command)

    def _mark_recovery_interrupted(
        self,
        runtime_id: UUID,
        preview_id: UUID,
        command_name: str,
        error: Exception,
    ) -> PreviewSession:
        error_code = str(getattr(error, "error_code", "WORKER_INTERRUPTED"))
        with self._transaction() as (unit_of_work, commands):
            state = unit_of_work.state
            runtime = self._require_runtime(state, runtime_id)
            preview = self._require_preview(state, preview_id)
            task = self._require_task(state, preview.task_id)
            if runtime.status in {
                RuntimeStatus.CREATED,
                RuntimeStatus.STARTING,
                RuntimeStatus.RUNNING,
                RuntimeStatus.STOPPING,
            }:
                revision = runtime.revision
                runtime.mark_interrupted(error_code)
                state.save_runtime(runtime, expected_revision=revision)
            if preview.status in {
                PreviewStatus.CREATED,
                PreviewStatus.STARTING,
                PreviewStatus.READY,
                PreviewStatus.STOPPING,
            }:
                revision = preview.revision
                preview.mark_interrupted(error_code)
                state.save_preview(preview, expected_revision=revision)
            if task.status in {
                TaskStatus.EXECUTING,
                TaskStatus.INSTALLING,
                TaskStatus.PREVIEWING,
                TaskStatus.REVIEWING,
                TaskStatus.REPAIRING,
            }:
                task.transition_to(TaskStatus.FAILED)
                state.save_task(task)
            command = self._prepare_recovery_command(
                unit_of_work.commands,
                commands,
                runtime,
                command_name,
            )
            if command is not None:
                commands.fail(
                    command.id,
                    error_code=error_code,
                    lease_owner=command.lease_owner,
                    lease_fence=command.lease_fence,
                )
            unit_of_work.commit()
        return preview
