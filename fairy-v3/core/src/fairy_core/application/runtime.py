from __future__ import annotations

import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from fairy_core.commanding import (
    CommandLedger,
    CommandRun,
    CommandStatus,
    EventVisibility,
)
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.policy import PermissionProfile, PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.domain.execution import (
    PreviewSession,
    PreviewStatus,
    PreviewVisibility,
    RuntimeKind,
    RuntimeSession,
    RuntimeStatus,
)
from fairy_core.domain.models import Conversation, Project, ScopeContract, Task, TaskStatus
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.runtime.models import (
    ExecutorRuntimeState,
    RuntimeExecutorError,
    RuntimeExecutorHealth,
    RuntimeProbeResult,
    RuntimeStartResult,
    StaticRuntimeStart,
)
from fairy_core.runtime.ports import RuntimeExecutor
from fairy_core.storage import StateStore

ScopeResolver = Callable[[StateStore, Task], ScopeContract]


@dataclass(frozen=True, slots=True)
class PreviewStartRequest:
    task_id: UUID
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")


@dataclass(frozen=True, slots=True)
class PreviewStopRequest:
    preview_id: UUID
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")


@dataclass(frozen=True, slots=True)
class PreviewResolveRequest:
    conversation_id: UUID
    preview_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PreviewContext:
    task: Task
    runtime: RuntimeSession
    preview: PreviewSession


@dataclass(frozen=True, slots=True)
class RuntimeHealthResult:
    executor: RuntimeExecutorHealth
    runtime: RuntimeSession | None
    preview: PreviewSession | None


@dataclass(frozen=True, slots=True)
class _StartIntent:
    context: PreviewContext
    command: CommandRun


@dataclass(frozen=True, slots=True)
class _StopIntent:
    preview: PreviewSession
    runtime: RuntimeSession
    command: CommandRun
    executor_handle: str


class RuntimeApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        executor: RuntimeExecutor,
        registry: ToolRegistry,
        policy: PolicyEngine,
        scope_resolver: ScopeResolver,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._executor = executor
        self._registry = registry
        self._policy = policy
        self._scope_resolver = scope_resolver
        self._instance_id = uuid4().hex
        self._operation_lock = RLock()

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
            result = self._executor.start_static(
                StaticRuntimeStart(
                    project_id=runtime.project_id,
                    version_id=runtime.version_id,
                    preview_id=preview.id,
                    project_root=runtime.project_root,
                )
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
        health = self._executor.health()
        if not health.available:
            raise RuntimeExecutorError(
                "Runtime executor is unavailable",
                error_code=health.error_code or "WORKER_INTERRUPTED",
            )
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
                self._validate_static_scope(scope)
                self._validate_static_entry(scope.project_root)
                runtime = RuntimeSession.create(
                    scope=scope,
                    kind=RuntimeKind.STATIC_SITE,
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

            scope = self._scope_resolver(state, task)
            command = self._start_user_command(
                commands,
                tool_name="preview.start",
                scope=scope,
                payload={
                    "preview_id": str(preview.id),
                    "runtime_id": str(runtime.id),
                    "version_id": str(runtime.version_id),
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
            unit_of_work.commands.append_event(
                run_id=intent.command.id,
                event_type="preview.ready",
                visibility=EventVisibility.USER,
                message="Preview ready",
                payload={"preview_id": str(preview.id), "url": result.url},
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
        handle = runtime.executor_handle or f"static:{preview.id}"
        try:
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
        assert probe.port is not None and probe.url is not None
        command = self._recovery_command(runtime, "preview.start")
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
            if command is not None:
                unit_of_work.commands.append_event(
                    run_id=command.id,
                    event_type="preview.ready",
                    visibility=EventVisibility.USER,
                    message="Preview recovered",
                    payload={"preview_id": str(preview.id), "url": probe.url},
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

    def _recovery_command(
        self,
        runtime: RuntimeSession,
        command_name: str,
    ) -> CommandRun | None:
        with self._transaction() as (unit_of_work, commands):
            run = self._prepare_recovery_command(
                unit_of_work.commands,
                commands,
                runtime,
                command_name,
            )
            unit_of_work.commit()
            return run

    def _prepare_recovery_command(
        self,
        ledger: CommandLedger,
        commands: CommandBus,
        runtime: RuntimeSession,
        command_name: str,
    ) -> CommandRun | None:
        run = self._active_recovery_command(ledger, runtime, command_name)
        if run is None:
            return None
        expected_worker = (
            self._start_worker_id(runtime.id)
            if command_name == "preview.start"
            else self._stop_worker_id(runtime.id)
        )
        now = datetime.now(UTC)
        if run.status is CommandStatus.RUNNING:
            if (
                run.lease_owner == expected_worker
                and run.lease_until is not None
                and run.lease_until > now
            ):
                return run
            if run.lease_until is not None and run.lease_until <= now:
                return commands.start(run.id, worker_id=expected_worker)
            return None
        if run.status is CommandStatus.INTERRUPTED:
            run = ledger.transition(run.id, CommandStatus.QUEUED)
        if run.status is CommandStatus.WAITING_APPROVAL:
            run = commands.decide_approval(run.id, approved=True)
        if run.status is CommandStatus.CREATED:
            run = ledger.transition(run.id, CommandStatus.QUEUED)
        if run.status is CommandStatus.QUEUED:
            return commands.start(run.id, worker_id=expected_worker)
        return None

    def _recovery_blocked_by_live_lease(
        self,
        runtime: RuntimeSession,
        command_name: str,
    ) -> bool:
        with self._transaction() as (unit_of_work, _commands):
            run = self._active_recovery_command(
                unit_of_work.commands,
                runtime,
                command_name,
            )
        if (
            run is None
            or run.status is not CommandStatus.RUNNING
            or run.lease_until is None
            or run.lease_until <= datetime.now(UTC)
        ):
            return False
        expected_owner = (
            self._start_worker_id(runtime.id)
            if command_name == "preview.start"
            else self._stop_worker_id(runtime.id)
        )
        return run.lease_owner != expected_owner

    @staticmethod
    def _active_recovery_command(
        ledger: CommandLedger,
        runtime: RuntimeSession,
        command_name: str,
    ) -> CommandRun | None:
        run = ledger.active_run_for_task(runtime.task_id, command_name)
        if run is None:
            return None
        runtime_id = run.input_payload.get("runtime_id")
        return run if runtime_id == str(runtime.id) else None

    def _start_user_command(
        self,
        commands: CommandBus,
        *,
        tool_name: str,
        scope: ScopeContract,
        payload: dict[str, object],
        idempotency_key: str,
        worker_id: str,
    ) -> CommandRun:
        dispatch = commands.submit(
            CommandRequest(
                tool_name=tool_name,
                actor="user",
                scope=scope,
                payload=payload,
                idempotency_key=idempotency_key,
            ),
            profile=PermissionProfile.STANDARD,
            capability_overrides={},
            sandbox_healthy=False,
        )
        if not dispatch.accepted or dispatch.run is None:
            raise RuntimeExecutorError(
                dispatch.reason or "Preview command was rejected",
                error_code=dispatch.error_code or "APPROVAL_REQUIRED",
            )
        run = dispatch.run
        if dispatch.requires_approval:
            run = commands.decide_approval(run.id, approved=True)
        if run.status is CommandStatus.QUEUED:
            run = commands.start(run.id, worker_id=worker_id)
        if run.status is not CommandStatus.RUNNING:
            raise InvalidTransitionError(f"Preview command cannot execute from {run.status}")
        return run

    @staticmethod
    def _validate_static_scope(scope: ScopeContract) -> None:
        if (
            scope.workspace_type.value != "project_chat"
            or scope.project_id is None
            or scope.target_version_id is None
            or scope.execution_target != "local"
        ):
            raise RuntimeExecutorError(
                "Static Preview requires a local Project draft",
                error_code="SCOPE_MISMATCH",
            )

    @staticmethod
    def _validate_static_entry(project_root: Path) -> None:
        try:
            unresolved_root = Path(project_root)
            root_stat = unresolved_root.lstat()
            root = unresolved_root.resolve(strict=True)
            entry = root / "index.html"
            entry_stat = entry.lstat()
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if (
                stat.S_ISLNK(root_stat.st_mode)
                or stat.S_ISLNK(entry_stat.st_mode)
                or (getattr(root_stat, "st_file_attributes", 0) & reparse_flag)
                or (getattr(entry_stat, "st_file_attributes", 0) & reparse_flag)
            ):
                raise ValueError("static entry cannot traverse a reparse point")
            resolved = entry.resolve(strict=True)
            resolved.relative_to(root)
            if not resolved.is_file():
                raise ValueError("static entry must be a regular file")
        except (OSError, ValueError) as error:
            raise RuntimeExecutorError(
                "Static Preview entry is outside the managed Version",
                error_code="PATH_OUT_OF_SCOPE",
            ) from error

    @staticmethod
    def _is_resolvable(preview: PreviewSession) -> bool:
        return preview.url is not None and preview.status in {
            PreviewStatus.READY,
            PreviewStatus.STOPPING,
            PreviewStatus.INTERRUPTED,
        }

    @staticmethod
    def _preview_for_runtime(
        state: StateStore,
        runtime: RuntimeSession,
    ) -> PreviewSession | None:
        return next(
            (
                preview
                for preview in reversed(state.previews_for_conversation(runtime.conversation_id))
                if preview.runtime_id == runtime.id
            ),
            None,
        )

    def _context_for_preview(
        self,
        state: StateStore,
        preview: PreviewSession,
    ) -> PreviewContext:
        return PreviewContext(
            task=self._require_task(state, preview.task_id),
            runtime=self._require_runtime(state, preview.runtime_id),
            preview=preview,
        )

    @staticmethod
    def _clear_active_preview(state: StateStore, preview: PreviewSession) -> None:
        conversation = state.get_conversation(preview.conversation_id)
        if conversation is not None and conversation.active_preview_id == preview.id:
            conversation.active_preview_id = None
            state.save_conversation(conversation)
        if preview.project_id is not None:
            project = state.get_project(preview.project_id)
            if project is not None and project.active_preview_id == preview.id:
                project.active_preview_id = None
                state.save_project(project)

    def _start_worker_id(self, runtime_id: UUID) -> str:
        return f"runtime:{runtime_id}:{self._instance_id}"

    def _stop_worker_id(self, runtime_id: UUID) -> str:
        return f"runtime:{runtime_id}:{self._instance_id}:stop"

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

    @staticmethod
    def _require_task(state: StateStore, task_id: UUID) -> Task:
        task = state.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return task

    @staticmethod
    def _require_runtime(state: StateStore, runtime_id: UUID) -> RuntimeSession:
        runtime = state.get_runtime(runtime_id)
        if runtime is None:
            raise KeyError(f"Runtime not found: {runtime_id}")
        return runtime

    @staticmethod
    def _require_preview(state: StateStore, preview_id: UUID) -> PreviewSession:
        preview = state.get_preview(preview_id)
        if preview is None:
            raise KeyError(f"Preview not found: {preview_id}")
        return preview

    @staticmethod
    def _require_conversation(state: StateStore, conversation_id: UUID) -> Conversation:
        conversation = state.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(f"Conversation not found: {conversation_id}")
        return conversation

    @staticmethod
    def _require_project(state: StateStore, project_id: UUID) -> Project:
        project = state.get_project(project_id)
        if project is None:
            raise KeyError(f"Project not found: {project_id}")
        return project
