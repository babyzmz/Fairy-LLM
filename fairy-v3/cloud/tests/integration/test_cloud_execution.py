from __future__ import annotations

import io
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.application.core import CoreApplication
from fairy_core.commanding import CommandStatus
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import RiskLevel, build_default_registry
from fairy_core.contracts.models import ExecutionTarget, TaskCreate
from fairy_core.domain.models import OperationMode, ProjectResidency, WorkspaceType
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.sandbox.models import SandboxNetworkPolicy, SandboxRequest
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from fairy_cloud.execution.executor import CloudSandboxExecutor
from fairy_cloud.execution.repository import ExecutionJobRepository

pytestmark = pytest.mark.integration


def test_attested_cloud_worker_executes_one_idempotent_fenced_job(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_id = postgres_test_context.track_tenant(f"execution-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    registry = build_default_registry()
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    application = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / tenant_id),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    try:
        project = application.create_project(
            name="Cloud execution",
            residency=ProjectResidency.SYNCED,
        )
        conversation = application.create_conversation(
            project_id=project.project.id,
            workspace_type=WorkspaceType.PROJECT_CHAT,
        )
        task = application.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="Execute in the cloud Sandbox",
                operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key="cloud-execution:task",
            )
        )
        with factory() as unit_of_work:
            run = unit_of_work.commands.create_run(
                command_name="run.sandboxed",
                actor="core:integration",
                scope=task.scope,
                input_payload={"argv": ["python3", "-c", "print('cloud-ok')"]},
                risk_level=RiskLevel.HIGH,
                idempotency_key="cloud-execution:command",
            )
            unit_of_work.commands.transition(run.id, CommandStatus.QUEUED)
            claim = unit_of_work.commands.claim(
                run.id,
                worker_id="core:integration",
                lease_until=datetime.now(UTC) + timedelta(seconds=60),
            )
            unit_of_work.commit()

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("probe.txt", "cloud workspace\n")
        request = SandboxRequest.create(
            job_id=run.id,
            project_id=task.scope.project_id,
            conversation_id=task.scope.conversation_id,
            task_id=task.scope.task_id,
            version_id=task.scope.target_version_id,
            scope_digest=task.scope.scope_digest,
            workspace_generation=1,
            lease_fence=claim.lease_fence,
            argv=(
                "python3",
                "-c",
                "from pathlib import Path; print(Path('probe.txt').read_text().strip())",
            ),
            cwd=".",
            environment={"CI": "1"},
            timeout_seconds=30,
            output_limit_bytes=65_536,
            network_policy=SandboxNetworkPolicy.NONE,
            workspace_archive=archive.getvalue(),
        )
        store = ExecutionJobRepository(engine, tenant_id=tenant_id)
        executor = CloudSandboxExecutor(store=store)

        first = executor.execute(request)
        replay = executor.execute(request)

        assert first == replay
        assert first.executor == "cloud_oci_worker"
        assert first.stdout == b"cloud workspace\n"
        persisted = store.get(request.job_id)
        assert persisted is not None
        assert persisted.attempts == 1
        assert ExecutionJobRepository(engine, tenant_id="another-tenant").get(run.id) is None
    finally:
        engine.dispose()


def test_execution_database_role_has_only_execution_table_access() -> None:
    dsn = os.environ.get("FAIRY_TEST_EXECUTION_SYNC_POSTGRES_DSN")
    if dsn is None:
        pytest.skip("FAIRY_TEST_EXECUTION_SYNC_POSTGRES_DSN is not set")
    engine = create_engine(dsn, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            privileges = connection.execute(
                text(
                    "SELECT "
                    "has_table_privilege(current_user, 'public.execution_jobs', 'SELECT'), "
                    "has_table_privilege(current_user, 'public.execution_workers', 'UPDATE'), "
                    "has_table_privilege(current_user, 'public.outbox', 'SELECT'), "
                    "has_table_privilege(current_user, 'public.core_projects', 'SELECT')"
                )
            ).one()
        assert tuple(privileges) == (True, True, False, False)

        for forbidden_table in ("outbox", "core_projects"):
            with pytest.raises(ProgrammingError), engine.connect() as connection:
                connection.execute(text(f"SELECT * FROM {forbidden_table} LIMIT 1"))
    finally:
        engine.dispose()
