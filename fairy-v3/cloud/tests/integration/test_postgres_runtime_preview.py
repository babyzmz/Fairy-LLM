from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.application.core import CoreApplication
from fairy_core.application.runtime import PreviewStartRequest, RuntimeApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import (
    ChangesetProposal,
    ExecutionTarget,
    FileMutation,
    TaskCreate,
)
from fairy_core.domain.errors import VersionConflictError
from fairy_core.domain.execution import PreviewStatus
from fairy_core.domain.models import OperationMode, ProjectResidency, WorkspaceType
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.runtime.models import (
    ExecutorRuntimeState,
    RuntimeExecutorHealth,
    RuntimeProbeResult,
    RuntimeStartResult,
    RuntimeStopResult,
    StaticRuntimeStart,
)
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


class CrashOnceRuntimeExecutor:
    def __init__(self) -> None:
        self.crash_after_start = True
        self.start_calls: list[StaticRuntimeStart] = []
        self.probes: dict[str, RuntimeProbeResult] = {}

    def health(self) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=True,
            executor="postgres_runtime_fixture",
            version="1",
            error_code=None,
            diagnostics=(),
        )

    def start_static(self, request: StaticRuntimeStart) -> RuntimeStartResult:
        self.start_calls.append(request)
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
        return self.probes[executor_handle]

    def stop(self, executor_handle: str) -> RuntimeStopResult:
        current = self.probes[executor_handle]
        self.probes[executor_handle] = RuntimeProbeResult(
            executor_handle=executor_handle,
            state=ExecutorRuntimeState.STOPPED,
            host=current.host,
            port=current.port,
            url=current.url,
        )
        return RuntimeStopResult(stopped=True)


def test_postgres_runtime_recovery_revision_uniqueness_and_outbox(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_id = postgres_test_context.track_tenant(f"runtime-preview-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    registry = build_default_registry()
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / tenant_id),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    executor = CrashOnceRuntimeExecutor()
    runtime = RuntimeApplication(
        unit_of_work_factory=factory,
        executor=executor,
        registry=registry,
        policy=PolicyEngine(registry),
        scope_resolver=core.scope_for_task,
    )
    try:
        project = core.create_project(
            name="PostgreSQL Runtime",
            residency=ProjectResidency.SYNCED,
        )
        conversation = core.create_conversation(
            project_id=project.project.id,
            workspace_type=WorkspaceType.PROJECT_CHAT,
        )
        task = core.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="Recover a durable Preview",
                operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
                execution_target=ExecutionTarget.LOCAL,
                idempotency_key="postgres:runtime:task",
            )
        )
        pending = core.propose_changeset(
            ChangesetProposal(
                task_id=task.task.id,
                files=(FileMutation(path="index.html", content="<h1>Preview</h1>"),),
                reason="Create static entry",
                idempotency_key="postgres:runtime:changeset",
            )
        )
        core.decide_approval(
            approval_id=pending.approval.id,
            approved=True,
            decided_by="integration-user",
        )

        request = PreviewStartRequest(
            task_id=task.task.id,
            idempotency_key="postgres:runtime:preview",
        )
        with pytest.raises(SystemExit):
            runtime.start_preview(request)
        executor.crash_after_start = False

        recovered = runtime.recover_interrupted()

        assert recovered[-1].status is PreviewStatus.READY
        assert len(executor.start_calls) == 1
        with factory() as unit_of_work:
            stored_runtime = unit_of_work.state.runtimes_for_task(task.task.id)[-1]
            stored_preview = unit_of_work.state.preview_for_task(task.task.id)
        assert stored_preview is not None

        _assert_preview_event_and_outbox_are_atomic(engine, tenant_id, stored_preview.id)
        _assert_postgres_partial_preview_uniqueness(
            engine,
            tenant_id=tenant_id,
            runtime_id=stored_runtime.id,
            project_id=project.project.id,
            conversation_id=conversation.id,
            task_id=task.task.id,
            version_id=task.target_version.id,
            project_root=task.target_version.project_root,
        )
        _assert_runtime_revision_fence(factory, stored_runtime.id)
    finally:
        engine.dispose()


def _assert_preview_event_and_outbox_are_atomic(engine, tenant_id: str, preview_id) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        rows = connection.execute(
            text(
                """
                SELECT events.event_id, outbox.event_id
                FROM domain_events AS events
                JOIN outbox
                  ON outbox.tenant_id = events.tenant_id
                 AND outbox.event_id = events.event_id
                WHERE events.tenant_id = :tenant_id
                  AND events.event_type = 'preview.ready'
                  AND events.payload ->> 'preview_id' = :preview_id
                """
            ),
            {"tenant_id": tenant_id, "preview_id": str(preview_id)},
        ).all()
    assert len(rows) == 1
    assert rows[0][0] == rows[0][1]


def _assert_postgres_partial_preview_uniqueness(
    engine,
    *,
    tenant_id: str,
    runtime_id,
    project_id,
    conversation_id,
    task_id,
    version_id,
    project_root: Path,
) -> None:
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO core_preview_sessions (
                    tenant_id, id, project_id, conversation_id, task_id, version_id,
                    runtime_id, project_root, execution_target, visibility, status,
                    health, idempotency_key, revision, created_at, updated_at
                ) VALUES (
                    :tenant_id, :preview_id, :project_id, :conversation_id, :task_id,
                    :version_id, :runtime_id, :project_root, 'local', 'chat_draft',
                    'starting', 'starting', 'postgres:runtime:duplicate', 1, now(), now()
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "preview_id": str(uuid4()),
                "project_id": str(project_id),
                "conversation_id": str(conversation_id),
                "task_id": str(task_id),
                "version_id": str(version_id),
                "runtime_id": str(runtime_id),
                "project_root": str(project_root),
            },
        )

    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        count = connection.execute(
            text(
                "SELECT count(*) FROM core_preview_sessions "
                "WHERE tenant_id = :tenant_id AND task_id = :task_id "
                "AND status IN ('created', 'starting', 'ready', 'stopping')"
            ),
            {"tenant_id": tenant_id, "task_id": str(task_id)},
        ).scalar_one()
    assert count == 1


def _assert_runtime_revision_fence(factory, runtime_id) -> None:
    with factory() as unit_of_work:
        stale = unit_of_work.state.get_runtime(runtime_id)
    assert stale is not None
    expected_revision = stale.revision

    with factory() as unit_of_work:
        current = unit_of_work.state.get_runtime(runtime_id)
        assert current is not None
        current.begin_stop()
        unit_of_work.state.save_runtime(current, expected_revision=expected_revision)
        unit_of_work.commit()

    stale.begin_stop()
    with pytest.raises(VersionConflictError), factory() as unit_of_work:
        unit_of_work.state.save_runtime(stale, expected_revision=expected_revision)
