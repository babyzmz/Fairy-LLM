from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.application.core import CoreApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import (
    ExecutionTarget,
    MemoryClaimGetInput,
    MemoryClaimPromoteInput,
    MemoryClaimSupersedeInput,
    MemoryObserveInput,
    TaskCreate,
)
from fairy_core.domain.errors import MemoryConflictError
from fairy_core.domain.models import OperationMode, ProjectResidency, WorkspaceType
from fairy_core.memory.application import MemoryApplication
from fairy_core.memory.policy import MemoryPolicy
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine

pytestmark = pytest.mark.integration


def test_postgres_memory_replay_and_revision_cas_are_atomic(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_id = postgres_test_context.track_tenant(f"memory-race-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    registry = build_default_registry()
    policy = PolicyEngine(registry)
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / tenant_id),
        registry=registry,
        policy=policy,
    )
    memory = MemoryApplication(
        unit_of_work_factory=factory,
        registry=registry,
        command_policy=policy,
        memory_policy=MemoryPolicy(),
        scope_resolver=core.scope_for_task,
    )
    try:
        project = core.create_project(
            name="PostgreSQL memory races",
            residency=ProjectResidency.SYNCED,
        )
        conversation = core.create_conversation(
            project_id=project.project.id,
            workspace_type=WorkspaceType.PROJECT_CHAT,
        )
        task = core.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="Remember the UI framework",
                operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key="postgres:memory:task",
            )
        )
        observe_request = MemoryObserveInput(
            task_id=task.task.id,
            content="The project uses React Aria.",
            idempotency_key="postgres:memory:observe",
        )
        observe_barrier = threading.Barrier(2)

        def observe():
            observe_barrier.wait(timeout=10)
            return memory.observe(observe_request)

        with ThreadPoolExecutor(max_workers=2) as executor:
            observations = [
                future.result(timeout=30) for future in [executor.submit(observe) for _ in range(2)]
            ]
        assert observations[0].id == observations[1].id

        promoted = memory.promote_claim(
            MemoryClaimPromoteInput(
                task_id=task.task.id,
                observation_id=observations[0].id,
                subject="project",
                predicate="accessibility_framework",
                value="React Aria",
                normalized_text="react aria",
                user_confirmed=True,
                idempotency_key="postgres:memory:promote",
            )
        )
        supersede_requests = (
            MemoryClaimSupersedeInput(
                task_id=task.task.id,
                claim_id=promoted.claim.id,
                expected_revision=1,
                source_observation_ids=(observations[0].id,),
                value="React Aria 4.0",
                normalized_text="react aria 4.0",
                user_confirmed=True,
                idempotency_key="postgres:memory:supersede:a",
            ),
            MemoryClaimSupersedeInput(
                task_id=task.task.id,
                claim_id=promoted.claim.id,
                expected_revision=1,
                source_observation_ids=(observations[0].id,),
                value="React Aria 4.1",
                normalized_text="react aria 4.1",
                user_confirmed=True,
                idempotency_key="postgres:memory:supersede:b",
            ),
        )
        supersede_barrier = threading.Barrier(2)

        def supersede(request: MemoryClaimSupersedeInput):
            supersede_barrier.wait(timeout=10)
            try:
                return memory.supersede_claim(request)
            except MemoryConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = [
                future.result(timeout=30)
                for future in [
                    executor.submit(supersede, supersede_requests[0]),
                    executor.submit(supersede, supersede_requests[1]),
                ]
            ]
        assert len([outcome for outcome in outcomes if outcome is not None]) == 1

        current = memory.get_claim(
            MemoryClaimGetInput(task_id=task.task.id, claim_id=promoted.claim.id)
        )
        assert current.current_revision.revision == 2
        assert current.current_revision.value in {"React Aria 4.0", "React Aria 4.1"}
        with factory() as unit_of_work:
            events = unit_of_work.commands.events_after(cursor=0)
        event_types = [event.event_type for event in events]
        assert event_types.count("memory.observation.accepted") == 1
        assert event_types.count("memory.claim.superseded") == 1
        sequences = [event.task_sequence for event in events if event.task_id == task.task.id]
        assert sequences == sorted(set(sequences))
    finally:
        engine.dispose()
