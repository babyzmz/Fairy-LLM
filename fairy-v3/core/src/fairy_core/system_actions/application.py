from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from fairy_core.assistant.tools import (
    DelegatingToolCancellation,
    ToolExecutor,
    ToolResult,
    UnavailableToolExecutor,
)
from fairy_core.commanding import (
    CommandRun,
    CommandStatus,
    EventVisibility,
)
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolDefinition, ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.domain.errors import DomainError, InvalidTransitionError
from fairy_core.domain.models import ScopeContract
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.system_actions.models import (
    CopyTextAction,
    NotifyAction,
    OpenSettingsAction,
    OpenUrlAction,
    RevealPathAction,
    SystemAction,
    SystemActionExecution,
    SystemActionRequest,
    SystemActionWorkerResult,
    tool_name_for_action,
)


class SystemActionWorker(Protocol):
    def execute(
        self,
        *,
        action: dict[str, object],
        idempotency_key: str,
    ) -> SystemActionWorkerResult: ...


class SystemActionUnavailableError(DomainError):
    def __init__(self, message: str, *, error_code: str = "CAPABILITY_NOT_AVAILABLE") -> None:
        self.error_code = error_code
        self.code = error_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class _ExecutionBinding:
    run_id: UUID
    tool_name: str
    scope_digest: str
    lease_owner: str | None
    lease_fence: int
    worker_payload: dict[str, object]


class SystemActionApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        registry: ToolRegistry,
        command_policy: PolicyEngine,
        execution_policy: ExecutionPolicyResolver,
        scope_resolver: Callable,
        worker: SystemActionWorker,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry
        self._command_policy = command_policy
        self._execution_policy = execution_policy
        self._scope_resolver = scope_resolver
        self._worker = worker

    def execute(self, request: SystemActionRequest) -> SystemActionExecution:
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(request.task_id)
            if task is None:
                raise KeyError(f"task not found: {request.task_id}")
            scope = self._scope_resolver(unit_of_work.state, task)
            tool_name = tool_name_for_action(request.action)
            worker_payload = _worker_payload(request.action, scope)
            bus = self._bus(unit_of_work)
            policy = self._execution_policy.resolve(
                unit_of_work.execution_settings,
                execution_target=scope.execution_target,
            )
            dispatch = bus.submit(
                CommandRequest(
                    tool_name=tool_name,
                    actor="desktop-user",
                    scope=scope,
                    payload=worker_payload,
                    idempotency_key=request.idempotency_key,
                ),
                profile=policy.profile,
                capability_overrides=dict(policy.capability_overrides),
                sandbox_healthy=policy.sandbox_healthy,
            )
            if dispatch.run is None:
                raise SystemActionUnavailableError(
                    dispatch.reason or "system action is unavailable",
                    error_code=dispatch.error_code or "CAPABILITY_NOT_AVAILABLE",
                )
            run = dispatch.run
            if run.status is CommandStatus.SUCCEEDED:
                return _execution(run, requires_approval=False, completed=True, replayed=True)
            if run.status in {
                CommandStatus.FAILED,
                CommandStatus.REJECTED,
                CommandStatus.CANCELLED,
            }:
                return _execution(
                    run,
                    requires_approval=False,
                    completed=False,
                    replayed=True,
                )
            if not dispatch.accepted:
                raise SystemActionUnavailableError(
                    dispatch.reason or "system action was rejected",
                    error_code=dispatch.error_code or "CAPABILITY_NOT_AVAILABLE",
                )
            if run.status is CommandStatus.WAITING_APPROVAL:
                if not request.user_confirmed:
                    unit_of_work.commit()
                    return _execution(
                        run,
                        requires_approval=True,
                        completed=False,
                        replayed=False,
                    )
                run = bus.decide_approval(run.id, approved=True)
            if run.status is CommandStatus.INTERRUPTED:
                run = unit_of_work.commands.transition(run.id, CommandStatus.QUEUED)
            if run.status is CommandStatus.RUNNING:
                try:
                    run = bus.start(run.id)
                except InvalidTransitionError:
                    return _execution(
                        run,
                        requires_approval=False,
                        completed=False,
                        replayed=True,
                    )
            elif run.status is CommandStatus.QUEUED:
                run = bus.start(run.id)
            if run.status is not CommandStatus.RUNNING:
                raise RuntimeError(f"system action cannot execute from {run.status.value}")
            binding = _ExecutionBinding(
                run_id=run.id,
                tool_name=tool_name,
                scope_digest=scope.scope_digest,
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
                worker_payload=worker_payload,
            )
            unit_of_work.commit()

        try:
            result = self._worker.execute(
                action=binding.worker_payload,
                idempotency_key=f"system:{binding.run_id}",
            )
            if not result.completed or f"system.{result.action_type}" != binding.tool_name:
                raise RuntimeError("system action worker returned an inconsistent result")
        except Exception as error:
            self._fail(binding, error)
            raise

        with self._unit_of_work_factory() as unit_of_work:
            running = _validated_running(unit_of_work, binding)
            unit_of_work.commands.append_event(
                run_id=running.id,
                event_type="system.action.completed",
                visibility=EventVisibility.USER,
                message="System action completed",
                payload={
                    "action_type": binding.tool_name,
                    "replayed": result.replayed,
                },
                lease_owner=running.lease_owner,
                lease_fence=running.lease_fence,
            )
            completed = self._bus(unit_of_work).complete(
                running.id,
                output={
                    "action_type": binding.tool_name,
                    "replayed": result.replayed,
                },
                lease_owner=running.lease_owner,
                lease_fence=running.lease_fence,
            )
            unit_of_work.commit()
        return _execution(
            completed,
            requires_approval=False,
            completed=True,
            replayed=result.replayed,
        )

    def _bus(self, unit_of_work: CoreUnitOfWork) -> CommandBus:
        return CommandBus(
            registry=self._registry,
            policy=self._command_policy,
            ledger=unit_of_work.commands,
        )

    def _fail(self, binding: _ExecutionBinding, error: Exception) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            run = unit_of_work.commands.get_run(binding.run_id)
            if run is None or run.status is not CommandStatus.RUNNING:
                return
            if run.scope_digest != binding.scope_digest or run.command_name != binding.tool_name:
                raise RuntimeError("system action CommandRun Scope changed")
            self._bus(unit_of_work).fail(
                run.id,
                error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()


class SystemActionToolExecutor(DelegatingToolCancellation):
    def __init__(
        self,
        *,
        worker: SystemActionWorker,
        delegate: ToolExecutor | None = None,
    ) -> None:
        self._worker = worker
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
        if not definition.name.startswith("system."):
            return self._delegate.execute(definition, scope, arguments)
        raise SystemActionUnavailableError(
            "system actions require a policy-approved CommandRun",
            error_code="APPROVAL_REQUIRED",
        )

    def execute_command(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
        *,
        command_run: CommandRun,
    ) -> ToolResult:
        if not definition.name.startswith("system."):
            execute_command = getattr(self._delegate, "execute_command", None)
            if callable(execute_command):
                return execute_command(
                    definition,
                    scope,
                    arguments,
                    command_run=command_run,
                )
            return self._delegate.execute(definition, scope, arguments)
        if (
            command_run.command_name != definition.name
            or command_run.status is not CommandStatus.RUNNING
            or command_run.scope_digest != scope.scope_digest
            or command_run.lease_owner is None
            or command_run.lease_fence < 1
        ):
            raise SystemActionUnavailableError(
                "system action CommandRun does not match Core Scope",
                error_code="SCOPE_MISMATCH",
            )
        action = _action_from_tool(definition.name, arguments)
        payload = _worker_payload(action, scope)
        result = self._worker.execute(
            action=payload,
            idempotency_key=f"system:{command_run.id}",
        )
        if not result.completed or f"system.{result.action_type}" != definition.name:
            raise RuntimeError("system action worker returned an inconsistent result")
        return ToolResult.create(
            public_summary=f"System action completed: {definition.name}",
            model_content=f"System action completed: {definition.name}",
            artifact_ids=(),
        )


def _worker_payload(action: SystemAction, scope: ScopeContract) -> dict[str, object]:
    if isinstance(action, OpenUrlAction):
        return {"type": action.type, "url": action.url}
    if isinstance(action, CopyTextAction):
        return {"type": action.type, "text": action.text}
    if isinstance(action, NotifyAction):
        return {
            "type": action.type,
            "title": action.title,
            "body": action.body,
            "level": action.level.value,
        }
    if isinstance(action, OpenSettingsAction):
        return {"type": action.type, "page": action.page.value}
    if isinstance(action, RevealPathAction):
        version_id = scope.target_version_id or scope.base_version_id
        if scope.project_id is None or version_id is None:
            raise SystemActionUnavailableError(
                "reveal path requires a managed project Version",
                error_code="SCOPE_MISMATCH",
            )
        return {
            "type": action.type,
            "project_id": str(scope.project_id),
            "version_id": str(version_id),
            "relative_path": action.relative_path,
        }
    raise TypeError(f"unsupported system action: {type(action).__name__}")


def _action_from_tool(name: str, arguments: dict[str, object]) -> SystemAction:
    action_type = name.removeprefix("system.")
    model_by_type = {
        "open_url": OpenUrlAction,
        "reveal_path": RevealPathAction,
        "copy_text": CopyTextAction,
        "notify": NotifyAction,
        "open_settings": OpenSettingsAction,
    }
    model = model_by_type.get(action_type)
    if model is None:
        raise SystemActionUnavailableError(f"unknown system action: {name}")
    try:
        return model.model_validate({"type": action_type, **arguments})
    except ValidationError as error:
        raise ValueError("system action arguments are invalid") from error


def _validated_running(
    unit_of_work: CoreUnitOfWork,
    binding: _ExecutionBinding,
) -> CommandRun:
    run = unit_of_work.commands.get_run(binding.run_id)
    if run is None or run.status is not CommandStatus.RUNNING:
        raise RuntimeError("system action requires a running CommandRun")
    if (
        run.command_name != binding.tool_name
        or run.scope_digest != binding.scope_digest
        or run.lease_owner != binding.lease_owner
        or run.lease_fence != binding.lease_fence
    ):
        raise RuntimeError("system action CommandRun Scope or lease changed")
    return run


def _execution(
    run: CommandRun,
    *,
    requires_approval: bool,
    completed: bool,
    replayed: bool,
) -> SystemActionExecution:
    return SystemActionExecution(
        run_id=run.id,
        tool_name=run.command_name,
        status=run.status,
        requires_approval=requires_approval,
        completed=completed,
        replayed=replayed,
    )


__all__ = [
    "SystemActionApplication",
    "SystemActionToolExecutor",
    "SystemActionUnavailableError",
    "SystemActionWorker",
]
