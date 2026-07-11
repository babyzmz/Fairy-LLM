from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from fairy_core.assistant.tools import ToolExecutor, ToolResult, UnavailableToolExecutor
from fairy_core.commanding import CommandRun, CommandStatus
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolDefinition, ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.domain.errors import ScopeViolationError
from fairy_core.domain.execution import Artifact, ArtifactType, ArtifactVisibility
from fairy_core.domain.models import ScopeContract, Task, TaskStatus
from fairy_core.execution.templates import (
    ReviewKind,
    UnknownProjectManagerError,
    dependency_layer_key,
    dependency_template,
    review_template,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.sandbox.archive import WorkspaceArchiveBuilder
from fairy_core.sandbox.models import SandboxRequest, SandboxResult, SandboxResultStatus
from fairy_core.sandbox.ports import SandboxExecutor
from fairy_core.storage import StateStore

_DEPENDENCY_TOOL = "deps.install"
_REVIEW_TOOLS = {
    "review.typecheck": ReviewKind.TYPECHECK,
    "review.lint": ReviewKind.LINT,
    "review.test": ReviewKind.TEST,
    "review.build": ReviewKind.BUILD,
}
_PROJECT_EXECUTION_TOOLS = frozenset({_DEPENDENCY_TOOL, *_REVIEW_TOOLS})


class ScopeResolver(Protocol):
    def __call__(self, state: StateStore, task: Task) -> ScopeContract: ...


class ProjectExecutionFailedError(RuntimeError):
    def __init__(self, tool_name: str, *, error_code: str) -> None:
        super().__init__(f"{tool_name} failed ({error_code})")
        self.error_code = error_code
        self.code = error_code


class ProjectExecutionApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        sandbox_executor: SandboxExecutor,
        registry: ToolRegistry,
        policy: PolicyEngine,
        execution_policy: ExecutionPolicyResolver,
        scope_resolver: ScopeResolver,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._sandbox_executor = sandbox_executor
        self._registry = registry
        self._policy = policy
        self._execution_policy = execution_policy
        self._scope_resolver = scope_resolver
        self._archive_builder = WorkspaceArchiveBuilder(unit_of_work_factory)

    def execute_command(
        self,
        *,
        tool_name: str,
        scope: ScopeContract,
        command_run: CommandRun,
    ) -> ToolResult:
        if tool_name not in _PROJECT_EXECUTION_TOOLS:
            raise ValueError(f"unsupported project execution tool: {tool_name}")
        self._validate_command(tool_name, scope, command_run)
        existing = self._existing_report(scope.task_id, command_run.id)
        if existing is not None:
            return self._tool_result(existing)
        self._begin_phase(scope.task_id, tool_name)
        template = (
            dependency_template(scope.project_root)
            if tool_name == _DEPENDENCY_TOOL
            else review_template(scope.project_root, _REVIEW_TOOLS[tool_name])
        )
        archive = self._archive_builder.build(scope)
        dependency_key = dependency_layer_key(scope.project_root, template.manager)
        request = SandboxRequest.create(
            job_id=command_run.id,
            project_id=scope.project_id,
            conversation_id=scope.conversation_id,
            task_id=scope.task_id,
            version_id=scope.target_version_id,
            scope_digest=scope.scope_digest,
            workspace_generation=archive.generation,
            lease_fence=command_run.lease_fence,
            argv=template.argv,
            cwd=template.cwd,
            environment={},
            timeout_seconds=template.timeout_seconds,
            output_limit_bytes=template.output_limit_bytes,
            network_policy=template.network_policy,
            workspace_archive=archive.content,
            purpose=template.purpose,
            dependency_key=dependency_key,
            dependency_manager=template.manager.value,
        )
        try:
            result = self._sandbox_executor.execute(request)
            self._validate_result(result, request)
        except Exception as error:
            artifact = self._record_exception(
                scope=scope,
                command_run=command_run,
                tool_name=tool_name,
                manager=template.manager.value,
                generation=archive.generation,
                dependency_key=dependency_key,
                error=error,
            )
            self._mark_repairing(scope.task_id)
            raise ProjectExecutionFailedError(
                tool_name,
                error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
            ) from error

        artifact = self._record_result(
            scope=scope,
            command_run=command_run,
            tool_name=tool_name,
            manager=template.manager.value,
            generation=archive.generation,
            dependency_key=dependency_key,
            result=result,
        )
        if result.status is not SandboxResultStatus.COMPLETED:
            self._mark_repairing(scope.task_id)
            raise ProjectExecutionFailedError(
                tool_name,
                error_code=(
                    "DEPENDENCY_FAILED" if tool_name == _DEPENDENCY_TOOL else "REVIEW_FAILED"
                ),
            )
        if tool_name == _DEPENDENCY_TOOL:
            self._finish_install(scope.task_id)
        return self._tool_result(artifact)

    def run_review_suite(self, task_id: UUID) -> tuple[Artifact, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            task = _require_task(unit_of_work.state, task_id)
            scope = self._scope_resolver(unit_of_work.state, task)
        try:
            review_template(scope.project_root, ReviewKind.TYPECHECK)
        except UnknownProjectManagerError:
            return ()
        reports: list[Artifact] = []
        for tool_name in _REVIEW_TOOLS:
            running, scope = self._start_review_command(task_id, tool_name)
            if running.status is CommandStatus.SUCCEEDED:
                existing = self._existing_report(task_id, running.id)
                if existing is None:
                    raise RuntimeError("succeeded Review command has no durable report")
                reports.append(existing)
                continue
            try:
                result = self.execute_command(
                    tool_name=tool_name,
                    scope=scope,
                    command_run=running,
                )
            except Exception as error:
                self._fail_command(running, error)
                raise
            self._complete_command(running, result)
            report = self._existing_report(task_id, running.id)
            if report is None:
                raise RuntimeError("completed Review command has no durable report")
            reports.append(report)
        return tuple(reports)

    def _start_review_command(
        self,
        task_id: UUID,
        tool_name: str,
    ) -> tuple[CommandRun, ScopeContract]:
        with self._unit_of_work_factory() as unit_of_work:
            task = _require_task(unit_of_work.state, task_id)
            scope = self._scope_resolver(unit_of_work.state, task)
            index = (
                unit_of_work.project_indexes.get(scope.target_version_id)
                if scope.target_version_id is not None
                else None
            )
            if index is None:
                raise ScopeViolationError(
                    "Review requires a current Project Index",
                    code="SCOPE_MISMATCH",
                )
            policy = self._execution_policy.resolve(
                unit_of_work.execution_settings,
                execution_target=scope.execution_target,
            )
            bus = self._command_bus(unit_of_work.commands)
            dispatch = bus.submit(
                CommandRequest(
                    tool_name=tool_name,
                    actor="user",
                    scope=scope,
                    payload={},
                    idempotency_key=(f"task:{task.id}:{tool_name}:generation:{index.generation}"),
                ),
                profile=policy.profile,
                capability_overrides=dict(policy.capability_overrides),
                sandbox_healthy=policy.sandbox_healthy,
            )
            if not dispatch.accepted or dispatch.run is None:
                raise ProjectExecutionFailedError(
                    tool_name,
                    error_code=dispatch.error_code or "CAPABILITY_NOT_AVAILABLE",
                )
            if dispatch.requires_approval:
                raise ProjectExecutionFailedError(
                    tool_name,
                    error_code="APPROVAL_REQUIRED",
                )
            run = dispatch.run
            if run.status is CommandStatus.QUEUED:
                template = review_template(scope.project_root, _REVIEW_TOOLS[tool_name])
                run = bus.start(
                    run.id,
                    worker_id=f"review:{task.id}:{tool_name}",
                    lease_until=datetime.now(UTC)
                    + timedelta(seconds=template.timeout_seconds + 30),
                )
            if run.status not in {CommandStatus.RUNNING, CommandStatus.SUCCEEDED}:
                raise ProjectExecutionFailedError(
                    tool_name,
                    error_code="WORKER_INTERRUPTED",
                )
            unit_of_work.commit()
        return run, scope

    def _complete_command(self, running: CommandRun, result: ToolResult) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            self._command_bus(unit_of_work.commands).complete(
                running.id,
                output={
                    "public_summary": result.public_summary,
                    "artifact_ids": [str(value) for value in result.artifact_ids],
                },
                lease_owner=running.lease_owner,
                lease_fence=running.lease_fence,
            )
            unit_of_work.commit()

    def _fail_command(self, running: CommandRun, error: Exception) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            persisted = unit_of_work.commands.get_run(running.id)
            if persisted is not None and persisted.status is CommandStatus.RUNNING:
                self._command_bus(unit_of_work.commands).fail(
                    running.id,
                    error_code=str(
                        getattr(
                            error,
                            "error_code",
                            getattr(error, "code", "WORKER_INTERRUPTED"),
                        )
                    ),
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                unit_of_work.commit()

    def _begin_phase(self, task_id: UUID, tool_name: str) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            task = _require_task(unit_of_work.state, task_id)
            if tool_name == _DEPENDENCY_TOOL:
                if task.status is TaskStatus.AWAITING_APPROVAL:
                    task.transition_to(TaskStatus.EXECUTING)
                if task.status is TaskStatus.REPAIRING:
                    task.transition_to(TaskStatus.EXECUTING)
                if task.status is TaskStatus.EXECUTING:
                    task.transition_to(TaskStatus.INSTALLING)
                elif task.status is not TaskStatus.INSTALLING:
                    raise ProjectExecutionFailedError(
                        tool_name,
                        error_code="INVALID_STATE_TRANSITION",
                    )
            else:
                if task.status in {
                    TaskStatus.EXECUTING,
                    TaskStatus.INSTALLING,
                    TaskStatus.PREVIEWING,
                    TaskStatus.REPAIRING,
                }:
                    task.transition_to(TaskStatus.REVIEWING)
                elif task.status is not TaskStatus.REVIEWING:
                    raise ProjectExecutionFailedError(
                        tool_name,
                        error_code="INVALID_STATE_TRANSITION",
                    )
            unit_of_work.state.save_task(task)
            unit_of_work.commit()

    def _finish_install(self, task_id: UUID) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            task = _require_task(unit_of_work.state, task_id)
            if task.status is TaskStatus.INSTALLING:
                task.transition_to(TaskStatus.EXECUTING)
                unit_of_work.state.save_task(task)
                unit_of_work.commit()

    def _mark_repairing(self, task_id: UUID) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            task = _require_task(unit_of_work.state, task_id)
            if task.status in {TaskStatus.INSTALLING, TaskStatus.REVIEWING}:
                task.transition_to(TaskStatus.REPAIRING)
                unit_of_work.state.save_task(task)
                unit_of_work.commit()

    def _record_result(
        self,
        *,
        scope: ScopeContract,
        command_run: CommandRun,
        tool_name: str,
        manager: str,
        generation: int,
        dependency_key: str,
        result: SandboxResult,
    ) -> Artifact:
        payload = {
            "schema_version": 1,
            "tool_name": tool_name,
            "command_run_id": str(command_run.id),
            "manager": manager,
            "workspace_generation": generation,
            "dependency_key": dependency_key,
            "status": result.status.value,
            "exit_code": result.exit_code,
            "executor": result.executor,
            "executor_version": result.executor_version,
            "stdout": result.stdout.decode("utf-8", errors="replace"),
            "stderr": result.stderr.decode("utf-8", errors="replace"),
            "stdout_sha256": result.stdout_sha256,
            "stderr_sha256": result.stderr_sha256,
            "output_truncated": result.output_truncated,
            "started_at": result.started_at.isoformat(),
            "finished_at": result.finished_at.isoformat(),
        }
        return self._append_report(scope, command_run.id, tool_name, payload)

    def _record_exception(
        self,
        *,
        scope: ScopeContract,
        command_run: CommandRun,
        tool_name: str,
        manager: str,
        generation: int,
        dependency_key: str,
        error: Exception,
    ) -> Artifact:
        payload = {
            "schema_version": 1,
            "tool_name": tool_name,
            "command_run_id": str(command_run.id),
            "manager": manager,
            "workspace_generation": generation,
            "dependency_key": dependency_key,
            "status": "interrupted",
            "error_code": str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
        }
        return self._append_report(scope, command_run.id, tool_name, payload)

    def _append_report(
        self,
        scope: ScopeContract,
        command_run_id: UUID,
        tool_name: str,
        payload: dict[str, object],
    ) -> Artifact:
        content = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        encoded = content.encode("utf-8")
        artifact = Artifact.create(
            project_id=scope.project_id,
            conversation_id=scope.conversation_id,
            task_id=scope.task_id,
            version_id=scope.target_version_id,
            artifact_type=(
                ArtifactType.LOG if tool_name == _DEPENDENCY_TOOL else ArtifactType.REPORT
            ),
            visibility=ArtifactVisibility.CONVERSATION,
            storage_location=f"inline://execution/{command_run_id}",
            media_type="application/json",
            byte_length=len(encoded),
            content_hash=hashlib.sha256(encoded).hexdigest(),
            metadata={
                "content": content,
                "command_run_id": str(command_run_id),
                "tool_name": tool_name,
                "workspace_generation": payload["workspace_generation"],
                "dependency_key": payload["dependency_key"],
                "status": payload["status"],
            },
        )
        with self._unit_of_work_factory() as unit_of_work:
            existing = _report_for_command(unit_of_work.state, scope.task_id, command_run_id)
            if existing is not None:
                return existing
            unit_of_work.state.append_artifact(artifact)
            unit_of_work.commit()
        return artifact

    def _existing_report(self, task_id: UUID, command_run_id: UUID) -> Artifact | None:
        with self._unit_of_work_factory() as unit_of_work:
            return _report_for_command(unit_of_work.state, task_id, command_run_id)

    @staticmethod
    def _tool_result(artifact: Artifact) -> ToolResult:
        content = artifact.metadata.get("content")
        status = artifact.metadata.get("status")
        tool_name = artifact.metadata.get("tool_name")
        if not isinstance(content, str) or not isinstance(status, str):
            raise RuntimeError("execution report Artifact is invalid")
        if status not in {SandboxResultStatus.COMPLETED.value}:
            raise ProjectExecutionFailedError(
                str(tool_name),
                error_code=(
                    "DEPENDENCY_FAILED" if tool_name == _DEPENDENCY_TOOL else "REVIEW_FAILED"
                ),
            )
        return ToolResult.create(
            public_summary=f"{tool_name} completed",
            model_content=content,
            artifact_ids=(artifact.id,),
        )

    def _command_bus(self, ledger) -> CommandBus:
        return CommandBus(registry=self._registry, policy=self._policy, ledger=ledger)

    @staticmethod
    def _validate_command(
        tool_name: str,
        scope: ScopeContract,
        command_run: CommandRun,
    ) -> None:
        if (
            command_run.command_name != tool_name
            or command_run.status is not CommandStatus.RUNNING
            or command_run.project_id != scope.project_id
            or command_run.conversation_id != scope.conversation_id
            or command_run.task_id != scope.task_id
            or command_run.scope_digest != scope.scope_digest
            or command_run.lease_fence < 1
            or scope.project_id is None
            or scope.target_version_id is None
        ):
            raise ScopeViolationError(
                "Project execution CommandRun does not match Core Scope",
                code="SCOPE_MISMATCH",
            )

    @staticmethod
    def _validate_result(result: SandboxResult, request: SandboxRequest) -> None:
        if (
            result.job_id != request.job_id
            or result.scope_digest != request.scope_digest
            or result.workspace_generation != request.workspace_generation
            or result.lease_fence != request.lease_fence
        ):
            raise ScopeViolationError(
                "Project execution result does not match Core Scope",
                code="SCOPE_MISMATCH",
            )


class ProjectExecutionToolExecutor:
    def __init__(
        self,
        *,
        application: ProjectExecutionApplication,
        delegate: ToolExecutor | None = None,
    ) -> None:
        self._application = application
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
        if definition.name in _PROJECT_EXECUTION_TOOLS:
            raise ProjectExecutionFailedError(
                definition.name,
                error_code="WORKER_INTERRUPTED",
            )
        return self._delegate.execute(definition, scope, arguments)

    def execute_command(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
        *,
        command_run: CommandRun,
    ) -> ToolResult:
        if definition.name in _PROJECT_EXECUTION_TOOLS:
            if arguments:
                raise ValueError("Project execution tools do not accept model-selected arguments")
            return self._application.execute_command(
                tool_name=definition.name,
                scope=scope,
                command_run=command_run,
            )
        delegated = getattr(self._delegate, "execute_command", None)
        if callable(delegated):
            return delegated(
                definition,
                scope,
                arguments,
                command_run=command_run,
            )
        return self._delegate.execute(definition, scope, arguments)


def _require_task(state: StateStore, task_id: UUID) -> Task:
    task = state.get_task(task_id)
    if task is None:
        raise KeyError(f"task not found: {task_id}")
    return task


def _report_for_command(
    state: StateStore,
    task_id: UUID,
    command_run_id: UUID,
) -> Artifact | None:
    expected = str(command_run_id)
    return next(
        (
            artifact
            for artifact in state.artifacts_for_task(task_id)
            if artifact.metadata.get("command_run_id") == expected
        ),
        None,
    )


__all__ = [
    "ProjectExecutionApplication",
    "ProjectExecutionFailedError",
    "ProjectExecutionToolExecutor",
]
