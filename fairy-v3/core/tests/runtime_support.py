from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy.engine import Engine

from fairy_core.application.core import CoreApplication, TaskContext
from fairy_core.application.runtime import RuntimeApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import (
    ChangesetProposal,
    ExecutionTarget,
    FileMutation,
    TaskCreate,
)
from fairy_core.domain.models import OperationMode, ProjectResidency, WorkspaceType
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.runtime.models import (
    ExecutorRuntimeState,
    RuntimeExecutorError,
    RuntimeExecutorHealth,
    RuntimeProbeResult,
    RuntimeRecoveryTarget,
    RuntimeStartResult,
    RuntimeStopResult,
    StaticRuntimeStart,
)


class FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def version_path(self, project_id: UUID | str, version_id: UUID | str) -> Path:
        return self.root / "projects" / str(project_id) / "versions" / str(version_id)

    def create_initial_version(
        self,
        project_id: UUID | str,
        version_id: UUID | str,
        *,
        source: Path | None = None,
    ) -> Path:
        target = self.version_path(project_id, version_id)
        if source is None:
            target.mkdir(parents=True)
        else:
            shutil.copytree(source, target)
        return target.resolve()

    def fork_version(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        parent_version_id: UUID | str,
    ) -> Path:
        target = self.version_path(project_id, version_id)
        shutil.copytree(self.version_path(project_id, parent_version_id), target)
        return target.resolve()

    def create_scratch(self, conversation_id: UUID | str, task_id: UUID | str) -> Path:
        target = self.scratch_path(conversation_id, task_id)
        target.mkdir(parents=True, exist_ok=True)
        return target.resolve()

    def scratch_path(self, conversation_id: UUID | str, task_id: UUID | str) -> Path:
        return self.root / "scratch" / str(conversation_id) / str(task_id)

    def write_text(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        relative_path: str,
        content: str,
    ) -> Path:
        target = self.version_path(project_id, version_id) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def apply_changeset(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        mutations: tuple[tuple[str, str], ...],
    ) -> tuple[Path, ...]:
        return tuple(
            self.write_text(
                project_id=project_id,
                version_id=version_id,
                relative_path=path,
                content=content,
            )
            for path, content in mutations
        )

    def diff(self, *, project_id: UUID | str, version_id: UUID | str) -> str:
        return "M index.html"

    def checkpoint(
        self,
        *,
        project_id: UUID | str,
        version_id: UUID | str,
        message: str,
    ) -> str:
        return "a" * 40

    def discard_version(self, *, project_id: UUID | str, version_id: UUID | str) -> None:
        shutil.rmtree(self.version_path(project_id, version_id))


class FakeRuntimeExecutor:
    def __init__(self) -> None:
        self.start_calls: list[StaticRuntimeStart] = []
        self.probe_calls: list[str] = []
        self.stop_calls: list[str] = []
        self.probes: dict[str, RuntimeProbeResult | RuntimeExecutorError] = {}
        self.start_failure: RuntimeExecutorError | None = None
        self.stop_failure: RuntimeExecutorError | None = None
        self.crash_after_start = False
        self.crash_after_stop = False

    def health(self) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=True,
            executor="fake_runtime",
            version="1",
            error_code=None,
            diagnostics=(),
        )

    def start_static(self, request: StaticRuntimeStart) -> RuntimeStartResult:
        self.start_calls.append(request)
        if self.start_failure is not None:
            raise self.start_failure
        handle = f"static:{request.preview_id}"
        url = f"http://127.0.0.1:43125/{request.preview_id}/"
        result = RuntimeStartResult(
            executor_handle=handle,
            host="127.0.0.1",
            port=43125,
            url=url,
            state=ExecutorRuntimeState.RUNNING,
        )
        self.probes[handle] = RuntimeProbeResult(
            executor_handle=handle,
            state=ExecutorRuntimeState.RUNNING,
            host="127.0.0.1",
            port=43125,
            url=url,
        )
        if self.crash_after_start:
            raise SystemExit("crash after external start")
        return result

    def probe(self, executor_handle: str) -> RuntimeProbeResult:
        self.probe_calls.append(executor_handle)
        result = self.probes.get(
            executor_handle,
            RuntimeExecutorError("missing runtime", error_code="WORKER_INTERRUPTED"),
        )
        if isinstance(result, RuntimeExecutorError):
            raise result
        return result

    def recovery_handle(self, target: RuntimeRecoveryTarget) -> str:
        return f"static:{target.preview_id}"

    def stop(self, executor_handle: str) -> RuntimeStopResult:
        self.stop_calls.append(executor_handle)
        if self.stop_failure is not None:
            raise self.stop_failure
        current = self.probes.get(executor_handle)
        if isinstance(current, RuntimeProbeResult):
            self.probes[executor_handle] = RuntimeProbeResult(
                executor_handle=executor_handle,
                state=ExecutorRuntimeState.STOPPED,
                host=current.host,
                port=current.port,
                url=current.url,
            )
        if self.crash_after_stop:
            raise SystemExit("crash after external stop")
        return RuntimeStopResult(stopped=True)


