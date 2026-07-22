from __future__ import annotations

import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from fairy_core.application.runtime_contracts import PreviewContext
from fairy_core.commanding import CommandLedger, CommandRun, CommandStatus
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.execution import (
    PreviewSession,
    PreviewStatus,
    PreviewVisibility,
    RuntimeSession,
)
from fairy_core.domain.models import Conversation, Project, ScopeContract, Task
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.runtime.models import RuntimeExecutorError
from fairy_core.runtime.ports import RuntimeExecutor
from fairy_core.sandbox.archive import WorkspaceArchiveBuilder
from fairy_core.storage import StateStore

ScopeResolver = Callable[[StateStore, Task], ScopeContract]


class RuntimeApplicationSupport:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        executor: RuntimeExecutor,
        registry: ToolRegistry,
        policy: PolicyEngine,
        scope_resolver: ScopeResolver,
        execution_policy: ExecutionPolicyResolver | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._executor = executor
        self._registry = registry
        self._policy = policy
        self._scope_resolver = scope_resolver
        self._execution_policy = execution_policy or ExecutionPolicyResolver()
        self._archive_builder = WorkspaceArchiveBuilder(unit_of_work_factory)
        self._instance_id = uuid4().hex
        self._operation_lock = RLock()

    @staticmethod
    def _require_bound_task(
        state: StateStore,
        *,
        task_id: UUID,
        workspace_id: UUID | None,
        version_id: UUID | None,
        expected_workspace_revision: int | None = None,
    ) -> Task:
        task = RuntimeApplicationSupport._require_task(state, task_id)
        if workspace_id is None and version_id is None:
            return task
        if workspace_id is None or version_id is None:
            raise RuntimeExecutorError(
                "Preview Scope binding is incomplete",
                error_code="SCOPE_MISMATCH",
            )
        if task.workspace_id != workspace_id or task.target_version_id != version_id:
            raise RuntimeExecutorError(
                "Preview does not match the Task Workspace Version",
                error_code="SCOPE_MISMATCH",
            )
        workspace = state.get_workspace(workspace_id)
        if workspace is None:
            raise RuntimeExecutorError(
                "Preview Workspace is unavailable",
                error_code="SCOPE_MISMATCH",
            )
        if (
            expected_workspace_revision is not None
            and workspace.revision != expected_workspace_revision
        ):
            raise RuntimeExecutorError(
                "Preview Workspace revision has changed",
                error_code="VERSION_CONFLICT",
            )
        return task

    @staticmethod
    def _validate_preview_binding(
        preview: PreviewSession,
        *,
        task_id: UUID | None,
        workspace_id: UUID | None,
        version_id: UUID | None,
    ) -> None:
        supplied = (task_id, workspace_id, version_id)
        if (
            workspace_id is None
            and version_id is None
            and task_id
            in {
                None,
                preview.task_id,
            }
        ):
            return
        if None in supplied or supplied != (
            preview.task_id,
            preview.workspace_id,
            preview.version_id,
        ):
            raise RuntimeExecutorError(
                "Preview identity does not match the Task Workspace Version",
                error_code="SCOPE_MISMATCH",
            )

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
        unit_of_work: CoreUnitOfWork,
        commands: CommandBus,
        *,
        tool_name: str,
        scope: ScopeContract,
        payload: dict[str, object],
        idempotency_key: str,
        worker_id: str,
    ) -> CommandRun:
        effective_policy = self._execution_policy.resolve(
            unit_of_work.execution_settings,
            execution_target=scope.execution_target,
        )
        dispatch = commands.submit(
            CommandRequest(
                tool_name=tool_name,
                actor="user",
                scope=scope,
                payload=payload,
                idempotency_key=idempotency_key,
            ),
            profile=effective_policy.profile,
            capability_overrides=dict(effective_policy.capability_overrides),
            sandbox_healthy=effective_policy.sandbox_healthy,
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
        if scope.target_version_id is None or scope.execution_target != "local":
            raise RuntimeExecutorError(
                "Static Preview requires a local immutable Workspace Version",
                error_code="SCOPE_MISMATCH",
            )

    @staticmethod
    def _validate_project_scope(scope: ScopeContract) -> None:
        if scope.target_version_id is None or scope.execution_target not in {"local", "cloud"}:
            raise RuntimeExecutorError(
                "Dynamic Preview requires an immutable Workspace Version Scope",
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

    @staticmethod
    def _set_active_preview(state: StateStore, preview: PreviewSession) -> None:
        conversation = RuntimeApplicationSupport._require_conversation(
            state,
            preview.conversation_id,
        )
        conversation.active_preview_id = preview.id
        state.save_conversation(conversation)
        if (
            preview.visibility is PreviewVisibility.PROJECT_ACTIVE
            and preview.project_id is not None
        ):
            project = RuntimeApplicationSupport._require_project(state, preview.project_id)
            project.active_preview_id = preview.id
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
