from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.application.core import CoreApplication
from fairy_core.commanding import CommandStatus, EventVisibility
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import RiskLevel, build_default_registry
from fairy_core.contracts.models import ExecutionTarget, TaskCreate
from fairy_core.domain.errors import InvalidTransitionError, WorkerFenceError
from fairy_core.domain.models import OperationMode, ProjectResidency, WorkspaceType
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine

pytestmark = pytest.mark.integration


def test_postgres_task_and_command_idempotency_races(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_id = postgres_test_context.track_tenant(f"command-race-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    factory, application = _application(engine, tenant_id, tmp_path)
    try:
        project = application.create_project(
            name="PostgreSQL races",
            residency=ProjectResidency.SYNCED,
        )
        conversation = application.create_conversation(
            project_id=project.project.id,
            workspace_type=WorkspaceType.PROJECT_CHAT,
        )
        task_request = TaskCreate(
            conversation_id=conversation.id,
            user_request="Exercise idempotency",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.CLOUD,
            idempotency_key="postgres:task:race",
        )
        task_barrier = threading.Barrier(2)

        def create_task():
            task_barrier.wait(timeout=10)
            return application.create_task(task_request)

        with ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [
                future.result(timeout=30)
                for future in [executor.submit(create_task) for _ in range(2)]
            ]

        assert tasks[0].task.id == tasks[1].task.id
        assert tasks[0].target_version.id == tasks[1].target_version.id

        command_barrier = threading.Barrier(2)

        def create_command():
            with factory() as unit_of_work:
                command_barrier.wait(timeout=10)
                run = unit_of_work.commands.create_run(
                    command_name="project.read",
                    actor="user:integration",
                    scope=tasks[0].scope,
                    input_payload={"query": "entrypoints"},
                    risk_level=RiskLevel.LOW,
                    idempotency_key="postgres:command:race",
                )
                unit_of_work.commit()
                return run

        with ThreadPoolExecutor(max_workers=2) as executor:
            runs = [
                future.result(timeout=30)
                for future in [executor.submit(create_command) for _ in range(2)]
            ]

        assert runs[0].id == runs[1].id
        with factory() as unit_of_work:
            events = unit_of_work.commands.events_for_run(runs[0].id)
        assert [event.event_type for event in events].count("command.created") == 1
    finally:
        engine.dispose()


def test_postgres_command_sequence_claim_and_fence_are_atomic(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_id = postgres_test_context.track_tenant(f"command-fence-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    factory, application = _application(engine, tenant_id, tmp_path)
    try:
        project = application.create_project(
            name="PostgreSQL fences",
            residency=ProjectResidency.SYNCED,
        )
        conversation = application.create_conversation(
            project_id=project.project.id,
            workspace_type=WorkspaceType.PROJECT_CHAT,
        )
        task = application.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="Exercise command fencing",
                operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key="postgres:fence:task",
            )
        )
        with factory() as unit_of_work:
            run = unit_of_work.commands.create_run(
                command_name="review.test",
                actor="core:integration",
                scope=task.scope,
                input_payload={},
                risk_level=RiskLevel.LOW,
                idempotency_key="postgres:fence:command",
            )
            unit_of_work.commands.transition(run.id, CommandStatus.QUEUED)
            unit_of_work.commit()

        sequence_barrier = threading.Barrier(8)

        def append_progress(index: int):
            with factory() as unit_of_work:
                sequence_barrier.wait(timeout=10)
                event = unit_of_work.commands.append_event(
                    run_id=run.id,
                    event_type="command.progress",
                    visibility=EventVisibility.DEVELOPER,
                    message=f"Progress {index}",
                    payload={"index": index},
                )
                unit_of_work.commit()
                return event

        with ThreadPoolExecutor(max_workers=8) as executor:
            progress = [
                future.result(timeout=30)
                for future in [executor.submit(append_progress, index) for index in range(8)]
            ]
        assert len({event.task_sequence for event in progress}) == 8

        claim_barrier = threading.Barrier(2)

        def claim(worker_id: str):
            try:
                with factory() as unit_of_work:
                    claim_barrier.wait(timeout=10)
                    claimed = unit_of_work.commands.claim(
                        run.id,
                        worker_id=worker_id,
                        lease_until=datetime.now(UTC) + timedelta(seconds=2),
                    )
                    unit_of_work.commit()
                    return claimed
            except InvalidTransitionError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = [
                future.result(timeout=30)
                for future in [
                    executor.submit(claim, "worker-a"),
                    executor.submit(claim, "worker-b"),
                ]
            ]
        winners = [claim_result for claim_result in claims if claim_result is not None]
        assert len(winners) == 1
        first_claim = winners[0]

        time.sleep(2.05)
        with factory() as unit_of_work:
            reclaimed = unit_of_work.commands.claim(
                run.id,
                worker_id="worker-recovery",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
            )
            unit_of_work.commit()
        assert reclaimed.lease_fence == first_claim.lease_fence + 1

        with pytest.raises(WorkerFenceError), factory() as unit_of_work:
            unit_of_work.commands.finish(
                run.id,
                status=CommandStatus.SUCCEEDED,
                event_type="command.succeeded",
                visibility=EventVisibility.USER,
                message="Stale completion",
                payload={},
                lease_owner=first_claim.lease_owner,
                lease_fence=first_claim.lease_fence,
            )

        with factory() as unit_of_work:
            completed = unit_of_work.commands.finish(
                run.id,
                status=CommandStatus.SUCCEEDED,
                event_type="command.succeeded",
                visibility=EventVisibility.USER,
                message="Current completion",
                payload={},
                lease_owner=reclaimed.lease_owner,
                lease_fence=reclaimed.lease_fence,
            )
            unit_of_work.commit()
        assert completed.status is CommandStatus.SUCCEEDED

        with factory() as unit_of_work:
            events = [
                event
                for event in unit_of_work.commands.events_after(cursor=0)
                if event.task_id == task.task.id
            ]
        sequences = [event.task_sequence for event in events]
        assert sequences == sorted(set(sequences))
        assert [event.event_type for event in events].count("command.reclaimed") == 1
    finally:
        engine.dispose()


def _application(engine, tenant_id: str, tmp_path: Path):
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    registry = build_default_registry()
    application = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / tenant_id),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    return factory, application