@dataclass(slots=True)
class RuntimeStack:
    core: CoreApplication
    runtime: RuntimeApplication
    factory: SqlAlchemyUnitOfWorkFactory
    engine: Engine
    executor: FakeRuntimeExecutor
    task: TaskContext
    project_revision: int


def build_runtime_stack(tmp_path: Path) -> RuntimeStack:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    workspace = FakeWorkspace(tmp_path / "managed")
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=workspace,
        registry=registry,
        policy=PolicyEngine(registry),
    )
    executor = FakeRuntimeExecutor()
    runtime = RuntimeApplication(
        unit_of_work_factory=factory,
        executor=executor,
        registry=registry,
        policy=PolicyEngine(registry),
        scope_resolver=core.scope_for_task,
    )
    project = core.create_project(name="Runtime", residency=ProjectResidency.LOCAL_ONLY)
    conversation = core.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = core.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Build a static preview",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="runtime:task",
        )
    )
    pending = core.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(FileMutation(path="index.html", content="<h1>Preview</h1>"),),
            reason="Create the static entry",
            idempotency_key="runtime:changeset",
        )
    )
    core.decide_approval(
        approval_id=pending.approval.id,
        approved=True,
        decided_by="user",
    )
    return RuntimeStack(
        core=core,
        runtime=runtime,
        factory=factory,
        engine=engine,
        executor=executor,
        task=task,
        project_revision=project.project.revision,
    )


def build_scratch_runtime_stack(tmp_path: Path) -> RuntimeStack:
    engine = create_sqlite_core_engine(tmp_path / "scratch-core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    workspace = FakeWorkspace(tmp_path / "managed")
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=workspace,
        registry=registry,
        policy=PolicyEngine(registry),
    )
    executor = FakeRuntimeExecutor()
    runtime = RuntimeApplication(
        unit_of_work_factory=factory,
        executor=executor,
        registry=registry,
        policy=PolicyEngine(registry),
        scope_resolver=core.scope_for_task,
    )
    conversation = core.create_conversation(
        project_id=None,
        workspace_type=WorkspaceType.CHAT_SCRATCH,
    )
    task = core.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Build a scratch preview",
            operation_mode=OperationMode.ANSWER,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="scratch-runtime:task",
        )
    )
    pending = core.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(FileMutation(path="index.html", content="<h1>Scratch Preview</h1>"),),
            reason="Create the scratch entry",
            idempotency_key="scratch-runtime:changeset",
        )
    )
    core.decide_approval(
        approval_id=pending.approval.id,
        approved=True,
        decided_by="user",
    )
    return RuntimeStack(
        core=core,
        runtime=runtime,
        factory=factory,
        engine=engine,
        executor=executor,
        task=task,
        project_revision=0,
    )
